"""
simulate.py — plays out every draft_N/*.dck pod in Forge's headless `sim`
mode and saves the full match log to draft_N/draft_N_detailed.log.

Run analyze.py afterwards to turn those logs into structured results — this
script deliberately doesn't print its own win/loss summary. An earlier
version did, using a different (less robust) win-detection method than
analyze.py; keeping two independent implementations of the same logic around
is exactly the kind of thing that quietly drifts out of sync, so there's only
one now.
"""
import os
import glob
import shutil
import subprocess

from config import FORGE_DIR, DECKS_BASE_DIR

FORGE_CONSTRUCTED_DIR = os.path.expanduser("~/.forge/decks/constructed")

MATCHES_PER_PAIRING = 1   # Best-of-[N] per pairing. 1 = single game. See README
                           # for why this pipeline hasn't found evidence Forge's
                           # AI sideboards between games of a best-of-three+.
TARGET_DRAFTS = None       # None = all draft_N folders found. Or an int, list,
                           # tuple, range, or set of draft numbers to restrict to.
SAVE_LOGS_TO_FILE = True
PRINT_LIVE_LOGS = False    # True prints Forge's raw output as it plays; usually
                           # too noisy to be useful across many drafts.


def find_forge_jar(forge_dir):
    jars = glob.glob(os.path.join(forge_dir, "forge-gui-desktop-*.jar"))
    if not jars:
        raise FileNotFoundError(f"No forge-gui-desktop-*.jar found in {forge_dir}")
    return jars[0]


def run_draft_tournament(draft_folder, jar_path):
    draft_name = os.path.basename(draft_folder)
    deck_files = sorted(glob.glob(os.path.join(draft_folder, "*.dck")))
    if len(deck_files) < 2:
        print(f"⚠️  Not enough decks in {draft_name}, skipping.")
        return

    os.makedirs(FORGE_CONSTRUCTED_DIR, exist_ok=True)
    deck_names_cli = []
    for dck_file in deck_files:
        filename = os.path.basename(dck_file)
        shutil.copy2(dck_file, os.path.join(FORGE_CONSTRUCTED_DIR, filename))
        deck_names_cli.append(os.path.splitext(filename)[0])

    print(f"🚀 {draft_name}: {len(deck_names_cli)} decks, best-of-{MATCHES_PER_PAIRING}")

    cmd = ["java", "-Xmx4096m", "-jar", jar_path, "sim", "-d"] + deck_names_cli + \
          ["-t", "RoundRobin", "-m", str(MATCHES_PER_PAIRING)]

    process = subprocess.Popen(
        cmd, cwd=FORGE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    log_file_path = os.path.join(draft_folder, f"{draft_name}_detailed.log")
    f_log = open(log_file_path, "w", encoding="utf-8") if SAVE_LOGS_TO_FILE else None
    try:
        for line in process.stdout:
            if f_log:
                f_log.write(line)
            if PRINT_LIVE_LOGS:
                print(line, end="")
    finally:
        if f_log:
            f_log.close()
    process.wait()

    if SAVE_LOGS_TO_FILE:
        print(f"   📄 {log_file_path}")


def matches_target(num, target):
    if target is None:
        return True
    if isinstance(target, int):
        return num == target
    return num in target


def main():
    try:
        forge_jar = find_forge_jar(FORGE_DIR)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return

    draft_folders = sorted(glob.glob(os.path.join(DECKS_BASE_DIR, "draft_*")))
    if TARGET_DRAFTS is not None:
        import re
        draft_folders = [
            f for f in draft_folders
            if (m := re.search(r"draft_(\d+)$", f)) and matches_target(int(m.group(1)), TARGET_DRAFTS)
        ]

    if not draft_folders:
        print(f"❌ No matching draft_N folders found under {DECKS_BASE_DIR}.")
        return

    for folder in draft_folders:
        run_draft_tournament(folder, forge_jar)

    print(f"\n✓ Done. Run analyze.py next to turn these logs into results.")


if __name__ == "__main__":
    main()
