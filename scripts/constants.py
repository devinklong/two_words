"""
scripts/constants.py

Single source of truth for constants previously hardcoded/duplicated
across multiple grid-search, validation, and verification scripts
(docs/architecture_risks.md #8, centralized 8/22/26). Import from here
instead of redefining locally -- a future change to any of these (a
new season added to the league, a recalibrated replacement level) now
only needs to happen in one place.

TRAIN_SEASONS / VALIDATE_SEASONS: the train/validate split used
throughout the lock/hold backtest and grid-search scripts. 2021-24
seasons for training, 2024-26 for out-of-sample validation.

REPLACEMENT_LEVEL: fixed floor score assumed for a PASS decision (no
game cleared the lock bar). Deliberately a fixed constant, not
derived -- with only 2 real seasons of league history there isn't
enough data to model this empirically yet. See methodology_notes.md's
"Replacement-Level Assumption for PASS" note. Revisit once more
seasons of real data exist; until then this stays 30.

MAX_WEEK: this league's actual season length -- 21 regular season
weeks + 3 playoff weeks. Used as the iteration bound wherever a
script needs to walk every week of a season (ingestion backfills,
independent verification pulls).

get_current_season_id(): NOT a static constant like the ones above --
"current season" is a real fact that changes once a year, not a fixed
historical value, so it can't be a Python literal without risking
drift from what ownable_player_pool.sql's SQL bootstrap logic is
using. Both sides read the same single source of truth instead: the
current_season_config table (schema/tables/create_tables.sql), same
pattern already established by sleeper_scoring_constants -- a real
operational fact lives in the DB, not hardcoded per-file/per-language.
Update the DB row once per season; nothing in scripts/ or models/
needs a code change.
"""

TRAIN_SEASONS = ('22021', '22022', '22023')
VALIDATE_SEASONS = ('22024', '22025')

REPLACEMENT_LEVEL = 30

MAX_WEEK = 24  # 21 regular season weeks + 3 playoff weeks, this league's actual schedule


def get_current_season_id(conn):
    """Reads the live season_id from current_season_config -- the same
    table ownable_player_pool.sql's bootstrap CTEs read from. Pass an
    open connection (e.g. from db_connection.get_connection()); does
    not open or close one itself, matching this project's convention
    of the caller owning connection lifecycle."""
    cur = conn.cursor()
    cur.execute("SELECT season_id FROM current_season_config WHERE id = 1;")
    row = cur.fetchone()
    cur.close()
    if row is None:
        raise RuntimeError(
            "current_season_config has no row -- has the table from "
            "schema/tables/create_tables.sql been deployed?"
        )
    return row[0]
