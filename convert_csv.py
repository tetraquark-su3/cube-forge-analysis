"""
convert_csv.py — converts a CubeCobra draft-breakdown CSV export into
per-draft Forge .dck files (draft_N/D{draft}_S{seat}_{archetype}.dck).

Card names containing a comma (e.g. "Squee, Goblin Nabob", "Karn, Silver
Golem") need special handling: CubeCobra's CSV separates cards with commas
too, so a naive split() breaks these into two fake half-cards, which Forge
will then reject as unknown cards. Rather than hand-maintaining a list of
"known" comma-cards (which silently breaks again the next time a new one is
drafted), this reads Forge's own card database and greedily re-merges any
two consecutive fragments that match a real card name.
"""
import os
import zipfile
import argparse
import pandas as pd
from collections import Counter

from config import DECKS_BASE_DIR, FORGE_DIR


def load_known_card_names(forge_dir):
    names = set()
    res_dir = os.path.join(forge_dir, "res")
    if not os.path.exists(res_dir):
        print(f"⚠️  {res_dir} not found — comma-card detection disabled "
              f"(falling back to a plain comma split).")
        return names

    def extract_name(lines):
        for line in lines:
            line = line.strip()
            if line.startswith("Name:"):
                return line.split("Name:", 1)[1].strip()
        return None

    for root, _, files in os.walk(res_dir):
        for file in files:
            filepath = os.path.join(root, file)
            if file.endswith(".zip") and "cards" in file.lower():
                try:
                    with zipfile.ZipFile(filepath) as z:
                        for zn in z.namelist():
                            if zn.endswith(".txt"):
                                with z.open(zn) as f:
                                    lines = [l.decode("utf-8", errors="replace") for l in f.readlines()]
                                    n = extract_name(lines)
                                    if n:
                                        names.add(n)
                except Exception:
                    continue
            elif file.endswith(".txt"):
                try:
                    with open(filepath, encoding="utf-8", errors="replace") as f:
                        n = extract_name(f.readlines())
                        if n:
                            names.add(n)
                except Exception:
                    continue
    return names


def parse_card_list(raw_value, known_names=None):
    if pd.isna(raw_value) or str(raw_value).strip() in ["nan", ""]:
        return []

    val = str(raw_value).strip()
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]

    if "\n" in val:
        cards_raw = [c.strip() for c in val.split("\n") if c.strip()]
    else:
        cards_raw = [c.strip() for c in val.split(",") if c.strip()]

    if not known_names:
        return cards_raw

    cleaned = []
    i = 0
    while i < len(cards_raw):
        merged = False
        if i + 1 < len(cards_raw):
            candidate = f"{cards_raw[i]}, {cards_raw[i + 1]}"
            if candidate in known_names:
                cleaned.append(candidate)
                i += 2
                merged = True
        if not merged:
            cleaned.append(cards_raw[i])
            i += 1
    return cleaned


def export_draft_decks(csv_path, base_dir, forge_dir):
    print(f"--> Reading {csv_path}")
    if not os.path.exists(csv_path):
        print(f"❌ File not found: {csv_path}")
        return

    known_names = load_known_card_names(forge_dir)
    print(f"--> {len(known_names)} card names loaded from Forge's database "
          f"({'comma-card detection active' if known_names else 'detection disabled'}).")

    df = pd.read_csv(csv_path)
    print(f"--> {len(df)} rows found.")
    os.makedirs(base_dir, exist_ok=True)

    created = 0
    for idx, row in df.iterrows():
        draft_id = row.get("Draft", idx)
        seat_id = row.get("Seat", "0")
        archetype = str(row.get("Archetype", "Deck")).replace("/", "_").replace(" ", "_")
        deck_name = f"D{draft_id}_S{seat_id}_{archetype}"

        main_list = parse_card_list(row.get("Mainboard", ""), known_names)
        side_list = parse_card_list(row.get("Sideboard", ""), known_names)
        main_counts = Counter(main_list)
        side_counts = Counter(side_list)

        lines = ["[metadata]", f"Name={deck_name}", "[Main]"]
        lines += [f"{count} {card}" for card, count in main_counts.items()]
        lines.append("[Sideboard]")
        lines += [f"{count} {card}" for card, count in side_counts.items()]

        draft_folder = os.path.join(base_dir, f"draft_{draft_id}")
        os.makedirs(draft_folder, exist_ok=True)
        with open(os.path.join(draft_folder, f"{deck_name}.dck"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        created += 1

    print(f"✔ Done: {created} decks written under {os.path.abspath(base_dir)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert a CubeCobra draft-breakdown CSV export into per-draft Forge .dck files."
    )
    parser.add_argument("csv_path", help="Path to the CubeCobra '...breakdown-all.csv' export")
    parser.add_argument(
        "--decks-dir", default=DECKS_BASE_DIR,
        help="Output directory (defaults to CUBE_DECKS_DIR / config.py)",
    )
    args = parser.parse_args()
    export_draft_decks(args.csv_path, args.decks_dir, FORGE_DIR)
