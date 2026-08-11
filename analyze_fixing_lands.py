import os
import re
import glob
import json
import math
import csv
from collections import defaultdict

from config import DECKS_BASE_DIR

# ==============================================================================
# "Fixing Pool" — 10 pain lands + leurs 10 guildgates correspondants.
# AUCUN document ("papier LaTeX", annexe A) définissant cette liste n'a été
# trouvé dans ce projet ni ailleurs sur la machine (recherche exhaustive :
# aucun .tex, aucune occurrence de "fixing pool" ou "annexe A"). Liste ci-
# dessous vérifiée directement dans les .dck du cube (les 20 cartes sont
# effectivement présentes) plutôt que recopiée d'une source introuvable.
# ==============================================================================
FIXING_POOL = {
    "Adarkar Wastes", "Battlefield Forge", "Brushland", "Caves of Koilos",
    "Karplusan Forest", "Llanowar Wastes", "Shivan Reef", "Sulfurous Springs",
    "Underground River", "Yavimaya Coast",
    "Azorius Guildgate", "Boros Guildgate", "Dimir Guildgate", "Golgari Guildgate",
    "Gruul Guildgate", "Izzet Guildgate", "Orzhov Guildgate", "Rakdos Guildgate",
    "Selesnya Guildgate", "Simic Guildgate",
}
UTILITY_LANDS_EXCLUDED_EXAMPLES = {"Wasteland", "Mishra's Factory"}  # non comptées (hors Fixing Pool)


def load_deck_main_cards(dck_path):
    cards = []
    in_main = False
    for line in open(dck_path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line == "[Main]":
            in_main = True
            continue
        if line.startswith("[") and line.endswith("]"):
            in_main = False
            continue
        if in_main and line and not line.startswith("#"):
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                count, name = int(parts[0]), parts[1]
            else:
                count, name = 1, line
            cards.extend([name] * count)
    return cards


def tier_of(n_fixing):
    if n_fixing == 0:
        return "0"
    if n_fixing <= 2:
        return "1-2"
    return "3+"


def pearson_and_pvalue(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None, None, n
    r = cov / math.sqrt(vx * vy)

    df = n - 2
    if df < 1 or abs(r) >= 1:
        return r, (0.0 if abs(r) >= 1 else None), n
    t_stat = r * math.sqrt(df / (1 - r * r))
    # p-value bilatéral via la fonction bêta incomplète régularisée :
    # P(|T| >= |t|) = I_x(df/2, 1/2), x = df / (df + t^2)
    x = df / (df + t_stat * t_stat)
    p = regularized_incomplete_beta(x, df / 2, 0.5)
    return r, p, n


# ---- Fonction bêta incomplète régularisée (fraction continue, Numerical Recipes) ----
def _betacf(x, a, b):
    MAXIT, EPS, FPMIN = 200, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def regularized_incomplete_beta(x, a, b):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(ln_beta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(x, a, b) / a
    else:
        return 1.0 - front * _betacf(1.0 - x, b, a) / b


def main():
    with open(os.path.join(DECKS_BASE_DIR, "retex_data.json"), encoding="utf-8") as f:
        all_data = json.load(f)

    rows = []
    for draft, data in all_data.items():
        for r in data["standings"]:
            if len(r["colors"]) != 2:
                continue
            dck_path = os.path.join(DECKS_BASE_DIR, draft, f"{r['seat']}.dck")
            if not os.path.exists(dck_path):
                print(f"⚠️ .dck introuvable pour {r['seat']} ({draft})")
                continue
            cards = load_deck_main_cards(dck_path)
            n_fixing = sum(1 for c in cards if c in FIXING_POOL)
            rows.append({
                "draft": draft, "seat": r["seat"], "archetype": r["archetype"],
                "colors": "".join(r["colors"]), "n_fixing": n_fixing,
                "wins": r["wins"], "matches": r["matches"], "winrate_pct": r["winrate_pct"],
            })

    print(f"Decks bicolores trouvés : {len(rows)} (sur {sum(len(d['standings']) for d in all_data.values())} decks au total)")

    # ---------------- 1. Winrate poolé par palier ----------------
    tiers = defaultdict(lambda: {"n_decks": 0, "wins": 0, "matches": 0})
    for row in rows:
        t = tier_of(row["n_fixing"])
        g = tiers[t]
        g["n_decks"] += 1
        g["wins"] += row["wins"]
        g["matches"] += row["matches"]

    print("\n=== Winrate poolé par palier de terrains de fixing (decks bicolores) ===")
    print(f"{'Palier':<8} {'n_decks':>8} {'n_matches':>10} {'wins':>6} {'winrate':>9}")
    csv_rows = []
    for t in ("0", "1-2", "3+"):
        g = tiers[t]
        wr = round(g["wins"] / g["matches"] * 100, 2) if g["matches"] else None
        print(f"{t:<8} {g['n_decks']:>8} {g['matches']:>10} {g['wins']:>6} {wr if wr is not None else 'n/a':>9}")
        csv_rows.append({"tier": t, "n_decks": g["n_decks"], "n_matches": g["matches"], "wins": g["wins"], "winrate_pct": wr})

    out_csv = os.path.join(DECKS_BASE_DIR, "fixing_lands_tiers.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["tier", "n_decks", "n_matches", "wins", "winrate_pct"])
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"\n📂 CSV exporté : {out_csv}")

    # ---------------- 2. Corrélation Pearson (r, p-value) ----------------
    xs = [row["n_fixing"] for row in rows]
    ys = [row["winrate_pct"] for row in rows]
    r, p, n = pearson_and_pvalue(xs, ys)
    print(f"\n=== Corrélation Pearson (nombre de terrains de fixing, winrate du deck) ===")
    print(f"n = {n}")
    if r is None:
        print("Non calculable (variance nulle).")
    else:
        print(f"r = {r:+.4f}")
        print(f"p-value (bilatérale) = {p:.4g}" if p is not None else "p-value non calculable")
        alpha = 0.05
        sig = "significatif" if (p is not None and p < alpha) else "non significatif"
        print(f"-> {sig} au seuil {alpha}")

    # ---------------- distribution brute pour transparence ----------------
    dist = defaultdict(int)
    for row in rows:
        dist[row["n_fixing"]] += 1
    print("\n=== Distribution brute du nombre de terrains de fixing (decks bicolores) ===")
    for k in sorted(dist):
        print(f"  {k} terrain(s) de fixing : {dist[k]} deck(s)")

    out_json = os.path.join(DECKS_BASE_DIR, "fixing_lands_raw.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "fixing_pool": sorted(FIXING_POOL),
            "n_bicolor_decks": len(rows),
            "pearson_r": r, "pearson_p_value": p,
            "tiers": csv_rows,
            "decks": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"📂 Détail brut exporté : {out_json}")


if __name__ == "__main__":
    main()
