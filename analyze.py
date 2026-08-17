"""
analyze.py — single pass over every draft_N/*_detailed.log, producing:
  - analysis.json     : full structured data per draft (standings, blocking
                         breakdown, per-game duration/loss reasons, per-draft
                         card performance, anomalies)
  - cards.csv         : corpus-wide card performance (games/wins/winrate,
                         distinct_drafts, reliable) across every draft analyzed

This replaces two previously separate scripts (analyze_all_drafts.py +
generate_retex_data.py) that duplicated the same log-parsing logic and had to
be run in a specific order (one produced a CSV the other silently depended
on). One script, one pass, one ordering to remember: just run this.

Nothing here is specific to any particular cube, mechanic, or card. The only
cube-specific outputs that used to live in this script (mono_vs_shadow.csv,
shadow_seats_detail.csv, shadow_vs_blocking.csv) have been removed — every
seat's `colors` and full card list are in analysis.json, so any cross-tab like
that is a short script against the JSON, not something the core pipeline
needs to know about in advance. See examples/ for a template.
"""
import os
import re
import glob
import csv
import json
import zipfile
from collections import defaultdict

from config import FORGE_DIR, DECKS_BASE_DIR

BASICS = {"Island", "Mountain", "Swamp", "Forest", "Plains"}
COLOR_LETTERS = set("WUBRG")

# A card needs to have been drafted into at least this many DISTINCT drafts
# before its winrate is treated as an independent signal. In a singleton
# cube, a card that only ever appeared in one deck mechanically inherits that
# deck's overall record — see README.md, "Single-deck card bias".
MIN_DRAFTS_FOR_CARD_STATS = 2

# ==============================================================================
# Win detection: anchored to "Round N - p1[x] vs p2[y]" (identifies the pair)
# + "Match Winner - X!" (closes the match). Verified exactly 1x/1x per pair
# with no duplicates or omissions across a 2,800-match / 100-draft corpus.
# Other candidate lines (a single "Match Result:" line, or "Game Outcome: ...
# has won") each failed at that scale: a genuine simultaneous double-loss
# makes Forge replay a game (two "Match Result:" lines for one pair), and an
# AI turn-timeout (120,000ms) can emit two contradictory "has won" lines for
# the same game. See README.md for the full story.
# ==============================================================================
ROUND_RE = re.compile(r"^Round \d+ - (\S+)\[\d+\] vs (\S+)\[\d+\]")
MATCH_WINNER_RE = re.compile(r"^Match Winner - (\S+)!")
GAME_TURN_RE = re.compile(r"Game Outcome: Turn (\d+)")
GAME_LOST_RE = re.compile(r"Game Outcome: (\S+) has lost(?: (.*))?$")
GAME_WON_RE = re.compile(r"Game Outcome: (\S+) has won")


# ==============================================================================
# Evasion keywords, loaded dynamically from Forge's own card database rather
# than a hand-maintained list, so this works unmodified on any cube's card
# pool. Scope note: only Flying/Reach/Shadow are modeled (the pipeline's
# legal-blocking-rate calculation needs to know exactly how each keyword
# interacts with blocking eligibility, and getting that subtly wrong for e.g.
# Menace or Intimidate is worse than not modeling it). A card with an evasion
# keyword this pipeline doesn't know about is simply treated as normal-blocked
# for rate-calculation purposes.
# ==============================================================================
def load_forge_keywords(forge_dir):
    flying, reach, shadow = set(), set(), set()
    res_dir = os.path.join(forge_dir, "res")
    if not os.path.exists(res_dir):
        print(f"⚠️  {res_dir} not found — evasion keywords unavailable "
              f"(blocking-rate calculation will treat all attackers as normal).")
        return flying, reach, shadow

    def parse_card_content(lines):
        card_name = None
        is_fly, is_reach, is_shd = False, False, False
        for line in lines:
            line = line.strip()
            if line.startswith("Name:"):
                card_name = line.split("Name:", 1)[1].strip()
            elif line.startswith("K:") or line.startswith("Oracle:"):
                kw_line = line.lower()
                if re.search(r"\bflying\b", kw_line): is_fly = True
                if re.search(r"\breach\b", kw_line): is_reach = True
                if re.search(r"\bshadow\b", kw_line): is_shd = True
        return card_name, is_fly, is_reach, is_shd

    for root, _, files in os.walk(res_dir):
        for file in files:
            filepath = os.path.join(root, file)
            if file.endswith(".zip") and "cards" in file.lower():
                try:
                    with zipfile.ZipFile(filepath, "r") as z:
                        for zname in z.namelist():
                            if zname.endswith(".txt"):
                                with z.open(zname) as f:
                                    lines = [l.decode("utf-8", errors="replace") for l in f.readlines()]
                                    cname, fly, rch, shd = parse_card_content(lines)
                                    if cname:
                                        if fly: flying.add(cname)
                                        if rch: reach.add(cname)
                                        if shd: shadow.add(cname)
                except Exception:
                    continue
            elif file.endswith(".txt"):
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        cname, fly, rch, shd = parse_card_content(f.readlines())
                        if cname:
                            if fly: flying.add(cname)
                            if rch: reach.add(cname)
                            if shd: shadow.add(cname)
                except Exception:
                    continue
    return flying, reach, shadow


def load_deck_details(draft_dir):
    """owner: {card_name: seat_id}, seat_decks: {seat_id: {card_name, ...}}"""
    owner = {}
    seat_decks = defaultdict(set)
    for path in glob.glob(os.path.join(draft_dir, "*.dck")):
        seat = os.path.splitext(os.path.basename(path))[0]
        in_main = False
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line == "[Main]":
                    in_main = True
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_main = False
                    continue
                if in_main and line and not line.startswith("#"):
                    parts = line.split(" ", 1)
                    name = parts[1] if len(parts) == 2 and parts[0].isdigit() else line
                    if name not in BASICS:
                        owner[name] = seat
                        seat_decks[seat].add(name)
    return owner, seat_decks


def parse_archetype(seat):
    """'D4_S3_W_Weenie' -> ('W_Weenie', {'W'}). Colors come from the leading
    color-letter prefix of the archetype label (as produced by convert_csv.py
    from CubeCobra's own archetype naming) — if a deck's archetype label
    doesn't resolve to a recognizable color prefix (e.g. a generic 'Combo'
    label), colors is an empty set and it's reported separately downstream
    rather than forced into mono/multi."""
    m = re.match(r"^D\d+_S\d+_(.+)$", seat)
    archetype = m.group(1) if m else seat
    prefix = archetype.split("_", 1)[0]
    colors = set(prefix) & COLOR_LETTERS if set(prefix) <= COLOR_LETTERS else set()
    return archetype, colors


def analyze_draft(draft_dir, log_path, FLYING, REACH, SHADOW):
    draft_name = os.path.basename(draft_dir)
    owner, seat_decks = load_deck_details(draft_dir)
    lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()

    # ---------------- Legal blocking rate ----------------
    # Board state is reconstructed from creature-resolution / graveyard events
    # rather than trusting a raw "didn't block" count, so an empty board and a
    # legally-unblockable attacker aren't miscounted as a missed block. See
    # README.md, "Legal vs. naive blocking rate".
    board = defaultdict(set)
    d_blocking = {"empty_board": 0, "evasion_legal": 0, "real_nonblock": 0, "real_block": 0}
    for line in lines:
        m_turn = re.search(r"Turn: Turn (\d+) \(", line)
        if m_turn:
            if m_turn.group(1) == "1":
                board = defaultdict(set)
            continue
        m = re.match(r"Resolve Stack: (.+?) - Creature", line)
        if m:
            name = m.group(1)
            if name in owner:
                board[owner[name]].add(name)
            continue
        m = re.match(r"Zone Change: (.+?) \(\d+\) was put into (Graveyard|Exile) from Battlefield\.", line)
        if m:
            name = m.group(1)
            if name in owner and name in board[owner[name]]:
                board[owner[name]].discard(name)
            continue
        m = re.match(r"(?:Combat: )?(\S+) assigned .* to block", line)
        if m:
            d_blocking["real_block"] += 1
            continue
        m = re.match(r"(?:Combat: )?(\S+) didn't block (.+?) \(\d+\)\.", line)
        if m:
            defender, attacker = m.group(1), m.group(2)
            defboard = board[defender]
            if len(defboard) == 0:
                d_blocking["empty_board"] += 1
            elif attacker in SHADOW:
                d_blocking["evasion_legal"] += 1
            elif attacker in FLYING:
                d_blocking["real_nonblock" if defboard & (FLYING | REACH) else "evasion_legal"] += 1
            else:
                d_blocking["real_nonblock"] += 1

    denom = d_blocking["real_block"] + d_blocking["real_nonblock"]
    total_events = sum(d_blocking.values())
    blocking = dict(d_blocking)
    blocking["total_events"] = total_events
    blocking["block_rate_pct"] = round((d_blocking["real_block"] / denom * 100) if denom else 0.0, 2)
    for k in ("real_block", "real_nonblock", "evasion_legal", "empty_board"):
        blocking[f"pct_{k}"] = round(d_blocking[k] / total_events * 100, 1) if total_events else 0.0

    # ---------------- Match results + per-game duration/loss reason ----------------
    # A pair can produce more than one game within a single best-of-one
    # "match": a genuine simultaneous double-loss (e.g. a symmetric board
    # wipe) makes Forge replay a game, and an AI turn-timeout can emit two
    # contradictory "has won" lines for one game. Both are handled explicitly
    # below rather than silently mis-counted. "Match Winner -" is the only
    # event that actually closes a match and is the sole credit point for
    # seat_matches / seat_wins / card_stats_draft.
    current_pair = None
    pending_games = []
    cur_game = None
    seat_matches = defaultdict(int)
    seat_wins = defaultdict(int)
    card_stats_draft = defaultdict(lambda: {"matches": 0, "wins": 0})
    seat_duration = defaultdict(lambda: {
        "turns": [], "losses_pv": 0, "losses_deckout": 0, "losses_other": 0, "draws": 0
    })
    anomalies = []

    for line in lines:
        m_round = ROUND_RE.match(line)
        if m_round:
            current_pair = (m_round.group(1), m_round.group(2))
            pending_games = []
            cur_game = None
            continue

        m_gt = GAME_TURN_RE.match(line)
        if m_gt:
            cur_game = {"turn": int(m_gt.group(1)), "status": {}, "reason": {}}
            pending_games.append(cur_game)
            continue

        m_lost = GAME_LOST_RE.match(line)
        if m_lost and cur_game is not None and current_pair and m_lost.group(1) in current_pair:
            cur_game["status"][m_lost.group(1)] = "lost"
            cur_game["reason"][m_lost.group(1)] = m_lost.group(2) or ""
            continue

        m_won = GAME_WON_RE.match(line)
        if m_won and cur_game is not None and current_pair and m_won.group(1) in current_pair:
            cur_game["status"].setdefault(m_won.group(1), "won")
            continue

        m_mw = MATCH_WINNER_RE.match(line)
        if m_mw and current_pair:
            winner = m_mw.group(1)
            seat_a, seat_b = current_pair
            if winner in current_pair and winner in seat_decks:
                loser = seat_a if winner == seat_b else seat_b
                if loser in seat_decks:
                    seat_matches[winner] += 1
                    seat_matches[loser] += 1
                    seat_wins[winner] += 1
                    for seat, is_winner in ((winner, True), (loser, False)):
                        for card in seat_decks[seat]:
                            card_stats_draft[card]["matches"] += 1
                            if is_winner:
                                card_stats_draft[card]["wins"] += 1

                    for i, g in enumerate(pending_games):
                        turn = g["turn"]
                        wa, wb = g["status"].get(seat_a), g["status"].get(seat_b)
                        is_last = (i == len(pending_games) - 1)
                        if wa == "lost" and wb != "lost":
                            g_loser = seat_a
                        elif wb == "lost" and wa != "lost":
                            g_loser = seat_b
                        elif wa == "lost" and wb == "lost":
                            g_loser = None
                            anomalies.append(
                                f"Game {seat_a} vs {seat_b} (Turn {turn}): genuine draw (both players "
                                f"lost simultaneously) — Forge replays an extra game to decide the match."
                            )
                        elif wa == "won" and wb == "won":
                            if is_last:
                                g_loser = loser
                                anomalies.append(
                                    f"Game {seat_a} vs {seat_b} (Turn {turn}): Forge's log emits two "
                                    f"contradictory 'has won' lines — likely AI turn-timeout (120,000ms). "
                                    f"Winner resolved via 'Match Winner -': {winner}."
                                )
                            else:
                                g_loser = None
                                anomalies.append(
                                    f"Game {seat_a} vs {seat_b} (Turn {turn}): outcome undetermined "
                                    f"(double 'has won', non-deciding game of the match)."
                                )
                        else:
                            g_loser = None
                            anomalies.append(
                                f"Game {seat_a} vs {seat_b} (Turn {turn}): no clear end-of-game status "
                                f"found in the log."
                            )

                        seat_duration[seat_a]["turns"].append(turn)
                        seat_duration[seat_b]["turns"].append(turn)
                        if g_loser is None:
                            seat_duration[seat_a]["draws"] += 1
                            seat_duration[seat_b]["draws"] += 1
                        else:
                            reason = g["reason"].get(g_loser, "")
                            if "empty library" in reason or "draw cards" in reason:
                                seat_duration[g_loser]["losses_deckout"] += 1
                            elif "life total" in reason:
                                seat_duration[g_loser]["losses_pv"] += 1
                            else:
                                seat_duration[g_loser]["losses_other"] += 1
            current_pair = None
            pending_games = []
            cur_game = None
            continue

    ranking = sorted(seat_decks.keys(), key=lambda s: seat_wins[s], reverse=True)
    standings = []
    for i, seat in enumerate(ranking, 1):
        archetype, colors = parse_archetype(seat)
        m_, w_ = seat_matches[seat], seat_wins[seat]
        standings.append({
            "rank": i, "seat": seat, "archetype": archetype, "colors": sorted(colors),
            "matches": m_, "wins": w_, "losses": m_ - w_,
            "winrate_pct": round(w_ / m_ * 100, 2) if m_ else 0.0,
            "cards": sorted(seat_decks[seat]),
        })

    duration = {}
    for seat in seat_decks:
        d = seat_duration[seat]
        turns = d["turns"]
        duration[seat] = {
            "n_games": len(turns),
            "avg_turns": round(sum(turns) / len(turns), 1) if turns else None,
            "min_turns": min(turns) if turns else None,
            "max_turns": max(turns) if turns else None,
            "losses_pv": d["losses_pv"],
            "losses_deckout": d["losses_deckout"],
            "losses_other": d["losses_other"],
            "draws": d["draws"],
        }

    # ---------------- This draft's own top cards (winrate within this draft only) ----------------
    draft_top_cards = []
    for card, s in card_stats_draft.items():
        if s["matches"] == 0:
            continue
        draft_top_cards.append({
            "card_name": card, "matches": s["matches"], "wins": s["wins"],
            "winrate_pct": round(s["wins"] / s["matches"] * 100, 2),
        })
    draft_top_cards.sort(key=lambda r: r["winrate_pct"], reverse=True)

    # ---------------- Local signals (compared against the rest of THIS draft only) ----------------
    winner = standings[0]
    others_avg = [duration[s["seat"]]["avg_turns"] for s in standings[1:] if duration[s["seat"]]["avg_turns"]]
    field_avg_turns = round(sum(others_avg) / len(others_avg), 1) if others_avg else None
    winner_avg_turns = duration[winner["seat"]]["avg_turns"]

    contest = sum(
        1 for row in standings
        if row["seat"] != winner["seat"] and winner["colors"] and set(row["colors"]) & set(winner["colors"])
    )

    signals = {
        "winner_seat": winner["seat"],
        "winner_archetype": winner["archetype"],
        "winner_colors": winner["colors"],
        "winner_wins": winner["wins"],
        "winner_matches": winner["matches"],
        "winner_avg_turns": winner_avg_turns,
        "field_avg_turns_excl_winner": field_avg_turns,
        "faster_than_field": (winner_avg_turns is not None and field_avg_turns is not None and winner_avg_turns < field_avg_turns),
        "decks_sharing_winner_color": contest,
        "winner_color_uncontested": (contest == 0 and bool(winner["colors"])),
    }

    return {
        "draft": draft_name,
        "n_decks": len(seat_decks),
        "standings": standings,
        "blocking": blocking,
        "duration": duration,
        "draft_top_cards": draft_top_cards[:15],
        "anomalies": anomalies,
        "signals": signals,
    }


def main():
    FLYING, REACH, SHADOW = load_forge_keywords(FORGE_DIR)
    print(f"✅ Forge card database loaded: {len(FLYING)} flying, {len(REACH)} reach, {len(SHADOW)} shadow.")

    log_files = sorted(
        glob.glob(os.path.join(DECKS_BASE_DIR, "draft_*", "*_detailed.log")),
        key=lambda p: int(re.search(r"draft_(\d+)", p).group(1)),
    )
    if not log_files:
        print(f"❌ No draft_*/*.log found under {DECKS_BASE_DIR}. Run simulate.py first.")
        return

    results = {}
    card_stats_corpus = defaultdict(lambda: {"played": 0, "wins": 0, "drafts": set()})
    total_anomalies = 0

    for log_path in log_files:
        draft_dir = os.path.dirname(log_path)
        data = analyze_draft(draft_dir, log_path, FLYING, REACH, SHADOW)
        results[data["draft"]] = data
        total_anomalies += len(data["anomalies"])
        print(f"✓ {data['draft']} analyzed ({data['n_decks']} decks, {len(data['anomalies'])} anomaly(ies))")

    # Corpus-wide card performance: derived from each seat's match/win count
    # (same source of truth as the per-draft numbers, computed once here
    # rather than re-parsing logs a second time).
    for data in results.values():
        draft = data["draft"]
        for row in data["standings"]:
            for card in row["cards"]:
                s = card_stats_corpus[card]
                s["played"] += row["matches"]
                s["wins"] += row["wins"]
                s["drafts"].add(draft)

    card_perf_rows = []
    for card, s in card_stats_corpus.items():
        played = s["played"]
        card_perf_rows.append({
            "card_name": card,
            "games_played": played,
            "wins": s["wins"],
            "winrate_pct": round(s["wins"] / played * 100, 2) if played else 0.0,
            "distinct_drafts": len(s["drafts"]),
            "reliable": len(s["drafts"]) >= MIN_DRAFTS_FOR_CARD_STATS,
        })
    card_perf_rows.sort(key=lambda r: r["winrate_pct"], reverse=True)

    # Backfill each draft's top-cards with corpus-wide reliability, now that
    # we have it (single pass, no re-reading logs).
    reliability = {r["card_name"]: r for r in card_perf_rows}
    for data in results.values():
        for c in data["draft_top_cards"]:
            g = reliability.get(c["card_name"], {})
            c["global_distinct_drafts"] = g.get("distinct_drafts")
            c["global_reliable"] = g.get("reliable")

    analysis_path = os.path.join(DECKS_BASE_DIR, "analysis.json")
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    cards_path = os.path.join(DECKS_BASE_DIR, "cards.csv")
    with open(cards_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["card_name", "games_played", "wins", "winrate_pct", "distinct_drafts", "reliable"])
        writer.writeheader()
        writer.writerows(card_perf_rows)

    g_block = sum(d["blocking"]["real_block"] for d in results.values())
    g_nonblock = sum(d["blocking"]["real_nonblock"] for d in results.values())
    g_denom = g_block + g_nonblock
    g_rate = (g_block / g_denom * 100) if g_denom else 0.0
    unreliable = sum(1 for r in card_perf_rows if not r["reliable"])

    print("\n" + "=" * 70)
    print(f" {len(results)} drafts analyzed, {len(card_perf_rows)} unique cards.")
    print(f" Legal blocking rate (all drafts pooled): {g_block}/{g_denom} = {g_rate:.1f}%")
    print(f" {unreliable} card(s) seen in < {MIN_DRAFTS_FOR_CARD_STATS} distinct draft(s) (winrate_pct unreliable — see 'reliable' column).")
    print(f" {total_anomalies} log anomaly(ies) across the corpus.")
    print("-" * 70)
    print(" TOP 10 CARDS BY WINRATE:")
    for i, row in enumerate(card_perf_rows[:10], 1):
        flag = "" if row["reliable"] else "  ⚠️  1 draft only"
        print(f"   {i}. {row['card_name']:<28} {row['winrate_pct']:>6}%  ({row['wins']}/{row['games_played']}){flag}")
    print("-" * 70)
    print(f" 📂 {analysis_path}")
    print(f" 📂 {cards_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
