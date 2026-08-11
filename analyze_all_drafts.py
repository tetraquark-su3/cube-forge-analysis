import os
import re
import glob
import zipfile
import csv
import math
from collections import defaultdict

# ==============================================================================
# CONFIGURATION
# ==============================================================================
from config import FORGE_DIR, DECKS_BASE_DIR

BASICS = {"Island", "Mountain", "Swamp", "Forest", "Plains"}

# Fiabilité des stats carte-par-carte : une carte qui n'apparaît que dans un seul
# deck (donc un seul draft) hérite mécaniquement du score de ce deck. On l'exclut
# du calcul de corrélation Shadow (l'analyse Shadow ci-dessous, elle, ne calcule
# JAMAIS un winrate de carte : elle calcule un winrate de SIEGE par draft, ce qui
# évite ce biais par construction) mais on garde le comptage pour cards_performance.csv
# à titre informatif (colonne distinct_drafts).
MIN_DRAFTS_FOR_CARD_STATS = 2

# Détection de victoire de MATCH — voir note "VÉRIFICATION DOUBLE COMPTAGE /
# ROBUSTESSE À 100 DRAFTS" en bas de fichier. Ni "Match Result: <s1>: <sc1>
# <s2>: <sc2>" ni les 3 anciennes regex ("Match Winner -", "Game Outcome: ...
# has won", "... has won!") ne sont fiables seules à l'échelle de 100 drafts :
# une paire peut produire plusieurs lignes "Match Result:" (vrai match nul
# rejoué par Forge, cf. draft_32) ou plusieurs lignes "has won" contradictoires
# (timeout IA 120000 ms, 5 drafts/100). "Round N - p1[x] vs p2[y]" +
# "Match Winner - X!" sont, elles, vérifiées exactement 1x/1x par paire sur
# les 2800 matchs du corpus complet, sans exception : on les utilise comme
# unique source de vérité.
ROUND_RE = re.compile(r"^Round \d+ - (\S+)\[\d+\] vs (\S+)\[\d+\]")
MATCH_WINNER_RE = re.compile(r"^Match Winner - (\S+)!")

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

# ==============================================================================
# 2. LECTURE DES DECKS ET PROPRIÉTAIRES
# ==============================================================================
def load_deck_details(draft_dir):
    """
    Retourne:
    - owner: dict {card_name: seat_id}
    - seat_decks: dict {seat_id: set_of_cards_in_main}
    """
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

# ==============================================================================
# 3. STATS
# ==============================================================================
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

# ==============================================================================
# 4. MAIN (ANALYSE COMBINÉE : COMBAT + PERFORMANCE CARTES + CROISEMENT SHADOW)
# ==============================================================================
def main():
    FLYING, REACH, SHADOW = load_forge_keywords(FORGE_DIR)
    print(f"✅ Base Forge chargée : {len(FLYING)} Vol | {len(REACH)} Portée | {len(SHADOW)} Ombre")

    log_files = sorted(glob.glob(os.path.join(DECKS_BASE_DIR, "draft_*", "*_detailed.log")))
    if not log_files:
        print("❌ Aucun fichier log trouvé.")
        return

    print(f"🚀 Analyse globale de {len(log_files)} drafts (Combat + Performance cartes + Croisement Shadow)...\n")

    # Structures pour le blocage
    global_blocking = {"empty_board": 0, "evasion_legal": 0, "real_nonblock": 0, "real_block": 0}
    blocking_csv_rows = []

    # Structures pour la performance des cartes (agrégées sur tous les drafts)
    card_stats = defaultdict(lambda: {"played": 0, "wins": 0, "drafts": set()})

    # Structures pour le croisement blocage / Shadow
    shadow_detail_rows = []   # 1 ligne par (draft, siège) possédant au moins 1 carte Shadow
    shadow_summary_rows = []  # 1 ligne par draft (table demandée dans le brief)

    for log_path in log_files:
        draft_dir = os.path.dirname(log_path)
        draft_name = os.path.basename(draft_dir)
        owner, seat_decks = load_deck_details(draft_dir)
        board = defaultdict(set)

        d_blocking = {"empty_board": 0, "evasion_legal": 0, "real_nonblock": 0, "real_block": 0}
        seat_matches = defaultdict(int)
        seat_wins = defaultdict(int)
        current_pair = None

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")

                # Identification de la paire en cours (source de vérité pour le
                # résultat de match, cf. ROUND_RE / MATCH_WINNER_RE ci-dessus).
                m_round = ROUND_RE.match(line)
                if m_round:
                    current_pair = (m_round.group(1), m_round.group(2))
                    continue

                # Réinitialisation du plateau au tour 1 de chaque nouvelle partie
                m_turn = re.search(r"Turn: Turn (\d+) \(", line)
                if m_turn:
                    if m_turn.group(1) == "1":
                        board = defaultdict(set)
                    continue

                # Détection d'apparition de créatures
                m = re.match(r"Resolve Stack: (.+?) - Creature", line)
                if m:
                    name = m.group(1)
                    if name in owner:
                        board[owner[name]].add(name)
                    continue

                # Détection de mort de créatures
                m = re.match(r"Zone Change: (.+?) \(\d+\) was put into (Graveyard|Exile) from Battlefield\.", line)
                if m:
                    name = m.group(1)
                    if name in owner and name in board[owner[name]]:
                        board[owner[name]].discard(name)
                    continue

                # Détection des blocages
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
                        if defboard & (FLYING | REACH): d_blocking["real_nonblock"] += 1
                        else: d_blocking["evasion_legal"] += 1
                    else:
                        d_blocking["real_nonblock"] += 1
                    continue

                # Détection de victoire de MATCH — cf. ROUND_RE / MATCH_WINNER_RE.
                m_mw = MATCH_WINNER_RE.match(line)
                if m_mw and current_pair:
                    winner_seat = m_mw.group(1)
                    s1, s2 = current_pair
                    if winner_seat in current_pair and s1 in seat_decks and s2 in seat_decks:
                        loser_seat = s1 if winner_seat == s2 else s2
                        seat_matches[s1] += 1
                        seat_matches[s2] += 1
                        seat_wins[winner_seat] += 1
                        for seat in (winner_seat, loser_seat):
                            is_winner = (seat == winner_seat)
                            for card in seat_decks[seat]:
                                card_stats[card]["played"] += 1
                                card_stats[card]["drafts"].add(draft_name)
                                if is_winner:
                                    card_stats[card]["wins"] += 1
                    current_pair = None
                    continue

        # Cumul global blocage
        for k in global_blocking:
            global_blocking[k] += d_blocking[k]

        denom = d_blocking["real_block"] + d_blocking["real_nonblock"]
        rate = round((d_blocking["real_block"] / denom * 100) if denom > 0 else 0.0, 2)

        blocking_csv_rows.append({
            "draft": draft_name,
            "real_block": d_blocking["real_block"],
            "real_nonblock": d_blocking["real_nonblock"],
            "evasion_legal": d_blocking["evasion_legal"],
            "empty_board": d_blocking["empty_board"],
            "block_rate_pct": rate
        })

        # --- Croisement block_rate_pct / winrate Shadow, PAR DRAFT (pas agrégé) ---
        # Classement des sièges du draft par nombre de victoires (1 = meilleur deck)
        ranking = sorted(seat_decks.keys(), key=lambda s: seat_wins[s], reverse=True)
        rank_of = {seat: i + 1 for i, seat in enumerate(ranking)}

        shadow_seats_this_draft = []
        for seat, cards in seat_decks.items():
            sh_cards = cards & SHADOW
            if not sh_cards:
                continue
            matches = seat_matches[seat]
            wins = seat_wins[seat]
            winrate = round((wins / matches * 100) if matches > 0 else 0.0, 2)
            row = {
                "draft": draft_name,
                "seat": seat,
                "shadow_card_count": len(sh_cards),
                "shadow_cards": ";".join(sorted(sh_cards)),
                "block_rate_pct": rate,
                "seat_matches": matches,
                "seat_wins": wins,
                "seat_winrate_pct": winrate,
                "seat_rank": rank_of.get(seat, ""),
            }
            shadow_detail_rows.append(row)
            shadow_seats_this_draft.append(row)

        if shadow_seats_this_draft:
            # "Le" deck Shadow du draft = le siège le plus concentré en cartes Shadow
            # (départage par winrate). Les décks avec une seule carte Shadow splashée
            # restent visibles dans shadow_seats_detail.csv, juste pas retenus comme
            # deck "primaire" pour la table de corrélation.
            primary = max(shadow_seats_this_draft, key=lambda r: (r["shadow_card_count"], r["seat_winrate_pct"]))
            shadow_summary_rows.append({
                "draft": draft_name,
                "block_rate_pct": rate,
                "shadow_present": True,
                "shadow_deck_seat": primary["seat"],
                "shadow_deck_card_count": primary["shadow_card_count"],
                "shadow_deck_winrate": primary["seat_winrate_pct"],
                "shadow_deck_rank": primary["seat_rank"],
            })
        else:
            shadow_summary_rows.append({
                "draft": draft_name,
                "block_rate_pct": rate,
                "shadow_present": False,
                "shadow_deck_seat": "",
                "shadow_deck_card_count": 0,
                "shadow_deck_winrate": "",
                "shadow_deck_rank": "",
            })

    # ==========================================================================
    # EXPORTS CSV
    # ==========================================================================
    # CSV 1: Blocages par Draft
    csv_block_path = os.path.join(DECKS_BASE_DIR, "drafts_blocking_scores.csv")
    with open(csv_block_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["draft", "real_block", "real_nonblock", "evasion_legal", "empty_board", "block_rate_pct"])
        writer.writeheader()
        writer.writerows(blocking_csv_rows)

    # CSV 2: Surperformance des Cartes (agrégée sur tous les drafts analysés)
    card_perf_rows = []
    for card, stats in card_stats.items():
        played = stats["played"]
        wins = stats["wins"]
        winrate = (wins / played * 100) if played > 0 else 0
        card_perf_rows.append({
            "card_name": card,
            "games_played": played,
            "wins": wins,
            "winrate_pct": round(winrate, 2),
            "distinct_drafts": len(stats["drafts"]),
            "reliable": len(stats["drafts"]) >= MIN_DRAFTS_FOR_CARD_STATS,
        })
    card_perf_rows.sort(key=lambda x: x["winrate_pct"], reverse=True)

    csv_cards_path = os.path.join(DECKS_BASE_DIR, "cards_performance.csv")
    with open(csv_cards_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["card_name", "games_played", "wins", "winrate_pct", "distinct_drafts", "reliable"])
        writer.writeheader()
        writer.writerows(card_perf_rows)

    # CSV 3: détail par (draft, siège Shadow)
    csv_shadow_detail_path = os.path.join(DECKS_BASE_DIR, "shadow_seats_detail.csv")
    with open(csv_shadow_detail_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["draft", "seat", "shadow_card_count", "shadow_cards", "block_rate_pct", "seat_matches", "seat_wins", "seat_winrate_pct", "seat_rank"])
        writer.writeheader()
        writer.writerows(shadow_detail_rows)

    # CSV 4: table de sortie demandée dans le brief (block_rate_pct vs deck Shadow primaire, par draft)
    csv_shadow_summary_path = os.path.join(DECKS_BASE_DIR, "shadow_vs_blocking.csv")
    with open(csv_shadow_summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["draft", "block_rate_pct", "shadow_present", "shadow_deck_seat", "shadow_deck_card_count", "shadow_deck_winrate", "shadow_deck_rank"])
        writer.writeheader()
        writer.writerows(shadow_summary_rows)

    # ==========================================================================
    # CORRÉLATION block_rate_pct <-> winrate du deck Shadow primaire
    # (uniquement sur les drafts où Shadow est présent)
    # ==========================================================================
    present_rows = [r for r in shadow_summary_rows if r["shadow_present"]]
    xs = [r["block_rate_pct"] for r in present_rows]
    ys = [r["shadow_deck_winrate"] for r in present_rows]
    r_value = pearson_corr(xs, ys)

    # Vérifications de robustesse : la sélection "1 deck primaire par draft" ci-dessus
    # départage arbitrairement les cas où plusieurs sièges possèdent des cartes Shadow
    # (fréquent : le package se scinde souvent entre un W/Soltari et un B/Dauthi dans
    # le même draft). Deux recoupements supplémentaires, sans cette simplification :
    all_seats_xs = [r["block_rate_pct"] for r in shadow_detail_rows]
    all_seats_ys = [r["seat_winrate_pct"] for r in shadow_detail_rows]
    r_all_seats = pearson_corr(all_seats_xs, all_seats_ys)

    packaged_rows = [r for r in shadow_detail_rows if r["shadow_card_count"] >= 2]
    packaged_xs = [r["block_rate_pct"] for r in packaged_rows]
    packaged_ys = [r["seat_winrate_pct"] for r in packaged_rows]
    r_packaged = pearson_corr(packaged_xs, packaged_ys)

    # ==========================================================================
    # RÉSUMÉ TERMINAL
    # ==========================================================================
    g_denom = global_blocking["real_block"] + global_blocking["real_nonblock"]
    g_rate = (global_blocking["real_block"] / g_denom * 100) if g_denom > 0 else 0

    print("=" * 70)
    print(" 📊 BILAN DE L'ANALYSE COMBINÉE")
    print("=" * 70)
    print(f" • Taux de blocage réel global : {global_blocking['real_block']}/{g_denom} = {g_rate:.1f}%")
    print(f" • Cartes uniques analysées    : {len(card_perf_rows)}")
    unreliable = sum(1 for r in card_perf_rows if not r["reliable"])
    print(f"   dont {unreliable} vues dans < {MIN_DRAFTS_FOR_CARD_STATS} draft(s) distinct(s) (winrate_pct peu fiable, colonne 'reliable'=False)")
    print("-" * 70)
    print(" 🏆 TOP 10 DES CARTES SURPERFORMANTES :")
    for i, row in enumerate(card_perf_rows[:10], 1):
        flag = "" if row["reliable"] else "  ⚠️ 1 seul draft"
        print(f"   {i}. {row['card_name']:<25} | WR: {row['winrate_pct']}% ({row['wins']}/{row['games_played']} games){flag}")
    print("-" * 70)
    print(" 🌑 CROISEMENT block_rate_pct <-> WINRATE DU DECK SHADOW (par draft) :")
    print(f"   {'draft':<10} {'block_rate_pct':>15} {'shadow_present':>15} {'shadow_winrate':>15} {'rank':>6}")
    for r in shadow_summary_rows:
        wr = f"{r['shadow_deck_winrate']}%" if r["shadow_present"] else "-"
        rk = str(r["shadow_deck_rank"]) if r["shadow_present"] else "-"
        print(f"   {r['draft']:<10} {r['block_rate_pct']:>14.2f}% {str(r['shadow_present']):>15} {wr:>15} {rk:>6}")
    print("-" * 70)
    if r_value is None:
        print(" ⚠️ Corrélation non calculable (moins de 2 drafts avec Shadow présente, ou variance nulle).")
    else:
        print(f" 📈 Corrélation de Pearson (block_rate_pct, shadow_deck_winrate) sur {len(present_rows)} drafts : r = {r_value:.3f}")
        if r_value <= -0.5:
            print("    → Corrélation fortement négative : signal en faveur d'un ARTEFACT IA (moins de blocage → Shadow gagne plus).")
        elif r_value >= -0.2:
            print("    → Pas de corrélation négative marquée : le winrate Shadow reste élevé indépendamment du blocage")
            print("      → signal en faveur d'une mécanique INTRINSÈQUEMENT trop forte plutôt qu'un bug IA isolé.")
        else:
            print("    → Signal ambigu (corrélation négative modérée) : à confirmer avec plus de drafts.")
    print("   Robustesse (sans le choix arbitraire d'un seul deck 'primaire' par draft) :")
    if r_all_seats is None:
        print("    • tous les sièges Shadow (splash 1 carte inclus) : non calculable")
    else:
        print(f"    • tous les sièges Shadow, splash inclus (n={len(shadow_detail_rows)})     : r = {r_all_seats:.3f}")
    if r_packaged is None:
        print(f"    • sièges avec paquet Shadow concentré (>=2 cartes, n={len(packaged_rows)}) : non calculable (variance nulle ou n<2)")
    else:
        print(f"    • sièges avec paquet Shadow concentré (>=2 cartes, n={len(packaged_rows)}) : r = {r_packaged:.3f}")
    print("-" * 70)
    print(f" 📂 CSV Blocages exporté          : {csv_block_path}")
    print(f" 📂 CSV Performance cartes        : {csv_cards_path}")
    print(f" 📂 CSV Détail sièges Shadow      : {csv_shadow_detail_path}")
    print(f" 📂 CSV Blocage vs Shadow (draft) : {csv_shadow_summary_path}")
    print("=" * 70)

# ==============================================================================
# NOTE — VÉRIFICATION DU DOUBLE COMPTAGE / ROBUSTESSE À 100 DRAFTS
# ==============================================================================
# Historique en 2 étapes :
#
# 1) Version initiale (3 regex "Match Winner -" / "Game Outcome: ... has won" /
#    "... has won!" combinées avec des `or`) : `games_played` par carte n'était
#    PAS gonflé (le code vidait `active_seats` après le premier match trouvé),
#    mais un vrai bug de mauvaise attribution existait sur draft_10 : une
#    partie ayant atteint le timeout IA (120000 ms) émet DEUX lignes
#    "Game Outcome: <siège> has won ..." contradictoires à la suite ; le code
#    créditait la victoire au premier siège rencontré (le perdant réel).
#    -> Corrigé en passant à "Match Result: <s1>: <sc1> <s2>: <sc2>", seule et
#    unique par match sur l'échantillon de 10 drafts testé à l'époque.
#
# 2) En étendant aux 100 drafts, "Match Result:" s'est révélée insuffisante à
#    son tour : draft_32 contient un vrai match nul (perte simultanée des deux
#    joueurs, Earthquake symétrique) que Forge rejoue automatiquement, ce qui
#    produit DEUX lignes "Match Result:" pour la MÊME paire (aurait compté 8
#    matchs joués au lieu de 7 pour les 2 sièges concernés). Et le bug du
#    timeout IA touche en réalité 5 drafts/100 (10, 16, 20, 32, 89), pas 1 seul.
#    -> Ce script utilise désormais "Round N - p1[x] vs p2[y]" (identifie la
#    paire) + "Match Winner - X!" (clôt le match) comme unique source de
#    vérité : vérifiées exactement 28x/28x par draft sur l'intégralité des 100
#    logs (2800 matchs), sans doublon ni omission. Voir `generate_retex_data.py`
#    pour la même correction appliquée en détail par-partie (durée, cause de
#    défaite), y compris la gestion explicite du match nul de draft_32.
# ==============================================================================

if __name__ == "__main__":
    main()
