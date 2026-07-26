# two_words

This project demonstrates schema design, ETL pipeline construction, and statistical
analysis using a real-world dataset (NBA game logs) and a concrete decision-making
use case: fantasy basketball "lock vs. hold" strategy.

**two_words** is a Python + PostgreSQL data pipeline that analyzes NBA game logs to calculate optimal "lock vs. hold" thresholds for game-selection fantasy basketball formats (e.g. Sleeper). It ingests official NBA statistics, computes rolling performance baselines, and outputs a data-driven **LOCK / PASS / EVALUATE** recommendation for each completed game.

> The name is an homage to our Sleeper fantasy league's name.

## Project Status
🚧 In development — schema and ingestion pipeline are being actively designed. Not yet functional end-to-end.

## Overview

In game-selection formats, managers must decide after a player's game whether to **lock** in that score or **hold** for a potentially better one later in the scoring week. Existing projection tools are plentiful, but few account for this specific lock/hold mechanic — this project fills that gap by comparing a completed game's result against player-specific rolling thresholds (5-game and 10-game averages and standard deviations) to flag it as **LOCK**, **PASS**, or **EVALUATE**.

## Initial Scope (v1.0)

- **Relational Schema Design** — Model players, teams, and game logs with primary/foreign key constraints for data integrity.
- **Database Architecture** — PostgreSQL, optimized for multi-year historical queries and rolling-window aggregations.
- **Data Ingestion** — Pull game logs and player/team stats via `nba_api`, stored in PostgreSQL.
- **Data Processing (ETL)** — Clean and transform raw data into consistent, query-ready tables.
- **Statistical Baselines** — Rolling averages (5-game, 10-game) and standard deviations defining floor/median/ceiling ranges.
- **Decision Engine** — Compares completed game results against thresholds to output LOCK / PASS / EVALUATE.

## Tech Stack

- **Language:** Python 3
- **Database:** PostgreSQL
- **Version Control:** Git & GitHub CLI (`gh`)

## Data Sources

- **`nba_api`** (stats.nba.com) — historical player/team stats, game logs, rolling performance metrics. Core engine.
- **Sleeper API** (`api.sleeper.app`, free, no auth) — league scoring settings and roster data, used to convert raw stats into actual fantasy points per your league's rules.
- **`nbainjuries`** (github.com/mxufc29/nbainjuries) — official NBA injury/rest report data, used to flag availability risk for upcoming games.

Back-to-back and home/away context are derived from `nba_api` game log dates and matchup fields directly — no additional source required.

## Folder Structure

```text
two_words/
├── cleaning_logs/   # Records/logs of data-cleaning runs for auditing and debugging
├── config/          # Environment and connection settings (DB credentials, constants)
├── data/            # Raw and processed data files (ignored by git where applicable)
├── docs/            # Project documentation, diagrams, and notes
├── models/          # Schema/ORM definitions and statistical model logic
├── notebooks/       # Exploratory analysis and prototyping (Jupyter)
├── schema/          # SQL DDL / migration files defining the database structure
├── scripts/         # Standalone ETL and utility scripts (ingestion, processing)
└── tests/           # Unit and integration tests
```

## Getting Started

<!-- Add this section later — expand once database specs are finalized -->
Development environment: VS Code on macOS (M1), PostgreSQL installed via Homebrew.

Database connection specs and setup steps to be documented here once finalized.

## License

MIT License — see [LICENSE](LICENSE) for details.