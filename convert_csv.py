import os
import argparse
import pandas as pd
from collections import Counter

from config import DECKS_BASE_DIR


def export_draft_decks(csv_path, base_dir):
    df = pd.read_csv(csv_path)
    
    for _, row in df.iterrows():
        draft_id = row['Draft']
        seat_id = row['Seat']
        archetype = str(row['Archetype']).replace("/", "_").replace(" ", "_")
        
        # Nom du deck unique
        deck_name = f"D{draft_id}_S{seat_id}_{archetype}"
        
        # Nettoyage et décompte des cartes
        main_list = [c.strip() for c in str(row['Mainboard']).split(',') if c.strip()]
        side_list = [c.strip() for c in str(row['Sideboard']).split(',') if c.strip() and str(c).lower() != 'nan']
        
        main_counts = Counter(main_list)
        side_counts = Counter(side_list)
        
        # Structure .dck Forge
        lines = ["[metadata]", f"Name={deck_name}", "[Main]"]
        for card, count in main_counts.items():
            lines.append(f"{count} {card}")
            
        lines.append("[Sideboard]")
        for card, count in side_counts.items():
            lines.append(f"{count} {card}")
            
        # Organisation dans des sous-dossiers /draft_X/
        draft_folder = os.path.join(base_dir, f"draft_{draft_id}")
        os.makedirs(draft_folder, exist_ok=True)
        
        file_path = os.path.join(draft_folder, f"{deck_name}.dck")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            
    print(f"✔ Export terminé : Decks rangés par dossiers dans {base_dir}")

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
    export_draft_decks(args.csv_path, args.decks_dir)