# cube-forge-analysis

A small pipeline for turning [CubeCobra](https://cubecobra.com) bot-draft simulations
into real, played-out [Forge](https://github.com/Card-Forge/forge) matches, then
analyzing the results with a few statistical guardrails that are easy to get wrong
(and did, more than once, while building this).

If you design a Magic: The Gathering cube and have ever wondered *"is this card
actually overpowered, or just heavily drafted?"* without wanting to manually play
hundreds of games to find out, this is for that. Nothing in the pipeline is specific
to any particular card, mechanic, or cube — it works on any CubeCobra export.

## What it does

```
CubeCobra draft-breakdown export (.csv)
        │  convert_csv.py
        ▼
   draft_N/*.dck                          (one Forge deck file per drafted seat)
        │  simulate.py       (drives Forge's headless `sim` mode)
        ▼
   draft_N/draft_N_detailed.log           (full-verbosity match logs)
        │  analyze.py        (single pass: parses every log)
        ▼
   analysis.json + cards.csv              (structured results, corpus-wide)
        │  report.py
        ▼
   draft_N/REPORT.md  (per draft)   +   SUMMARY.md  (corpus-wide)
```

Five files, four pipeline stages, run in order (`config.py` holds shared settings
and isn't run directly).

## Why this exists (the guardrails)

A few mistakes are very easy to make when turning bot-drafted decks and simulated
matches into "which cards/archetypes are actually good" conclusions. This pipeline
specifically guards against the ones we ran into building it:

- **Single-deck card bias.** In a singleton cube, if a card only appears in one
  drafted deck, that deck's overall win rate gets trivially "copied" onto every
  card it contains. A card that looks like it has an 85% win rate might just be
  sitting in the one deck that went 6-1 for unrelated reasons. `cards.csv` tracks
  `distinct_drafts` per card specifically so this can be filtered out, and
  `report.py` flags it wherever it would otherwise mislead.
- **Ambiguous win-detection in Forge logs.** Several individually-plausible ways
  of detecting a match winner from Forge's log turn out to be insufficient at
  scale: a genuine simultaneous double-loss makes Forge replay a game, and an AI
  turn-timeout can emit two contradictory "has won" lines for the same game. Win
  detection here is anchored to `Round N - p1 vs p2` (identifies the pairing) +
  `Match Winner -` (closes it) — verified exactly once per pairing with no
  duplicates or omissions across a 2,800-match corpus. See the header of
  `analyze.py` for the full reasoning.
- **Legal vs. naive blocking rate.** Counting `"didn't block"` log lines
  over-states an AI opponent's passivity, because it doesn't distinguish an empty
  board, a legally-unblockable attacker (evasion the defender has no answer to),
  and a genuine missed block. Board state is reconstructed from the log
  (creature resolution / graveyard events) to separate these three cases before
  computing a blocking rate.
- **Small-sample overconfidence.** A correlation that looks strong on 10 drafts
  can evaporate on 100. Nothing in this pipeline enforces this discipline for
  you — that's on whoever's running it.
- **No sideboard access.** All matches are best-of-one by default
  (`MATCHES_PER_PAIRING` in `simulate.py`). A card that only earns its keep in a
  specific matchup is played in *every* game if maindecked, regardless of
  opponent — this can drag its measured winrate down relative to how it performs
  when actually relevant. Testing found no evidence Forge's headless AI swaps
  cards between games of a match, so raising `MATCHES_PER_PAIRING` alone is
  unlikely to fix this.

## Requirements

- Python 3.9+, `pandas`
- A local [Forge](https://github.com/Card-Forge/forge) installation (headless
  `sim` mode support)
- Draft data exported from CubeCobra as a "breakdown" CSV — from any public or
  private cube, yours or someone else's

## Setup

All scripts read their paths from `config.py`, which reads environment variables
(with sensible fallbacks):

```bash
export FORGE_DIR=/path/to/your/forge-installer
export CUBE_DECKS_DIR=/path/to/your/forge-installer/res/my_cube_decks   # optional,
    # defaults to $FORGE_DIR/res/my_cube_decks
```

Or just edit the two defaults at the top of `config.py` if you'd rather not use
environment variables.

## Usage

```bash
# 1. Convert a CubeCobra draft-breakdown export into per-draft .dck files
python3 convert_csv.py path/to/your-cube-100drafts-breakdown-all.csv

# 2. Play every draft out in Forge (edit TARGET_DRAFTS in simulate.py to run a
#    subset first — a full 100-draft x 8-seat corpus is a lot of matches)
python3 simulate.py

# 3. Parse every log into structured results (one pass, produces both
#    analysis.json and cards.csv — no separate step, no ordering to remember)
python3 analyze.py

# 4. Generate human-readable reports
python3 report.py   # draft_N/REPORT.md (one per draft) + SUMMARY.md (corpus-wide)
```

Re-run steps 3–4 whenever you add drafts or cards to the cube — `cards.csv`'s
per-card reliability is corpus-wide and goes stale otherwise.

## Output files

| File | Produced by | Contents |
|---|---|---|
| `draft_N/*.dck` | `convert_csv.py` | One deck per drafted seat |
| `draft_N/draft_N_detailed.log` | `simulate.py` | Full Forge match log |
| `analysis.json` | `analyze.py` | Structured per-draft results: standings, blocking breakdown, per-game duration/loss reasons, this-draft's top cards, anomalies, local signals |
| `cards.csv` | `analyze.py` | Corpus-wide per-card performance + reliability flag |
| `draft_N/REPORT.md` | `report.py` | Human-readable report for one draft |
| `SUMMARY.md` | `report.py` | Archetype performance, mono/multi-color split, top/bottom cards, anomalies, limitations — across the whole corpus |

## License

MIT — see [LICENSE](./LICENSE).
