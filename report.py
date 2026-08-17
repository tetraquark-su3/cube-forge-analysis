"""
report.py — reads analysis.json + cards.csv (produced by analyze.py) and
writes:
  - draft_N/REPORT.md   : one human-readable report per draft
  - SUMMARY.md           : corpus-wide comparison across every draft analyzed

Nothing here assumes any particular cube, mechanic, or card — it reports on
whatever archetypes/colors/cards actually show up in your data. If you want to
track a specific card or mechanic (a keyword ability, a color pair, a new
addition you're testing), see examples/ for a short template rather than
editing this file.
"""
import os
import csv
import json
import math
from collections import defaultdict

from config import DECKS_BASE_DIR

MAX_PEERS_SHOWN = 5


def draft_num(name):
    return int(name.split("_")[1])


def mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def pearson_corr(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def fmt_turns(v):
    return f"{v}" if v is not None else "—"


# ==============================================================================
# Corpus-wide signals, computed once after all drafts are loaded — complement
# the per-draft "signals" block already in analysis.json (computed against the
# rest of that draft's own table only).
# ==============================================================================
def compute_corpus_signals(all_data):
    n = len(all_data)
    by_block = sorted(all_data.items(), key=lambda kv: kv[1]["blocking"]["block_rate_pct"])
    block_rank = {}
    for i, (draft, _) in enumerate(by_block, 1):
        pct = round((i - 1) / (n - 1) * 100, 1) if n > 1 else 50.0
        block_rank[draft] = {"rank_asc": i, "percentile_low_to_high": pct}
    return {"n_drafts": n, "block_rank": block_rank}


# ==============================================================================
# Per-draft report
# ==============================================================================
def render_standings(data):
    has_draws = any(data["duration"][s["seat"]]["draws"] for s in data["standings"])
    header = "| Rank | Deck | Archetype | W-L | Winrate | Avg turns | PV losses | Deckout losses | Other losses |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    if has_draws:
        header += " Draws |"
        sep += "---|"
    lines = [header, sep]
    for row in data["standings"]:
        seat = row["seat"]
        dur = data["duration"][seat]
        line = (
            f"| {row['rank']} | {seat} | {row['archetype']} | {row['wins']}-{row['losses']} | "
            f"{row['winrate_pct']}% | {fmt_turns(dur['avg_turns'])} "
            f"(min {fmt_turns(dur['min_turns'])} / max {fmt_turns(dur['max_turns'])}) | "
            f"{dur['losses_pv']} | {dur['losses_deckout']} | {dur['losses_other']} |"
        )
        if has_draws:
            line += f" {dur['draws']} |"
        lines.append(line)
    table = "\n".join(lines)
    if has_draws:
        table += (
            "\n\n*At least one pairing in this draft had a genuine draw (simultaneous double-loss) — "
            "Forge replays an extra game to decide the match, so total games can exceed `matches` for "
            "the seats involved (see Anomalies).*"
        )
    return table


def render_blocking(data):
    b = data["blocking"]
    lines = [
        "| Category | Occurrences | % of total |",
        "|---|---|---|",
        f"| `real_block` (legal block made) | {b['real_block']} | {b['pct_real_block']}% |",
        f"| `real_nonblock` (avoidable non-block) | {b['real_nonblock']} | {b['pct_real_nonblock']}% |",
        f"| `evasion_legal` (no legal blocker existed) | {b['evasion_legal']} | {b['pct_evasion_legal']}% |",
        f"| `empty_board` (defender had no creatures) | {b['empty_board']} | {b['pct_empty_board']}% |",
        f"| **Total attack occurrences** | **{b['total_events']}** | 100% |",
    ]
    return "\n".join(lines) + (
        f"\n\n**Legal blocking rate = real_block / (real_block + real_nonblock) "
        f"= {b['real_block']}/{b['real_block'] + b['real_nonblock']} = {b['block_rate_pct']}%**"
    )


def render_top_cards(data):
    lines = ["| Card | Winrate (this draft) | Matches | Corpus reliability |", "|---|---|---|---|"]
    any_warning = False
    for c in data["draft_top_cards"][:10]:
        if c.get("global_reliable"):
            fiab = f"OK ({c['global_distinct_drafts']} distinct drafts)"
        else:
            fiab = "⚠️ **1 draft only** (mirrors that deck's overall record)"
            any_warning = True
        lines.append(f"| {c['card_name']} | {c['winrate_pct']}% | {c['wins']}/{c['matches']} | {fiab} |")
    table = "\n".join(lines)
    if any_warning:
        note = (
            "\n\n⚠️ At least one card above has never appeared in another draft in this corpus. In a "
            "singleton cube, its winrate here is mathematically identical to the overall record of the "
            "one deck that contained it — not an independent signal (see `cards.csv`, `reliable` column)."
        )
    else:
        note = "\n\nNo card in this draft's top 10 is a single-deck case: all were seen in ≥2 distinct drafts."
    return table + note


def render_anomalies(data):
    if not data["anomalies"]:
        return "No anomalies detected while parsing this log."
    return "\n".join(f"- {a}" for a in data["anomalies"])


def render_interpretation(data, corpus):
    draft = data["draft"]
    sig = data["signals"]
    sentences = [
        f"This draft was won by {sig['winner_seat']} ({sig['winner_archetype']}, "
        f"{sig['winner_wins']}-{sig['winner_matches'] - sig['winner_wins']})."
    ]

    wt, ft = sig["winner_avg_turns"], sig["field_avg_turns_excl_winner"]
    if wt is not None and ft is not None:
        delta = round(ft - wt, 1)
        if abs(delta) < 0.5:
            sentences.append(f"Its game length is close to the rest of the table ({wt} vs {ft} avg turns).")
        elif delta > 0:
            sentences.append(f"It closes games noticeably faster than the rest of the table ({wt} vs {ft} avg turns, {delta} turns quicker).")
        else:
            sentences.append(f"It is actually slower than the rest of the table ({wt} vs {ft} avg turns) — the win doesn't come from an aggressive curve.")

    if sig["winner_colors"]:
        colors_str = "/".join(sig["winner_colors"])
        n = sig["decks_sharing_winner_color"]
        if n == 0:
            sentences.append(f"Its color(s) ({colors_str}) weren't played by any other deck this draft — a wide-open lane.")
        else:
            sentences.append(f"Its color(s) ({colors_str}) were shared by {n} other deck(s) this draft — not an open lane.")

    br = data["blocking"]["block_rate_pct"]
    rk = corpus["block_rank"][draft]
    n = corpus["n_drafts"]
    if rk["rank_asc"] == 1:
        pos = f"the lowest of the {n} drafts analyzed"
    elif rk["rank_asc"] == n:
        pos = f"the highest of the {n} drafts analyzed"
    else:
        pos = f"the {rk['rank_asc']}-lowest of {n} drafts (percentile {rk['percentile_low_to_high']}%)"
    sentences.append(f"Its legal blocking rate ({br}%) is {pos}.")

    if data["anomalies"]:
        sentences.append(f"Note: {len(data['anomalies'])} log anomaly(ies) detected and resolved via 'Match Winner -' (see Anomalies).")

    return " ".join(sentences)


def render_report(data, corpus):
    draft = data["draft"]
    return f"""# Report — {draft}

*Auto-generated by `analyze.py` + `report.py` from `{draft}/{draft}_detailed.log`, across the {corpus['n_drafts']} drafts with a detailed log available. Win detection: `Round N - p1 vs p2` + `Match Winner -` (see analyze.py header).*

## 1. Standings

{render_standings(data)}

## 2. Legal blocking rate (this draft)

{render_blocking(data)}

## 3. Interpretation

{render_interpretation(data, corpus)}

## 4. Top cards this draft — reliability

{render_top_cards(data)}

## Anomalies detected

{render_anomalies(data)}
"""


# ==============================================================================
# Corpus-wide summary
# ==============================================================================
def build_summary(all_data, card_perf):
    drafts = sorted(all_data.keys(), key=draft_num)
    n = len(drafts)

    block_all = [all_data[d]["blocking"]["block_rate_pct"] for d in drafts]
    by_block = sorted(drafts, key=lambda d: all_data[d]["blocking"]["block_rate_pct"])
    lowest, highest = by_block[0], by_block[-1]

    anomalies_by_draft = {d: all_data[d]["anomalies"] for d in drafts if all_data[d]["anomalies"]}
    total_anomalies = sum(len(v) for v in anomalies_by_draft.values())

    # Archetype performance
    arch_stats = defaultdict(lambda: {"decks": 0, "wins": 0, "matches": 0, "titles": 0})
    for d in drafts:
        standings = all_data[d]["standings"]
        winner_arch = standings[0]["archetype"] if standings else None
        for r in standings:
            a = arch_stats[r["archetype"]]
            a["decks"] += 1
            a["wins"] += r["wins"]
            a["matches"] += r["matches"]
        if winner_arch:
            arch_stats[winner_arch]["titles"] += 1
    arch_rows = [
        {"archetype": a, "decks": s["decks"], "titles": s["titles"],
         "winrate_pct": round(s["wins"] / s["matches"] * 100, 2), "matches": s["matches"]}
        for a, s in arch_stats.items() if s["matches"] > 0
    ]
    arch_rows.sort(key=lambda r: r["winrate_pct"], reverse=True)

    # Mono vs multi-color, computed directly from analysis.json (no extra file needed)
    color_groups = defaultdict(lambda: {"n_decks": 0, "wins": 0, "matches": 0})
    for d in drafts:
        for r in all_data[d]["standings"]:
            n_colors = len(r["colors"])
            bucket = "colorless" if n_colors == 0 else ("mono" if n_colors == 1 else "multi")
            g = color_groups[bucket]
            g["n_decks"] += 1
            g["wins"] += r["wins"]
            g["matches"] += r["matches"]

    n_unreliable = sum(1 for row in card_perf if not row["reliable"])

    lines = []
    lines.append("# SUMMARY\n")
    lines.append(f"*Auto-generated by `report.py` from `analysis.json` ({n} drafts with a detailed log available).*\n")

    lines.append("## 1. Overview\n")
    total_decks = sum(len(all_data[d]["standings"]) for d in drafts)
    lines.append(
        f"- **{n} drafts** analyzed, {total_decks} decks.\n"
        f"- Legal blocking rate: mean {mean(block_all)}%, from "
        f"{all_data[lowest]['blocking']['block_rate_pct']}% ({lowest}) to "
        f"{all_data[highest]['blocking']['block_rate_pct']}% ({highest}).\n"
        f"- **{total_anomalies} log anomaly(ies)** across {len(anomalies_by_draft)} draft(s) (see §5).\n"
    )

    lines.append("## 2. Archetype performance\n")
    lines.append("Pooled winrate and number of drafts each archetype actually won:\n")
    lines.append("| Archetype | Decks | Titles | Winrate | Matches |")
    lines.append("|---|---|---|---|---|")
    for r in arch_rows:
        lines.append(f"| {r['archetype']} | {r['decks']} | {r['titles']} | {r['winrate_pct']}% | {r['matches']} |")
    lines.append("")

    lines.append("## 3. Mono-color vs. multi-color\n")
    lines.append("| Group | Decks | Wins/Matches | Winrate |")
    lines.append("|---|---|---|---|")
    for bucket, label in [("mono", "Mono-color"), ("multi", "Multi-color"), ("colorless", "No color identified")]:
        g = color_groups.get(bucket)
        if g and g["matches"]:
            lines.append(f"| {label} | {g['n_decks']} | {g['wins']}/{g['matches']} | {round(g['wins']/g['matches']*100,2)}% |")
    lines.append("")

    lines.append("## 4. Top / bottom cards by winrate\n")
    reliable_rows = [r for r in card_perf if r["reliable"]]
    lines.append("Top 15 (reliable cards only — seen in ≥2 distinct drafts):\n")
    lines.append("| Card | Winrate | Matches | Distinct drafts |")
    lines.append("|---|---|---|---|")
    for r in reliable_rows[:15]:
        lines.append(f"| {r['card_name']} | {r['winrate_pct']}% | {r['games_played']} | {r['distinct_drafts']} |")
    lines.append("\nBottom 15:\n")
    lines.append("| Card | Winrate | Matches | Distinct drafts |")
    lines.append("|---|---|---|---|")
    for r in reliable_rows[-15:]:
        lines.append(f"| {r['card_name']} | {r['winrate_pct']}% | {r['games_played']} | {r['distinct_drafts']} |")
    lines.append("")

    lines.append("## 5. Anomalies detected\n")
    if anomalies_by_draft:
        for d, alist in anomalies_by_draft.items():
            for a in alist:
                lines.append(f"- **{d}**: {a}")
        lines.append("")
    else:
        lines.append("No anomalies detected.\n")

    lines.append("## 6. Limitations\n")
    if n_unreliable:
        lines.append(
            f"- **Per-card reliability**: {n_unreliable} card(s) in this corpus have never been seen in "
            f"more than one draft — their winrate mirrors the one deck that contained them rather than "
            f"being an independent signal (see `cards.csv`, `reliable` column).\n"
        )
    else:
        lines.append("- **Per-card reliability**: every card in this corpus has been seen in ≥2 distinct drafts.\n")
    lines.append(
        "- **Color classification** excludes archetypes whose label doesn't resolve to a recognizable "
        "color prefix (e.g. a generic `Combo` label) — counted separately rather than forced into a bucket.\n"
    )
    lines.append(
        "- **No sideboard access**: all matches are best-of-one (see `simulate.py`, `MATCHES_PER_PAIRING`). "
        "A card that only earns its keep in a specific matchup is played in every game regardless of "
        "opponent if maindecked, which can drag its measured winrate down relative to how it would "
        "perform if it could be deployed only when relevant. Testing on a small best-of-three sample "
        "found no evidence Forge's AI swaps cards between games of a match, so a longer match format is "
        "unlikely to change this on its own — see the project README for the full investigation.\n"
    )

    return "\n".join(lines)


def main():
    with open(os.path.join(DECKS_BASE_DIR, "analysis.json"), encoding="utf-8") as f:
        all_data = json.load(f)
    with open(os.path.join(DECKS_BASE_DIR, "cards.csv"), encoding="utf-8") as f:
        card_perf = list(csv.DictReader(f))
        for r in card_perf:
            r["games_played"] = int(r["games_played"])
            r["wins"] = int(r["wins"])
            r["winrate_pct"] = float(r["winrate_pct"])
            r["distinct_drafts"] = int(r["distinct_drafts"])
            r["reliable"] = r["reliable"] == "True"

    corpus = compute_corpus_signals(all_data)

    for draft, data in sorted(all_data.items(), key=lambda kv: draft_num(kv[0])):
        out_path = os.path.join(DECKS_BASE_DIR, draft, "REPORT.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(render_report(data, corpus))

    summary_path = os.path.join(DECKS_BASE_DIR, "SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(build_summary(all_data, card_perf))

    print(f"✓ {len(all_data)} draft_N/REPORT.md written.")
    print(f"✓ {summary_path} written.")


if __name__ == "__main__":
    main()
