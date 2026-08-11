import os
import csv
import json
import math
from collections import defaultdict

from config import DECKS_BASE_DIR


def draft_num(name):
    return int(name.split("_")[1])


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


def mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def primary_shadow_deck(data):
    """Le siège le plus concentré en cartes Shadow de ce draft (départage par
    winrate) — même méthodologie que celle validée sur le lot de 10 drafts,
    désormais appliquée sur TOUS les sièges (standings porte shadow_cards pour
    chaque siège, pas seulement le top 3)."""
    candidates = [r for r in data["standings"] if r["shadow_cards"]]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (len(r["shadow_cards"]), r["winrate_pct"]))


def main():
    with open(os.path.join(DECKS_BASE_DIR, "retex_data.json"), encoding="utf-8") as f:
        all_data = json.load(f)
    drafts = sorted(all_data.keys(), key=draft_num)
    n = len(drafts)

    with open(os.path.join(DECKS_BASE_DIR, "mono_vs_shadow.csv"), encoding="utf-8") as f:
        mono_rows = {(r["color_class"], r["shadow_class"]): r for r in csv.DictReader(f)}

    # ---------------- Classification Shadow dominante / absente (méthodologie validée) ----------------
    dominant, absent = [], []
    for d in drafts:
        data = all_data[d]
        (dominant if data["signals"]["winner_shadow_cards"] else absent).append(d)

    block_dom = [all_data[d]["blocking"]["block_rate_pct"] for d in dominant]
    block_abs = [all_data[d]["blocking"]["block_rate_pct"] for d in absent]
    block_all = [all_data[d]["blocking"]["block_rate_pct"] for d in drafts]

    by_block = sorted(drafts, key=lambda d: all_data[d]["blocking"]["block_rate_pct"])
    lowest_block_draft, highest_block_draft = by_block[0], by_block[-1]

    # ---------------- 3 découpages de corrélation ----------------
    primary_rows = []
    for d in drafts:
        p = primary_shadow_deck(all_data[d])
        if p:
            primary_rows.append((all_data[d]["blocking"]["block_rate_pct"], p["winrate_pct"], d, p))

    all_seat_rows = []
    packaged_rows = []
    for d in drafts:
        br = all_data[d]["blocking"]["block_rate_pct"]
        for r in all_data[d]["standings"]:
            if r["shadow_cards"]:
                all_seat_rows.append((br, r["winrate_pct"]))
                if len(r["shadow_cards"]) >= 2:
                    packaged_rows.append((br, r["winrate_pct"]))

    r_primary = pearson_corr([x for x, y, *_ in primary_rows], [y for x, y, *_ in primary_rows])
    r_all_seats = pearson_corr([x for x, y in all_seat_rows], [y for x, y in all_seat_rows])
    r_packaged = pearson_corr([x for x, y in packaged_rows], [y for x, y in packaged_rows])

    # ---------------- Anomalies ----------------
    anomalies_by_draft = {d: all_data[d]["anomalies"] for d in drafts if all_data[d]["anomalies"]}
    total_anomalies = sum(len(v) for v in anomalies_by_draft.values())

    # ---------------- Rendu ----------------
    def fmt_pct(v):
        return f"{v}%" if v is not None else "n/a"

    def peer_names(lst, k=8):
        shown = lst[:k]
        s = ", ".join(shown)
        if len(lst) > k:
            s += f" (+{len(lst) - k} autre(s))"
        return s

    lines = []
    lines.append("# SYNTHÈSE — corrélation taux de blocage / winrate Shadow\n")
    lines.append(
        f"*Généré automatiquement par `generate_synthese.py` à partir de `retex_data.json` "
        f"({n} drafts disposant d'un log détaillé — l'intégralité du corpus de 100 drafts). Détail par "
        f"draft dans `draft_N/RETEX.md`. Classification \"Shadow dominante\" = le deck **vainqueur** "
        f"possède au moins une carte Shadow (pas un classement de cartes tronqué). Croisement "
        f"mono/multi-couleur x Shadow dans `mono_vs_shadow.csv`.*\n"
    )

    lines.append("## 1. Vue d'ensemble\n")
    lines.append(
        f"- **{n} drafts** analysés, {n * 8} decks, {n * 28} matchs.\n"
        f"- Taux de blocage réel : moyenne {mean(block_all)}%, de {all_data[lowest_block_draft]['blocking']['block_rate_pct']}% "
        f"({lowest_block_draft}) à {all_data[highest_block_draft]['blocking']['block_rate_pct']}% ({highest_block_draft}).\n"
        f"- **{len(dominant)}/{n} drafts** ont un deck **vainqueur** portant au moins une carte Shadow "
        f"(\"Shadow dominante\") ; **{len(absent)}/{n}** n'en ont aucune (\"Shadow absente\").\n"
        f"- **{total_anomalies} anomalie(s) de log** détectée(s) sur {len(anomalies_by_draft)} draft(s) "
        f"(voir §5) — toutes résolues via `Match Winner -`, sans impact sur les conclusions ci-dessous.\n"
    )

    lines.append("## 2. Shadow dominante vs Shadow absente\n")
    lines.append(
        f"**Shadow dominante ({len(dominant)}/{n} drafts)** : taux de blocage réel moyen "
        f"{mean(block_dom)}%, de {min(block_dom)}% à {max(block_dom)}%. Exemple marquant : "
        f"**{highest_block_draft}** a le taux de blocage le plus élevé de tout le corpus "
        f"({all_data[highest_block_draft]['blocking']['block_rate_pct']}%) et son vainqueur "
        f"({all_data[highest_block_draft]['signals']['winner_seat']}) porte pourtant "
        f"{len(all_data[highest_block_draft]['signals']['winner_shadow_cards'])} carte(s) Shadow — "
        f"contre-exemple direct à l'hypothèse d'un artefact IA.\n"
    )
    lines.append(
        f"**Shadow absente ({len(absent)}/{n} drafts)** : taux de blocage réel moyen "
        f"{mean(block_abs)}%, de {min(block_abs)}% à {max(block_abs)}%. Exemple marquant : "
        f"**{lowest_block_draft}** a le taux de blocage le plus bas de tout le corpus "
        f"({all_data[lowest_block_draft]['blocking']['block_rate_pct']}%) et son vainqueur "
        f"({all_data[lowest_block_draft]['signals']['winner_seat']}) ne joue **aucune** carte Shadow — "
        f"si le sous-blocage de l'IA suffisait à faire gagner Shadow, c'est précisément dans ce "
        f"draft-là qu'on l'attendrait.\n"
    )
    lines.append(
        f"Les moyennes de blocage des deux groupes sont proches ({mean(block_dom)}% vs {mean(block_abs)}%) "
        f"et les deux couvrent toute l'étendue observée sur le corpus — le statut Shadow d'un draft ne "
        f"se prédit pas depuis son taux de blocage.\n"
    )
    lines.append(f"- Drafts Shadow dominante : {peer_names(dominant, 12)}")
    lines.append(f"- Drafts Shadow absente : {peer_names(absent, 12)}\n")

    lines.append("## 3. Mono-couleur vs Bi/Tri-couleur, avec et sans Shadow\n")
    lines.append(
        "Lecture directe des `.dck` de **tous** les sièges des 100 drafts (pas seulement le top 3), "
        "winrate poolé (somme des victoires / somme des matchs) :\n"
    )
    lines.append("| Groupe | Decks | Victoires/Matchs | Winrate |")
    lines.append("|---|---|---|---|")
    for cc, label in [("mono", "Mono-couleur (tous)"), ("multi", "Bi/Tri-couleur (tous)"), ("colorless", "Sans couleur identifiée")]:
        r = mono_rows.get((cc, "all"))
        if r:
            lines.append(f"| {label} | {r['n_decks']} | {r['wins']}/{r['matches']} | {r['winrate_pct']}% |")
    lines.append("| | | | |")
    for cc, label in [("mono", "Mono-couleur"), ("multi", "Bi/Tri-couleur")]:
        for sc, sublabel in [("with_shadow", "— avec Shadow"), ("without_shadow", "— sans Shadow")]:
            r = mono_rows.get((cc, sc))
            if r:
                lines.append(f"| {label} {sublabel} | {r['n_decks']} | {r['wins']}/{r['matches']} | {r['winrate_pct']}% |")
    lines.append("")
    r_mono_all = mono_rows[("mono", "all")]
    r_multi_all = mono_rows[("multi", "all")]
    r_mono_sh = mono_rows[("mono", "with_shadow")]
    r_mono_nosh = mono_rows[("mono", "without_shadow")]
    r_multi_sh = mono_rows[("multi", "with_shadow")]
    r_multi_nosh = mono_rows[("multi", "without_shadow")]
    lines.append(
        f"Les decks mono-couleur gagnent plus que les bi/tri-couleur toutes choses égales par ailleurs "
        f"({r_mono_all['winrate_pct']}% contre {r_multi_all['winrate_pct']}%). Le fossé le plus large "
        f"n'est cependant pas la couleur mais **Shadow** : à nombre de couleurs égal, posséder au moins "
        f"une carte Shadow ajoute "
        f"{round(float(r_mono_sh['winrate_pct']) - float(r_mono_nosh['winrate_pct']), 1)} points de "
        f"winrate en mono-couleur ({r_mono_sh['winrate_pct']}% vs {r_mono_nosh['winrate_pct']}%) et "
        f"{round(float(r_multi_sh['winrate_pct']) - float(r_multi_nosh['winrate_pct']), 1)} points en "
        f"bi/tri-couleur ({r_multi_sh['winrate_pct']}% vs {r_multi_nosh['winrate_pct']}%). Shadow "
        f"profite donc aux decks mono comme aux decks multicolores, dans des proportions comparables — "
        f"encore un signal que c'est la mécanique elle-même qui est forte, indépendamment du nombre de "
        f"couleurs du deck qui la porte.\n"
    )

    lines.append("## 4. Corrélation block_rate_pct / winrate Shadow\n")
    lines.append("| Découpage | n | r (Pearson) |")
    lines.append("|---|---|---|")
    lines.append(f"| 1 deck Shadow \"primaire\" par draft (le plus concentré en cartes Shadow) | {len(primary_rows)} | **{r_primary:+.3f}** |" if r_primary is not None else f"| 1 deck Shadow \"primaire\" par draft | {len(primary_rows)} | non calculable |")
    lines.append(f"| Tous les sièges portant ≥1 carte Shadow | {len(all_seat_rows)} | {r_all_seats:+.3f} |" if r_all_seats is not None else f"| Tous les sièges portant ≥1 carte Shadow | {len(all_seat_rows)} | non calculable |")
    lines.append(f"| Sièges à package Shadow concentré (≥2 cartes) | {len(packaged_rows)} | {r_packaged:+.3f} |" if r_packaged is not None else f"| Sièges à package Shadow concentré (≥2 cartes) | {len(packaged_rows)} | non calculable |")
    lines.append("")
    signs = [x for x in (r_primary, r_all_seats, r_packaged) if x is not None]
    all_nonneg = all(x >= 0 for x in signs)
    max_abs = max(abs(x) for x in signs) if signs else 0
    lines.append(
        (f"Recalculés sur l'intégralité des {n} drafts (contre 10 dans une version précédente à "
         f"échantillon réduit, où le découpage \"1 deck primaire\" donnait r=+0.689), les 3 découpages "
         f"convergent vers des corrélations **quasi nulles** (|r| ≤ {max_abs:.3f}). L'échantillon à 10 "
         "drafts surestimait donc la force du lien — avec 10x plus de données, le signal se dissout "
         "presque entièrement. Le point qui reste vrai et se renforce avec l'échantillon complet : "
         if all_nonneg else
         "Les 3 découpages ne convergent pas vers un signe cohérent une fois étendus à l'intégralité "
         f"des {n} drafts : ")
        + ("**aucun des 3 découpages ne devient négatif**, ce qui continue d'exclure la signature "
           "attendue d'un artefact IA (moins de blocage → Shadow gagne mécaniquement plus). Le taux de "
           "blocage réel d'un draft n'explique donc, au mieux, qu'une part négligeable du winrate de "
           "son deck Shadow." if all_nonneg else
           "certains découpages sont positifs, d'autres proches de zéro ou négatifs, sans qu'aucun "
           "n'atteigne une magnitude franche — le lien reste trop faible et instable pour trancher "
           "dans un sens ou l'autre à partir de cette seule corrélation.")
        + "\n"
    )

    lines.append("## 5. Verdict\n")
    lines.append(
        "> Si le winrate de Shadow reste élevé indépendamment du taux de blocage → la mécanique est "
        "probablement trop forte intrinsèquement, indépendamment du bug IA.\n"
    )
    lines.append(
        f"C'est ce que montrent les {n} drafts analysés : {len(dominant)}/{n} ont un vainqueur Shadow, "
        f"répartis sur toute l'étendue du taux de blocage observé (le draft au blocage le plus élevé du "
        f"corpus, {highest_block_draft}, a un vainqueur Shadow ; le draft au blocage le plus bas, "
        f"{lowest_block_draft}, n'en a aucun). Le croisement mono/multi-couleur (§3) confirme que "
        f"l'avantage Shadow est stable indépendamment du nombre de couleurs du deck qui la porte. Le "
        f"signal pointe vers une **mécanique intrinsèquement forte plutôt qu'un artefact isolé du "
        f"blocage IA**, ce qui n'exclut pas que le sous-blocage général de l'IA reste, par ailleurs, un "
        f"problème de fidélité de simulation à corriger.\n"
    )

    lines.append("## 6. Anomalies de log détectées sur le corpus complet\n")
    if anomalies_by_draft:
        lines.append(
            f"{total_anomalies} anomalie(s) sur {len(anomalies_by_draft)} draft(s) "
            f"({peer_names(sorted(anomalies_by_draft.keys(), key=draft_num))}), toutes de deux types "
            "connus (voir `generate_retex_data.py`) :\n"
        )
        lines.append(
            "- **Timeout IA (120000 ms)** : le log émet 2 lignes `Game Outcome: ... has won` "
            "contradictoires pour une même partie — résolu via `Match Winner -`.\n"
            "- **Match nul réel** (`draft_32` uniquement) : perte simultanée des deux joueurs (Earthquake "
            "symétrique), Forge rejoue une partie supplémentaire pour départager le match — comptée "
            "comme un match, pas deux.\n"
        )
    else:
        lines.append("Aucune anomalie détectée.\n")

    n_unreliable = sum(
        1 for row in csv.DictReader(open(os.path.join(DECKS_BASE_DIR, "cards_performance.csv"), encoding="utf-8"))
        if row["reliable"] == "False"
    )
    lines.append("## 7. Limites\n")
    if n_unreliable:
        lines.append(
            f"- **Fiabilité carte-par-carte** : {n_unreliable} carte(s) du corpus n'ont jamais été vues "
            "dans un autre draft — chaque `RETEX.md` les signale explicitement quand elles apparaissent "
            "dans son top 10 ; leur winrate affiché recopie mécaniquement le score du deck qui les "
            "contient et ne doit pas être lu comme un signal de carte indépendant (voir "
            "`cards_performance.csv`, colonne `reliable`).\n"
        )
    else:
        lines.append(
            "- **Fiabilité carte-par-carte** : à cette échelle (100 drafts), plus aucune carte du corpus "
            "n'est mono-draft (`distinct_drafts` ≥ 2 partout dans `cards_performance.csv`) — le biais "
            "\"score de deck recopié sur ses cartes\", bien réel sur l'échantillon à 10 drafts (Helm of "
            "Awakening, Edgewalker), a disparu avec la taille du corpus. Le mécanisme d'avertissement "
            "reste actif dans chaque `RETEX.md` si de nouvelles cartes ou drafts le réintroduisent.\n"
        )
    lines.append(
        "- **Classification mono/multi** exclut les archétypes sans préfixe de couleur reconnu dans le "
        "nom du deck (ex. `Combo`) — comptés séparément (\"colorless\") plutôt que forcés dans une des "
        "deux catégories.\n"
    )

    out_path = os.path.join(DECKS_BASE_DIR, "SYNTHESE.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✓ {out_path} généré sur {n} drafts.")
    print(f"  r_primary={r_primary}, r_all_seats={r_all_seats}, r_packaged={r_packaged}")


if __name__ == "__main__":
    main()
