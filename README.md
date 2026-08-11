# cube-forge-analysis

A small pipeline for turning [CubeCobra](https://cubecobra.com) bot-draft simulations
into real, played-out [Forge](https://github.com/Card-Forge/forge) matches, then
analyzing the results with a few statistical guardrails that are easy to get wrong
(and did, more than once, while building this).

If you design a Magic: The Gathering cube and have ever wondered *"is this card
actually overpowered, or just heavily drafted?"* without wanting to manually play
hundreds of games to find out, this is for that.

## What it does

```
CubeCobra draft export (.csv)
        │  convert_csv.py
        ▼
   .dck files, one per drafted deck, grouped by draft_N/
        │  tournament_simulation.py  (drives Forge's headless `sim` mode)
        ▼
   draft_N/draft_N_detailed.log      (full-verbosity match logs)
        │  analyze_all_drafts.py     (produces cards_performance.csv — see note below)
        │  generate_retex_data.py    (parses every log into retex_data.json)
        ▼
   retex_data.json
        │  generate_retex_reports.py            generate_synthese.py
        ▼                                        ▼
   draft_N/RETEX.md (one report per draft)   SYNTHESE.md (corpus-wide comparison)
```

`analyze_fixing_lands.py` is an example of an ad-hoc follow-up script built on top
of `retex_data.json` for a specific question (see [Case study](#case-study) below)
rather than a core pipeline step — a template for asking your own questions of the
same data.

## Why this exists (the guardrails)

A few mistakes are very easy to make when turning bot-drafted decks and simulated
matches into "which cards/archetypes are actually good" conclusions. This pipeline
specifically guards against the ones we ran into:

- **Single-deck card bias.** In a singleton cube, if a card only appears in one
  drafted deck, that deck's overall win rate gets trivially "copied" onto every
  card it contains. A card that looks like it has an 85% win rate might just be
  sitting in the one deck that went 6-1 for unrelated reasons. `cards_performance.csv`
  tracks `distinct_drafts` per card specifically so this can be filtered out.
- **Ambiguous win-detection in Forge logs.** Forge can emit more than one
  (occasionally contradictory) "who won" line per match, especially around
  AI turn-timeouts. Win detection here is anchored to the single unambiguous
  `Match Result: <seat>: <score> <seat>: <score>` summary line rather than
  pattern-matching narrative log lines.
- **Legal vs. naive blocking rate.** Counting `"didn't block"` log lines
  over-states an AI opponent's passivity, because it doesn't distinguish an empty
  board, a legally-unblockable attacker (evasion the defender has no answer to),
  and a genuine missed block. Board state is reconstructed from the log
  (creature resolution / graveyard events) to separate these three cases before
  computing a blocking rate.
- **Small-sample overconfidence.** A correlation that looks strong on 10 drafts
  can evaporate on 100. Nothing in this pipeline enforces this discipline for
  you — see the case study for what happened when we didn't check.

## Requirements

- Python 3.9+, `pandas`
- A local [Forge](https://github.com/Card-Forge/forge) installation (headless
  `sim` mode support)
- Draft data exported from CubeCobra as a "breakdown" CSV

## Setup

All scripts read their paths from `config.py`, which in turn reads environment
variables (with sensible fallbacks):

```bash
export FORGE_DIR=/path/to/your/forge-installer
export CUBE_DECKS_DIR=/path/to/your/forge-installer/res/my_cube_decks   # optional,
    # defaults to $FORGE_DIR/res/my_cube_decks
```

Or just edit the two defaults at the top of `config.py` directly if you don't
want to set environment variables.

## Usage

```bash
# 1. Convert a CubeCobra draft-breakdown export into per-draft .dck files
python3 convert_csv.py path/to/your-cube-100drafts-breakdown-all.csv

# 2. Play every draft out in Forge (edit TARGET_DRAFTS in tournament_simulation.py
#    to run a subset first — a full 100-draft x 8-seat corpus is a lot of matches)
python3 tournament_simulation.py

# 3. Build the corpus-wide card performance table (needed by step 4)
python3 analyze_all_drafts.py

# 4. Parse every log into one structured JSON file
python3 generate_retex_data.py

# 5. Generate human-readable reports
python3 generate_retex_reports.py   # draft_N/RETEX.md, one per draft
python3 generate_synthese.py        # SYNTHESE.md, corpus-wide comparison
```

Step 3 is easy to assume is a one-off you can skip after the first run — it isn't.
Steps 4 and 5 read `cards_performance.csv` for per-card reliability data, so
re-run step 3 whenever you add drafts or cards to the cube, or that data will
silently go stale.

## Case study

[`shadow_paper.pdf`](./shadow_paper.pdf) uses this pipeline on 100 drafts
(2,800 matches) to test whether the `Shadow` keyword ability was intrinsically
overpowered in one specific cube, or whether its apparent strength was an
artifact of Forge's AI opponent under-utilizing blocking. Short version: an
initial 10-draft sample suggested the latter; replicating at 10x the sample size
overturned that conclusion. LaTeX source included for anyone who wants to see
the full analysis or reuse the structure for their own cube's open questions.

## License

MIT — see [LICENSE](./LICENSE).
