# two_words

A Python + PostgreSQL data pipeline that analyzes NBA game logs to compute a **LOCK / HOLD / PASS** recommendation for game-selection fantasy basketball formats (e.g. Sleeper) — after a player's game, should you lock in that score, or hold for a potentially better one later in the scoring week?

> The name is an homage to our Sleeper fantasy league's name.

## Project Status

**v1.0–v1.2 shipped (8/11/26).** The decision engine is live, validated, and updateable in real time:

- Core decision logic calibrated via a proper train/validate backtest, not just tuned on training data
- Two real corrections tested against real outcomes — one shipped (back-to-back fatigue), one correctly rejected after failing a targeted backtest (injury-return penalty) — see `methodology_notes.md` for the full story on why a negative result there was the right outcome, not a failure
- Daily `nba_api` ingestion (box scores, schedule, scoreboard) keeps the database current without manual backfilling, verified end-to-end and spot-checked at scale
- A callable input model (`scripts/lock_decision_input.py`) checks the database first and only computes live when a game genuinely isn't loaded yet

**v2.0 closed (8/12/26), exhaustively negative.** Tested team-level context (pace, ratings, opponent defense, home/away) as a layer on the player-specific model — every candidate signal either failed significance or collapsed once confounds (schedule position, team composition) were controlled for. No team-level signal cleared the bar for production. Full test log in `methodology_notes.md`.

**v3.0 shipped (8/15/26): Sleeper league integration.** All 9 roadmap steps complete — raw ingestion, crosswalk, scoring-settings-as-config, roster/transaction/matchup tables, full relationship diagram, opponent threat scouting, and waiver-wire target finding. The 2025-26 historical-standings data-reliability issue (see **Known Limitations** below) is resolved via manual re-entry, hand-verified against the app's real record — full verification writeup in `docs/step6_verification_results.md`.

## Known limitation: 2025-26 historical matchup points (Lock-In Mode)

This league runs Sleeper's **Lock-In Mode** (one game per player per
week counts, manually selected or auto-defaulted to the week's final
game). For the 2025-26 season specifically, Sleeper's public API
returned historical weekly point values that didn't reliably match the
league's real record for weeks 1-18, and — confirmed via an
independently-built verification script — the API itself returned
inconsistent values across separate calls for the SAME historical
week. 2024-25 data was unaffected on the API side and matched the real
record exactly on every automated check.

**Root cause not confirmed.** Six theories tested and ruled out with
direct evidence (roster changes, transaction volume, IR/taxi moves,
season status, and a from-scratch independent re-verification that
shares no code with the main pipeline). Full investigation log,
including exactly what was tested and what's still open, in
`SLEEPER_LOCKIN_METHODOLOGY.md`.

**Resolved practically (8/15/26):** all affected 2025-26 weeks were
manually re-entered from the app's real Schedule screens and hand-
verified. A full audit against the real app's League History pages
caught and fixed 6 total discrepancies across both seasons — 2
manual-entry typos and 4 genuine, previously-undetected errors in what
had been assumed fully correct 2024-25 data. Both completed seasons
now match the real app exactly on wins/losses/ties/points for/against.
Full comparison table: `docs/step6_verification_results.md`.

## Overview

In game-selection formats, managers must decide after a player's game whether to **lock** in that score or **hold** for a potentially better one later in the scoring week. This project compares a completed game's result against a player's own self-relative ceiling (`GREATEST(35, their mean + 0.5*their stddev)`) — not a flat league-wide bar — to flag it as **LOCK**, **HOLD**, or **PASS**.

## Tech Stack

- **Language:** Python 3.13
- **Database:** PostgreSQL
- **Version Control:** Git & GitHub CLI (`gh`)

## Data Sources

- **`nba_api`** (stats.nba.com) — historical and daily player/team stats, game logs, box scores, schedule, and scoreboard data. Core engine. Currently on `BoxScoreTraditionalV3`/`ScoreboardV3` — both V2 predecessors were confirmed to return no real data for the 2025-26 season.
- **Sleeper API** (`api.sleeper.app`, free, no auth) — league settings, rosters, transactions, and matchup structure, converting raw stats into actual fantasy points per this league's rules and identifying who's rostered/who's facing whom each week. Fully integrated as of v3.0. Own-computed weekly points are independently verified against real Sleeper scores; Sleeper's own historical points data had one known reliability gap — see Known Limitations — now resolved via manual re-entry.
- **`nbainjuries`** (github.com/mxufc29/nbainjuries) — official NBA injury/rest report data, cross-referenced daily to flag whether an absence was injury-explained or a coach's decision.

Back-to-back and home/away context are derived from `nba_api` schedule data directly.

## Known Limitations

**2025-26 historical matchup points (Sleeper Lock-In Mode) — resolved via manual re-entry.** This league runs Sleeper's Lock-In Mode (one game per player per week counts, manually selected or auto-defaulted). For 2025-26 weeks 1-18 specifically, Sleeper's public API returned historical point values that didn't reliably match the league's real record, and an independent, from-scratch verification script confirmed the API itself returned inconsistent values across separate calls for the same historical week. Root cause not confirmed after six tested and ruled-out theories — full investigation log: `docs/SLEEPER_LOCKIN_METHODOLOGY.md`. The affected data has since been manually re-entered and hand-verified against the app's real record; both 2024-25 and 2025-26 now match exactly on wins/losses/ties/points for/against — full comparison: `docs/step6_verification_results.md`.

## Folder Structure

```text
two_words/
├── cleaning_logs/   # Ambiguous name-matching logs from build_gap_reasons.py, for manual review
├── config/          # Environment and connection settings (DB credentials, constants)
├── data/            # Raw and processed data files (ignored by git where applicable)
├── docs/            # Project documentation, diagrams, methodology_notes.md, SLEEPER_LOCKIN_METHODOLOGY.md,
│                     step6_verification_results.md, patch_list.md, architecture_risks.md
├── models/          # The deployed decision-engine schema (ownable_player_pool, player_tiers,
│                     percentage_to_lock, game_lock_signal, player_injury_return_flags,
│                     weekly_outcome_simulation)
├── notebooks/       # Exploratory analysis and prototyping (Jupyter)
├── schema/          # Supporting schema: tables/ (raw + Sleeper ingestion), views/ (Sleeper-derived
│                     views: crosswalk, roster/transaction/matchup, historical standings, playoff
│                     bracket, transaction summary), fixes/ (one-time migrations), analysis/
├── scripts/         # ETL, daily ingestion (nba_api + Sleeper), calibration, and the callable
│                     decision-engine input; subfoldered into ingestion/, sleeper/, analysis/
└── tests/           # tests/injuries/ and tests/retired/ hold superseded/rejected experiments
                       kept as a record; live tests run from tests/ directly
```

## Getting Started

Development environment: VS Code on macOS (Apple Silicon), Python 3.13 via `pyenv`, PostgreSQL via Homebrew. DB connection settings live in `config/.env` (gitignored) and are read by `scripts/db_connection.py`.

Run any script from the project root, e.g.:
```bash
python scripts/ingestion/load_daily_game_logs.py [YYYY-MM-DD]
python scripts/lock_decision_input.py PLAYER_ID --game-id GAME_ID --season-id 22024 --game-date YYYY-MM-DD
```

Run any `schema/`/`models/` `.sql` file via `psql`, e.g.:
```bash
psql -d postgres -f models/game_lock_signal.sql
```

For the full deploy order of the core decision-engine files (`percentage_to_lock.sql` → `fit_hold_value_curve_by_tier.py` → `game_lock_signal.sql`) and daily-run order, see `methodology_notes.md`.

## License

MIT License — see [LICENSE](LICENSE) for details.
