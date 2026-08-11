import os
import re
import shutil
import subprocess
import glob
from collections import defaultdict

# ==============================================================================
# CONFIGURATION
# ==============================================================================
from config import FORGE_DIR, DECKS_BASE_DIR

FORGE_CONSTRUCTED_DIR = os.path.expanduser("~/.forge/decks/constructed")

MATCHES_PER_PAIRING = 1  # Nombre de matchs par confrontation
TARGET_DRAFTS = range(80, 101)     # None pour TOUS
SAVE_LOGS_TO_FILE = True # Sauvegarder le déroulé complet dans un fichier .log
PRINT_LIVE_LOGS = False  # Mettre à False pour garder un terminal 100% propre

# ==============================================================================
# UTILITAIRES & ANALYSE
# ==============================================================================
def find_forge_jar(forge_dir):
    pattern = os.path.join(forge_dir, "forge-gui-desktop-*.jar")
    jars = glob.glob(pattern)
    if not jars:
        raise FileNotFoundError(f"Fichier forge-gui-desktop-*.jar introuvable dans {forge_dir}")
    return jars[0]

def load_deck_cards(deck_path):
    cards = set()
    if not os.path.exists(deck_path):
        return cards
    in_main = False
    with open(deck_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line == "[Main]":
                in_main = True
            elif line.startswith("[") and line.endswith("]"):
                in_main = False
            elif in_main and line and not line.startswith("#"):
                parts = line.split(" ", 1)
                cards.add(parts[1] if len(parts) == 2 and parts[0].isdigit() else line)
    return cards

def analyze_and_print_results(output, draft_name):
    deck_wins = defaultdict(int)
    card_stats = defaultdict(lambda: {"wins": 0, "matches": 0})
    deck_game_stats = defaultdict(lambda: {"turns": [], "losses_pv": 0, "losses_deckout": 0})

    matches = re.findall(
        r"Round \d+ - (.*?)\[\d+\] vs (.*?)\[\d+\].*?Match Winner - (.*?)!",
        output, re.DOTALL
    )

    for p1_name, p2_name, winner in matches:
        p1_name, p2_name, winner = p1_name.strip(), p2_name.strip(), winner.strip()
        loser = p2_name if winner == p1_name else p1_name
        deck_wins[winner] += 1
        deck_wins[loser] += 0

        winner_cards = load_deck_cards(os.path.join(FORGE_CONSTRUCTED_DIR, f"{winner}.dck"))
        loser_cards = load_deck_cards(os.path.join(FORGE_CONSTRUCTED_DIR, f"{loser}.dck"))

        for card in winner_cards:
            card_stats[card]["wins"] += 1
            card_stats[card]["matches"] += 1
        for card in loser_cards:
            card_stats[card]["matches"] += 1

    game_blocks = re.split(r"(?=Game Outcome: Turn \d+)", output)
    for block in game_blocks:
        turn_match = re.search(r"Game Outcome: Turn (\d+)", block)
        winner_match = re.search(r"Game Outcome: (.*?) has won", block)
        loser_match = re.search(r"Game Outcome: (.*?) has lost (.*)", block)

        if turn_match and winner_match and loser_match:
            turns = int(turn_match.group(1))
            winner_name, loser_name = winner_match.group(1).strip(), loser_match.group(1).strip()
            reason = loser_match.group(2).strip()

            deck_game_stats[winner_name]["turns"].append(turns)
            deck_game_stats[loser_name]["turns"].append(turns)

            if "empty library" in reason or "draw cards" in reason:
                deck_game_stats[loser_name]["losses_deckout"] += 1
            elif "life total" in reason:
                deck_game_stats[loser_name]["losses_pv"] += 1

    print(f"\n=================== BILAN DU {draft_name.upper()} ===================")
    print("--- CLASSEMENT GÉNÉRAL DES DECKS ---")
    for deck, wins in sorted(deck_wins.items(), key=lambda x: x[1], reverse=True):
        print(f"• {deck:<45} : {wins} victoires")

    print("\n--- METRIQUES DE JEU ET VITESSE ---")
    for deck, wins in sorted(deck_wins.items(), key=lambda x: x[1], reverse=True):
        stats = deck_game_stats[deck]
        turns = stats["turns"]
        total = len(turns)
        avg_t = sum(turns) / total if total > 0 else 0
        print(f"• {deck}")
        print(f"  └─ Durée moyenne ({total} games) : {avg_t:.1f} tours (Min: {min(turns, default=0)} / Max: {max(turns, default=0)})")
        print(f"  └─ Causes de défaite         : {stats['losses_pv']} aux PV | {stats['losses_deckout']} sur Meule")

    print("\n--- TOP 15 CARTES DU DRAFT (WINRATE) ---")
    card_winrates = [
        (card, (data["wins"] / data["matches"]) * 100, data["wins"], data["matches"])
        for card, data in card_stats.items() if data["matches"] > 0
    ]
    for card, wr, wins, total in sorted(card_winrates, key=lambda x: x[1], reverse=True)[:15]:
        print(f"• {card:<32} | {wr:5.1f}% ({wins}/{total} matchs)")
    print("===================================================================\n")

# ==============================================================================
# EXECUTION DU TOURNOI
# ==============================================================================
def run_draft_tournament(draft_folder, jar_path):
    draft_name = os.path.basename(draft_folder)
    deck_files = sorted(glob.glob(os.path.join(draft_folder, "*.dck")))

    if len(deck_files) < 2:
        print(f"⚠️ Pas assez de decks dans {draft_name}")
        return

    os.makedirs(FORGE_CONSTRUCTED_DIR, exist_ok=True)
    deck_names_cli = []
    for dck_file in deck_files:
        filename = os.path.basename(dck_file)
        shutil.copy2(dck_file, os.path.join(FORGE_CONSTRUCTED_DIR, filename))
        deck_names_cli.append(os.path.splitext(filename)[0])

    print(f"\n🚀 LANCEMENT DU TOURNOI : {draft_name.upper()} ({len(deck_names_cli)} decks)")

    cmd = [
        "java", "-Xmx4096m", "-jar", jar_path, "sim", "-d"
    ] + deck_names_cli + ["-t", "RoundRobin", "-m", str(MATCHES_PER_PAIRING)]

    process = subprocess.Popen(
        cmd, cwd=FORGE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace"
    )

    log_file_path = os.path.join(draft_folder, f"{draft_name}_detailed.log")
    full_logs = []

    f_log = open(log_file_path, "w", encoding="utf-8") if SAVE_LOGS_TO_FILE else None

    try:
        for line in process.stdout:
            if f_log:
                f_log.write(line)
            if PRINT_LIVE_LOGS:
                print(line, end="")
            full_logs.append(line)
    finally:
        if f_log:
            f_log.close()

    process.wait()

    if SAVE_LOGS_TO_FILE:
        print(f"📄 Log détaillé sauvegardé dans : {log_file_path}")

    analyze_and_print_results("".join(full_logs), draft_name)

def main():
    try:
        forge_jar = find_forge_jar(FORGE_DIR)
    except FileNotFoundError as e:
        print(f"❌ Erreur : {e}")
        return

    draft_folders = sorted(glob.glob(os.path.join(DECKS_BASE_DIR, "draft_*")))

    # Filtrage dynamique (Supporte int, range, list, set et None)
    if TARGET_DRAFTS is not None:
        filtered_folders = []
        for folder in draft_folders:
            match = re.search(r"draft_(\d+)$", folder)
            if match:
                num = int(match.group(1))
                if isinstance(TARGET_DRAFTS, int) and num == TARGET_DRAFTS:
                    filtered_folders.append(folder)
                elif isinstance(TARGET_DRAFTS, (list, tuple, range, set)) and num in TARGET_DRAFTS:
                    filtered_folders.append(folder)
        draft_folders = filtered_folders

    for folder in draft_folders:
        run_draft_tournament(folder, forge_jar)

if __name__ == "__main__":
    main()