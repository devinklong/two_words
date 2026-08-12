# two_words

A Python + PostgreSQL data pipeline that analyzes NBA game logs to compute a **LOCK / HOLD / PASS** recommendation for game-selection fantasy basketball formats (e.g. Sleeper) — after a player's game, should you lock in that score, or hold for a potentially better one later in the scoring week?

> The name is an homage to our Sleeper fantasy league's name.

## Project Status

**v1.0–v1.2 shipped (8/11/26).** The decision engine is live, validated, and updateable in real time:

- Core decision logic calibrated via a proper train/validate backtest, not just tuned on training data
- Two real corrections tested against real outcomes — one shipped (back-to-back fatigue), one correctly rejected after failing a targeted backtest (injury-return penalty) — see `methodology_notes.md` for the full story on why a negative result there was the right outcome, not a failure
- Daily `nba_api` ingestion (box scores, schedule, scoreboard) keeps the database current without manual backfilling, verified end-to-end and spot-checked at scale
- A callable input model (`scripts/lock_decision_input.py`) checks the database first and only computes live when a game genuinely isn't loaded yet

**Now starting v2.0**: team-level context (pace, ratings) as a real layer on top of the existing player-specific model. See `methodology_notes.md`'s Open Items.

## Overview

In game-selection formats, managers must decide after a player's game whether to **lock** in that score or **hold** for a potentially better one later in the scoring week. This project compares a completed game's result against a player's own self-relative ceiling (`GREATEST(35, their mean + 0.5*their stddev)`) — not a flat league-wide bar — to flag it as **LOCK**, **HOLD**, or **PASS**.

## Tech Stack

- **Language:** Python 3.13
- **Database:** PostgreSQL
- **Version Control:** Git & GitHub CLI (`gh`)

## Data Sources

- **`nba_api`** (stats.nba.com) — historical and daily player/team stats, game logs, box scores, schedule, and scoreboard data. Core engine. Currently on `BoxScoreTraditionalV3`/`ScoreboardV3` — both V2 predecessors were confirmed to return no real data for the 2025-26 season.
- **Sleeper API** (`api.sleeper.app`, free, no auth) — league scoring settings and roster data, used to convert raw stats into actual fantasy points per your league's rules. **Not yet integrated** — see Open Items in `methodology_notes.md`.
- **`nbainjuries`** (github.com/mxufc29/nbainjuries) — official NBA injury/rest report data, cross-referenced daily to flag whether an absence was injury-explained or a coach's decision.

Back-to-back and home/away context are derived from `nba_api` schedule data directly.

## Folder Structure

```text
two_words/
├── cleaning_logs/   # Ambiguous name-matching logs from build_gap_reasons.py, for manual review
├── config/          # Environment and connection settings (DB credentials, constants)
├── data/            # Raw and processed data files (ignored by git where applicable)
├── docs/            # Project documentation, diagrams, methodology_notes.md
├── models/          # The deployed decision-engine schema (ownable_player_pool, player_tiers,
│                     percentage_to_lock, game_lock_signal, player_injury_return_flags,
│                     weekly_outcome_simulation)
├── notebooks/       # Exploratory analysis and prototyping (Jupyter)
├── schema/          # Supporting schema: tables/, fixes/ (one-time migrations), analysis/
├── scripts/         # ETL, daily ingestion, calibration, and the callable decision-engine input
└── tests/           # tests/injuries/ and tests/retired/ hold superseded/rejected experiments
                       kept as a record; live tests run from tests/ directly
```

## Getting Started

Development environment: VS Code on macOS (Apple Silicon), Python 3.13 via `pyenv`, PostgreSQL via Homebrew. DB connection settings live in `config/.env` (gitignored) and are read by `scripts/db_connection.py`.

Run any script from the project root, e.g.:
```bash
python scripts/load_daily_game_logs.py [YYYY-MM-DD]
python scripts/lock_decision_input.py PLAYER_ID --game-id GAME_ID --season-id 22024 --game-date YYYY-MM-DD
```

Run any `schema/`/`models/` `.sql` file via `psql`, e.g.:
```bash
psql -h 127.0.0.1 -U <user> -d postgres -f models/game_lock_signal.sql
```

For the full deploy order of the core decision-engine files (`percentage_to_lock.sql` → `fit_hold_value_curve_by_tier.py` → `game_lock_signal.sql`) and daily-run order, see `methodology_notes.md`.

## License

MIT License — see [LICENSE](LICENSE) for details.
