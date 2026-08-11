import json
import os

from config import DECKS_BASE_DIR

MAX_PEERS_SHOWN = 5  # nb de drafts cités nommément dans la phrase "comme draft_X et draft_Y..."


def draft_num(name):
    return int(name.split("_")[1])


# ==============================================================================
# SIGNAUX DE CORPUS — calculés une seule fois, après chargement de tous les
# drafts. Complètent les signaux LOCAUX déjà présents dans data["signals"]
# (calculés par draft, dans generate_retex_data.py, à partir du reste de la
# table de CE draft uniquement).
# ==============================================================================
def compute_corpus_signals(all_data):
    n = len(all_data)
    by_block = sorted(all_data.items(), key=lambda kv: kv[1]["blocking"]["block_rate_pct"])
    block_rank = {}
    for i, (draft, _) in enumerate(by_block, 1):
        percentile = round((i - 1) / (n - 1) * 100, 1) if n > 1 else 50.0
        block_rank[draft] = {"rank_asc": i, "percentile_low_to_high": percentile}

    shadow_dominant = {d: bool(dd["signals"]["winner_shadow_cards"]) for d, dd in all_data.items()}

    peers = {}
    for draft in all_data:
        status = shadow_dominant[draft]
        peers[draft] = sorted(
            [d for d in all_data if d != draft and shadow_dominant[d] == status],
            key=draft_num,
        )

    return {
        "n_drafts": n,
        "block_rank": block_rank,
        "shadow_dominant": shadow_dominant,
        "peers": peers,
    }


def peer_list_phrase(peers):
    if not peers:
        return None
    shown = peers[:MAX_PEERS_SHOWN]
    names = ", ".join(shown)
    if len(peers) > MAX_PEERS_SHOWN:
        names += f" (+{len(peers) - MAX_PEERS_SHOWN} autre(s))"
    return names


def fmt_turns(v):
    return f"{v}" if v is not None else "—"


# ==============================================================================
# RENDU DES TABLES
# ==============================================================================
def render_standings(data):
    has_draws = any(data["duration"][s["seat"]]["draws"] for s in data["standings"])
    header = "| Rang | Deck | Archétype | V-D | Winrate | Durée moy. (tours) | Défaites PV | Défaites Meule | Défaites autre |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    if has_draws:
        header += " Parties nulles |"
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
            "\n\n*Au moins une paire de ce draft a connu un vrai match nul (perte simultanée des "
            "deux joueurs) — Forge rejoue une partie supplémentaire pour départager le match, ce qui "
            "explique un nombre de \"games\" total supérieur à `matches` pour les sièges concernés "
            "(détail en section Anomalies).*"
        )
    return table


def render_blocking(data):
    b = data["blocking"]
    lines = [
        "| Catégorie | Occurrences | % du total |",
        "|---|---|---|",
        f"| `real_block` (blocage réel) | {b['real_block']} | {b['pct_real_block']}% |",
        f"| `real_nonblock` (non-blocage réel, évitable) | {b['real_nonblock']} | {b['pct_real_nonblock']}% |",
        f"| `evasion_legal` (Shadow/Vol sans parade légale) | {b['evasion_legal']} | {b['pct_evasion_legal']}% |",
        f"| `empty_board` (aucun bloqueur possible) | {b['empty_board']} | {b['pct_empty_board']}% |",
        f"| **Total occasions d'attaque** | **{b['total_events']}** | 100% |",
    ]
    return "\n".join(lines) + (
        f"\n\n**Taux de blocage réel = real_block / (real_block + real_nonblock) "
        f"= {b['real_block']}/{b['real_block'] + b['real_nonblock']} = {b['block_rate_pct']}%**"
    )


def render_top3_shadow(data):
    if not data["top3_shadow"]:
        return "Aucun deck du top 3 ne joue de carte Shadow dans ce draft."
    lines = []
    for entry in data["top3_shadow"]:
        lines.append(f"- **#{entry['rank']} {entry['seat']}** :")
        for c in entry["shadow_cards"]:
            rel = "" if c["reliable"] else " ⚠️ *winrate peu fiable — voir avertissement plus bas*"
            lines.append(
                f"  - {c['card_name']} — winrate global {c['global_winrate_pct']}% "
                f"({c['global_games_played']} matchs, vue dans {c['distinct_drafts']} draft(s) distinct(s)){rel}"
            )
    return "\n".join(lines)


def render_top_cards(data):
    lines = [
        "| Carte | Winrate (ce draft) | Matchs | Fiabilité globale |",
        "|---|---|---|---|",
    ]
    any_warning = False
    for c in data["draft_top_cards"][:10]:
        if c["global_reliable"]:
            fiab = f"OK ({c['global_distinct_drafts']} drafts distincts)"
        else:
            fiab = "⚠️ **1 seul draft** (ce chiffre recopie le score du deck)"
            any_warning = True
        lines.append(f"| {c['card_name']} | {c['winrate_pct']}% | {c['wins']}/{c['matches']} | {fiab} |")
    table = "\n".join(lines)
    if any_warning:
        note = (
            "\n\n⚠️ **Avertissement méthodologique** : au moins une carte ci-dessus n'a jamais été "
            "vue dans un autre draft du corpus analysé. Dans un cube en draft (une seule copie par "
            "carte), son \"winrate\" pour ce draft est mathématiquement identique au bilan du deck qui "
            "la contient — ce n'est pas un signal de performance indépendant de la carte elle-même, "
            "juste le score du deck recopié sur chacune de ses cartes."
        )
    else:
        note = (
            "\n\nAucune carte du top 10 de ce draft n'est un cas de \"score de deck recopié\" isolé : "
            "toutes ont été vues dans au moins 2 drafts distincts du corpus (colonne `distinct_drafts` "
            "de `cards_performance.csv`)."
        )
    return table + note


def render_anomalies(data):
    if not data["anomalies"]:
        return "Aucune anomalie détectée dans le parsing de ce log."
    return "\n".join(f"- {a}" for a in data["anomalies"])


# ==============================================================================
# INTERPRÉTATION — un seul mécanisme, appliqué uniformément à tous les drafts.
# Combine signaux LOCAUX (data["signals"]) et signaux DE CORPUS (corpus, calculés
# une fois tous les drafts chargés) en 4-5 phrases, pas une reformulation brute
# des chiffres : chaque phrase sélectionne le fait le plus explicatif de sa
# catégorie (vitesse, contestation de couleur, Shadow, position dans le corpus).
# ==============================================================================
def render_interpretation(data, corpus):
    draft = data["draft"]
    sig = data["signals"]
    sentences = []

    # --- 1. Vainqueur ---
    sentences.append(
        f"Le draft est remporté par {sig['winner_seat']} ({sig['winner_archetype']}, "
        f"{sig['winner_wins']}-{sig['winner_matches'] - sig['winner_wins']})."
    )

    # --- 2. Vitesse locale (vs le reste de CE draft) ---
    wt, ft = sig["winner_avg_turns"], sig["field_avg_turns_excl_winner"]
    if wt is not None and ft is not None:
        delta = round(ft - wt, 1)
        if abs(delta) < 0.5:
            sentences.append(
                f"Sa vitesse de jeu est proche du reste de la table ({wt} tours en moyenne contre "
                f"{ft} pour les autres decks) : la victoire ne s'explique pas par une curve inhabituelle."
            )
        elif delta > 0:
            sentences.append(
                f"Il tourne nettement plus vite que le reste de la table ({wt} tours en moyenne contre "
                f"{ft} pour les autres decks, soit {delta} tours d'avance)."
            )
        else:
            sentences.append(
                f"Il est au contraire plus lent que le reste de la table ({wt} tours en moyenne contre "
                f"{ft} pour les autres decks) : la victoire ne tient pas à une curve agressive."
            )

    # --- 3. Contestation de couleur locale ---
    if sig["winner_colors"]:
        colors_str = "/".join(sig["winner_colors"])
        n = sig["decks_sharing_winner_color"]
        if n == 0:
            sentences.append(f"Sa couleur ({colors_str}) n'est jouée par aucun autre deck du draft : c'est un axe totalement ouvert.")
        elif n == 1:
            sentences.append(f"Sa couleur ({colors_str}) est aussi jouée par 1 autre deck du draft — pas totalement ouvert, mais peu contesté.")
        else:
            sentences.append(f"Sa couleur ({colors_str}) est aussi jouée par {n} autres decks du draft : la victoire ne tient pas à une couleur ouverte.")

    # --- 4. Shadow chez le vainqueur + peers de corpus ---
    shadow_dominant = corpus["shadow_dominant"][draft]
    peers = corpus["peers"][draft]
    peer_phrase = peer_list_phrase(peers)
    if shadow_dominant:
        cards_str = ", ".join(sig["winner_shadow_cards"])
        base = f"Le deck vainqueur porte {len(sig['winner_shadow_cards'])} carte(s) Shadow ({cards_str})."
        if peer_phrase:
            base += f" C'est aussi le cas de {len(peers)} autre(s) draft(s) du corpus, comme {peer_phrase}."
        else:
            base += " C'est le seul draft du corpus dans ce cas."
        sentences.append(base)
    else:
        base = "Le deck vainqueur ne joue aucune carte Shadow."
        if peer_phrase:
            base += f" Comme {len(peers)} autre(s) draft(s) du corpus (ex. {peer_phrase}), Shadow n'a pas déterminé l'issue ici."
        else:
            base += " C'est le seul draft du corpus où Shadow est absente du deck vainqueur."
        sentences.append(base)

    # --- 5. Position du taux de blocage dans le corpus ---
    br = data["blocking"]["block_rate_pct"]
    rk = corpus["block_rank"][draft]
    n = corpus["n_drafts"]
    pct = rk["percentile_low_to_high"]
    if rk["rank_asc"] == 1:
        pos = f"le plus bas des {n} drafts analysés"
    elif rk["rank_asc"] == n:
        pos = f"le plus élevé des {n} drafts analysés"
    else:
        pos = f"{rk['rank_asc']}e taux de blocage le plus bas sur {n} drafts analysés (percentile {pct}%)"
    sentences.append(f"Son taux de blocage réel ({br}%) est {pos}.")

    # --- 6. Anomalies, le cas échéant ---
    if data["anomalies"]:
        n_a = len(data["anomalies"])
        sentences.append(
            f"À noter : {n_a} anomalie(s) de log détectée(s) et corrigée(s) via `Match Winner -` "
            f"(voir section Anomalies) — sans lien avec le résultat du vainqueur sauf mention contraire."
        )

    return " ".join(sentences)


def render_report(data, corpus):
    draft = data["draft"]
    return f"""# RETEX — {draft}

*Généré automatiquement à partir de `{draft}/{draft}_detailed.log` par `generate_retex_data.py` \
+ `generate_retex_reports.py`, sur l'ensemble des {corpus['n_drafts']} drafts disposant d'un log \
détaillé. Détection de victoire basée sur `Round N - p1 vs p2` + `Match Winner -` (seule source \
vérifiée exacte 1x/paire sur tout le corpus — voir note en bas de `generate_retex_data.py`).*

## 1. Classement des decks

{render_standings(data)}

## 2. Répartition du taux de blocage (ce draft précis)

{render_blocking(data)}

## 3. Cartes Shadow dans le top 3

{render_top3_shadow(data)}

## 4. Interprétation

{render_interpretation(data, corpus)}

## 5. Top cartes de ce draft — fiabilité du signal

{render_top_cards(data)}

## Anomalies de log détectées

{render_anomalies(data)}
"""


def main():
    with open(os.path.join(DECKS_BASE_DIR, "retex_data.json"), encoding="utf-8") as f:
        all_data = json.load(f)

    corpus = compute_corpus_signals(all_data)

    for draft, data in sorted(all_data.items(), key=lambda kv: draft_num(kv[0])):
        out_path = os.path.join(DECKS_BASE_DIR, draft, "RETEX.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(render_report(data, corpus))

    print(f"✓ {len(all_data)} RETEX.md générés (draft_1 à draft_{max(draft_num(d) for d in all_data)}).")
    print(f"  Shadow dominante chez le vainqueur : {sum(corpus['shadow_dominant'].values())}/{corpus['n_drafts']} drafts.")


if __name__ == "__main__":
    main()
