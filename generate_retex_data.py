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

# ==============================================================================
# Détection du résultat de MATCH — voir note en bas de fichier ("VÉRIFICATION
# DOUBLE COMPTAGE / ROBUSTESSE À 100 DRAFTS") pour la justification du choix
# ci-dessous. Ni "Match Result:" ni les lignes "Game Outcome: ... has won" ne
# sont fiables seules à l'échelle du corpus complet : "Round N - p1[x] vs
# p2[y]" + "Match Winner - X!" sont, elles, vérifiées exactement 1x/1x par
# paire sur les 2800 matchs des 100 drafts, sans exception.
# ==============================================================================
ROUND_RE = re.compile(r"^Round \d+ - (\S+)\[\d+\] vs (\S+)\[\d+\]")
MATCH_WINNER_RE = re.compile(r"^Match Winner - (\S+)!")
GAME_TURN_RE = re.compile(r"Game Outcome: Turn (\d+)")
GAME_LOST_RE = re.compile(r"Game Outcome: (\S+) has lost(?: (.*))?$")
GAME_WON_RE = re.compile(r"Game Outcome: (\S+) has won")

MIN_DRAFTS_FOR_CARD_STATS = 2

# ==============================================================================
# 1. CHARGEMENT DYNAMIQUE DES CAPACITÉS DEPUIS FORGE
# ==============================================================================
def load_forge_keywords(forge_dir):
    flying, reach, shadow = set(), set(), set()
    res_dir = os.path.join(forge_dir, "res")
    if not os.path.exists(res_dir):
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
                    with zipfile.ZipFile(filepath, 'r') as z:
                        for zname in z.namelist():
                            if zname.endswith(".txt"):
                                with z.open(zname) as f:
                                    lines = [l.decode('utf-8', errors='replace') for l in f.readlines()]
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
    owner = {}
    seat_decks = defaultdict(set)
    for path in glob.glob(os.path.join(draft_dir, "*.dck")):
        seat = os.path.splitext(os.path.basename(path))[0]
        in_main = False
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line == "[Main]": in_main = True; continue
                if line.startswith("[") and line.endswith("]"): in_main = False; continue
                if in_main and line and not line.startswith("#"):
                    parts = line.split(" ", 1)
                    name = parts[1] if len(parts) == 2 and parts[0].isdigit() else line
                    if name not in BASICS:
                        owner[name] = seat
                        seat_decks[seat].add(name)
    return owner, seat_decks


def parse_archetype(seat):
    """'D4_S3_W_Weenie' -> ('W_Weenie', {'W'})"""
    m = re.match(r"^D\d+_S\d+_(.+)$", seat)
    archetype = m.group(1) if m else seat
    prefix = archetype.split("_", 1)[0]
    colors = set(prefix) & COLOR_LETTERS if set(prefix) <= COLOR_LETTERS else set()
    return archetype, colors


def load_global_card_reliability(path):
    """Relit cards_performance.csv (déjà généré par analyze_all_drafts.py) pour
    récupérer le winrate GLOBAL individuel de chaque carte + son statut de fiabilité
    (distinct_drafts >= MIN_DRAFTS_FOR_CARD_STATS)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["card_name"]] = {
                "winrate_pct": float(row["winrate_pct"]),
                "games_played": int(row["games_played"]),
                "wins": int(row["wins"]),
                "distinct_drafts": int(row["distinct_drafts"]),
                "reliable": row["reliable"] == "True",
            }
    return out


def analyze_draft(draft_dir, log_path, SHADOW, FLYING, REACH, global_cards):
    draft_name = os.path.basename(draft_dir)
    owner, seat_decks = load_deck_details(draft_dir)
    text = open(log_path, encoding="utf-8", errors="replace").read()
    lines = text.splitlines()

    # ---------------- Blocage ----------------
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
            if name in owner: board[owner[name]].add(name)
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
    block_rate_pct = round((d_blocking["real_block"] / denom * 100) if denom else 0.0, 2)
    blocking = dict(d_blocking)
    blocking["total_events"] = total_events
    blocking["block_rate_pct"] = block_rate_pct
    for k in ("real_block", "real_nonblock", "evasion_legal", "empty_board"):
        blocking[f"pct_{k}"] = round(d_blocking[k] / total_events * 100, 1) if total_events else 0.0

    # ---------------- Résultats de matchs + durée/cause de défaite (passe unique) ----------------
    # Une paire de decks peut jouer PLUSIEURS parties au sein d'un même match Bo1 :
    #  - un vrai match nul (perte simultanée des deux joueurs, ex. Earthquake
    #    symétrique) fait rejouer une partie par Forge -> gérer explicitement,
    #    ne pas compter comme 2 matchs.
    #  - un timeout IA (120000 ms) peut émettre 2 lignes "has won" contradictoires
    #    pour la même partie -> résolu via le résultat de match final.
    # "Round N - p1[x] vs p2[y]" identifie la paire, "Match Winner - X!" est le
    # SEUL événement qui clôt un match (vérifié unique par paire sur tout le corpus) :
    # c'est notre unique point de crédit pour seat_matches/seat_wins/card_stats_draft.
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
                                f"Partie {seat_a} vs {seat_b} (Turn {turn}) : match nul réel (perte "
                                f"simultanée des deux joueurs) — Forge rejoue une partie supplémentaire "
                                f"pour départager la paire."
                            )
                        elif wa == "won" and wb == "won":
                            if is_last:
                                g_loser = loser
                                anomalies.append(
                                    f"Partie {seat_a} vs {seat_b} (Turn {turn}) : le log Forge émet 2 "
                                    f"lignes 'Game Outcome: ... has won' contradictoires — probable "
                                    f"timeout IA (120000 ms). Vainqueur retenu via 'Match Winner -' : "
                                    f"{winner}."
                                )
                            else:
                                g_loser = None
                                anomalies.append(
                                    f"Partie {seat_a} vs {seat_b} (Turn {turn}) : issue de partie "
                                    f"indéterminée (double 'has won', partie non-décisive de la paire)."
                                )
                        else:
                            g_loser = None
                            anomalies.append(
                                f"Partie {seat_a} vs {seat_b} (Turn {turn}) : statut de fin de partie "
                                f"introuvable dans le log."
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
            "shadow_cards": sorted(seat_decks[seat] & SHADOW),
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

    # ---------------- Cartes Shadow dans le top 3 ----------------
    top3_shadow = []
    for row in standings[:3]:
        sh_cards = row["shadow_cards"]
        if not sh_cards:
            continue
        cards_info = []
        for c in sh_cards:
            g = global_cards.get(c, {})
            cards_info.append({
                "card_name": c,
                "global_winrate_pct": g.get("winrate_pct"),
                "global_games_played": g.get("games_played"),
                "distinct_drafts": g.get("distinct_drafts"),
                "reliable": g.get("reliable"),
            })
        top3_shadow.append({"seat": row["seat"], "rank": row["rank"], "shadow_cards": cards_info})

    # ---------------- Top cartes DE CE DRAFT (avec avertissement fiabilité globale) ----------------
    draft_top_cards = []
    for card, s in card_stats_draft.items():
        if s["matches"] == 0:
            continue
        wr = round(s["wins"] / s["matches"] * 100, 2)
        g = global_cards.get(card, {})
        draft_top_cards.append({
            "card_name": card,
            "matches": s["matches"],
            "wins": s["wins"],
            "winrate_pct": wr,
            "global_distinct_drafts": g.get("distinct_drafts"),
            "global_reliable": g.get("reliable"),
        })
    draft_top_cards.sort(key=lambda r: r["winrate_pct"], reverse=True)
    draft_top_cards = draft_top_cards[:15]

    # ---------------- Signaux LOCAUX (comparaison avec le reste de CE draft uniquement) ----------------
    winner = standings[0]
    others_avg = [duration[s["seat"]]["avg_turns"] for s in standings[1:] if duration[s["seat"]]["avg_turns"]]
    field_avg_turns = round(sum(others_avg) / len(others_avg), 1) if others_avg else None
    winner_avg_turns = duration[winner["seat"]]["avg_turns"]

    contest = 0
    for row in standings:
        if row["seat"] == winner["seat"]:
            continue
        if set(row["colors"]) & set(winner["colors"]) and winner["colors"]:
            contest += 1

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
        "winner_shadow_cards": winner["shadow_cards"],
    }

    return {
        "draft": draft_name,
        "n_decks": len(seat_decks),
        "standings": standings,
        "blocking": blocking,
        "duration": duration,
        "top3_shadow": top3_shadow,
        "draft_top_cards": draft_top_cards,
        "anomalies": anomalies,
        "signals": signals,
    }


# ==============================================================================
# 2. CROISEMENT mono-couleur / bi-tricolore x présence de Shadow, TOUS SIÈGES
#    (pas seulement le top 3 — relit `standings`, qui porte désormais
#    `colors` et `shadow_cards` pour chaque siège de chaque draft)
# ==============================================================================
def export_mono_vs_shadow(results, out_path):
    def bucket(row):
        n = len(row["colors"])
        if n == 0: return "colorless"
        if n == 1: return "mono"
        return "multi"

    groups = defaultdict(lambda: {"n_decks": 0, "wins": 0, "matches": 0})
    for data in results.values():
        for row in data["standings"]:
            b = bucket(row)
            shadow_class = "with_shadow" if row["shadow_cards"] else "without_shadow"
            for key in ((b, shadow_class), (b, "all")):
                g = groups[key]
                g["n_decks"] += 1
                g["wins"] += row["wins"]
                g["matches"] += row["matches"]

    rows = []
    order = [
        ("mono", "all"), ("multi", "all"), ("colorless", "all"),
        ("mono", "with_shadow"), ("mono", "without_shadow"),
        ("multi", "with_shadow"), ("multi", "without_shadow"),
        ("colorless", "with_shadow"), ("colorless", "without_shadow"),
    ]
    for key in order:
        g = groups.get(key)
        if g is None:
            continue
        rows.append({
            "color_class": key[0],
            "shadow_class": key[1],
            "n_decks": g["n_decks"],
            "wins": g["wins"],
            "matches": g["matches"],
            "winrate_pct": round(g["wins"] / g["matches"] * 100, 2) if g["matches"] else 0.0,
        })

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["color_class", "shadow_class", "n_decks", "wins", "matches", "winrate_pct"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


# ==============================================================================
# 3. MAIN
# ==============================================================================
def main():
    FLYING, REACH, SHADOW = load_forge_keywords(FORGE_DIR)
    global_cards = load_global_card_reliability(os.path.join(DECKS_BASE_DIR, "cards_performance.csv"))

    log_files = sorted(
        glob.glob(os.path.join(DECKS_BASE_DIR, "draft_*", "*_detailed.log")),
        key=lambda p: int(re.search(r"draft_(\d+)", p).group(1)),
    )
    results = {}
    total_anomalies = 0
    for log_path in log_files:
        draft_dir = os.path.dirname(log_path)
        data = analyze_draft(draft_dir, log_path, SHADOW, FLYING, REACH, global_cards)
        results[data["draft"]] = data
        total_anomalies += len(data["anomalies"])
        print(f"✓ {data['draft']} analysé ({data['n_decks']} decks, {len(data['anomalies'])} anomalie(s))")

    out_path = os.path.join(DECKS_BASE_DIR, "retex_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📂 Données exportées ({len(results)} drafts, {total_anomalies} anomalie(s) au total) : {out_path}")

    mono_path = os.path.join(DECKS_BASE_DIR, "mono_vs_shadow.csv")
    rows = export_mono_vs_shadow(results, mono_path)
    print(f"📂 Croisement mono/multi-couleur x Shadow exporté : {mono_path}")
    for r in rows:
        print(f"   {r['color_class']:<10} {r['shadow_class']:<15} n={r['n_decks']:>4}  "
              f"{r['wins']}/{r['matches']} = {r['winrate_pct']}%")


if __name__ == "__main__":
    main()

# ==============================================================================
# NOTE — VÉRIFICATION DOUBLE COMPTAGE / ROBUSTESSE À 100 DRAFTS
# ==============================================================================
# Version précédente (10 drafts) : la détection de victoire se basait sur la ligne
# "Match Result: <siège1>: <score> <siège2>: <score>", choisie car elle donnait les
# deux participants + le score en une seule ligne. Elle s'est révélée insuffisante
# en passant à l'échelle de 100 drafts (2800 matchs) :
#
# 1) draft_32 contient une partie qui se termine par un vrai match nul (un
#    Earthquake symétrique amène les DEUX joueurs à 0 PV au même moment). Forge
#    déclare "Game 1 ended in a Draw!" et rejoue une seconde partie pour
#    départager la paire. Cela produit DEUX lignes "Match Result:" pour la MÊME
#    paire (la première à 0-0, la seconde décisive) — avec l'ancienne méthode,
#    ce siège se serait vu créditer 8 matchs joués au lieu de 7 sur ce draft,
#    faussant son winrate_pct à la baisse sans raison.
#
# 2) 5 drafts sur 100 (draft_10, 16, 20, 32, 89) contiennent une partie ayant
#    atteint le timeout IA (120000 ms), où le log émet DEUX lignes
#    "Game Outcome: ... has won" contradictoires pour la même partie (déjà
#    identifié sur draft_10 dans la version précédente).
#
# Aucune de ces deux anomalies n'affecte en revanche "Round N - p1[x] vs p2[y]"
# ni "Match Winner - X!" : vérifié par comptage exhaustif, chacune de ces deux
# lignes apparaît exactement 28 fois par draft (2800 au total), sans doublon ni
# omission, sur l'intégralité des 100 logs. Ce script les utilise donc comme
# unique source de vérité pour le résultat de MATCH (voir analyze_draft ci-
# dessus) ; les lignes "Game Outcome:" par partie individuelle ne servent plus
# qu'à la durée/cause de défaite, avec résolution explicite des deux anomalies
# ci-dessus (match nul compté comme tel, timeout résolu via "Match Winner -").
# ==============================================================================
