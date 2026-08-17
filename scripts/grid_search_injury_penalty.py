"""
Grid search for RETURN_GAME_PENALTY, the additive lock_bar penalty applied
to a player's return game after an injury-explained absence
(schema/lock_model/game_lock_signal.sql). Mirrors the approach used for
ABSOLUTE_FLOOR/CEILING_MULTIPLIER: hold everything else fixed (pool
membership, floor=35, ceiling_multiplier=0.5 -- all already validated),
sweep only the new constant, and check the winner holds up on BOTH the
train split (2021-24) and the validate split (2024-26) independently,
same non-overfit bar that floor/ceiling_multiplier had to clear.

CENTRALIZED 8/15/26 (docs/patch_list.md #1): the base lock_bar (before
the injury penalty is added) now calls the shared lock_bar() SQL
function with its default floor=35/ceiling_multiplier=0.5, instead of
hand-writing GREATEST(35, ...) -- this file always uses the canonical
values for floor/mult (only the penalty term varies), so the defaults
apply cleanly. The penalty addition itself stays inline here since it's
specific to this experiment, not part of the shared formula. DEPLOY
ORDER: lock_bar_function.sql must exist before running this.

Candidate grid intentionally centered on the corrected diagnostic figure
(-1.46 pt dip for high-usage return games, confirmed 8/10/26 after fixing
a season_id join bug that had inflated the original estimate to -3.11).
0.0 is included as a real candidate, not just a sanity check -- if the
penalty doesn't actually improve edge_over_naive vs not having it at all,
that's a legitimate answer, not a bug.

Simulation logic (banked score per player-week, oracle, naive baseline)
is copied from weekly_outcome_simulation.sql -- see that file for the
full rationale on why PASS banks GREATEST(final_score, 30), why oracle
uses the same floor, etc. Only the lock decision itself is
re-parameterized here per candidate penalty, since game_lock_signal.sql
is a VIEW with the penalty hardcoded at 1.5 -- can't grid-search a fixed
view, so the CASE logic is recomputed inline per candidate instead.

Run as: python scripts/grid_search_injury_penalty.py
"""

from db_connection import get_connection

CANDIDATE_PENALTIES = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

TRAIN_SEASONS = ('22021', '22022', '22023')
VALIDATE_SEASONS = ('22024', '22025')

# Policy banked score for a given candidate penalty, split train/validate.
# lock_bar()'s default floor (35) and ceiling_multiplier (0.5) held fixed
# -- already validated via the original grid search (schema/lock_model
# docs, 8/9/26).
POLICY_SQL = """
WITH base AS (
    SELECT
        gfswls.player_id, gfswls.season_id, gfswls.week_number, gfswls.game_date,
        gfswls.fantasy_score, gfswls.games_remaining_in_week,
        pss.avg_fantasy_score AS player_avg,
        pss.stddev_fantasy_score AS player_std,
        COALESCE(pirf.is_return_game, FALSE) AS is_return_game
    FROM game_fantasy_scores_weekly_lock_signal gfswls
    JOIN ownable_player_pool opp
        ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
    JOIN player_season_fantasy_stats pss
        ON pss.player_id = gfswls.player_id AND pss.season_id = gfswls.season_id
    LEFT JOIN player_injury_return_flags pirf
        ON pirf.player_id = gfswls.player_id
        AND pirf.team_id = gfswls.team_id
        AND pirf.game_date = gfswls.game_date
),
scored AS (
    SELECT
        *,
        lock_bar(player_avg, player_std)
            + CASE WHEN is_return_game THEN %(penalty)s ELSE 0 END AS lock_bar,
        CASE
            WHEN fantasy_score >= lock_bar(player_avg, player_std)
                + CASE WHEN is_return_game THEN %(penalty)s ELSE 0 END
                THEN 'LOCK'
            WHEN games_remaining_in_week = 0 THEN 'PASS'
            ELSE 'HOLD'
        END AS lock_signal
    FROM base
),
first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM scored
    WHERE lock_signal = 'LOCK'
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM scored
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM scored
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        CASE WHEN pw.season_id IN %(train)s THEN 'train' ELSE 'validate' END AS split,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, 30)) AS policy_banked_score
    FROM player_weeks pw
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number)
)
SELECT split, COUNT(*) AS player_weeks, ROUND(AVG(policy_banked_score), 4) AS avg_policy_banked
FROM banked
GROUP BY split
ORDER BY split;
"""

# Naive baseline (flat 30.3 cutoff, no lock_bar/penalty logic at all) and
# oracle -- both INDEPENDENT of the penalty, computed once and reused
# across every candidate. Same population (ownable pool) as POLICY_SQL,
# so the comparison is apples to apples.
BASELINE_SQL = """
WITH base AS (
    SELECT
        gfswls.player_id, gfswls.season_id, gfswls.week_number, gfswls.game_date,
        gfswls.fantasy_score, gfswls.games_remaining_in_week
    FROM game_fantasy_scores_weekly_lock_signal gfswls
    JOIN ownable_player_pool opp
        ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
),
naive_first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS naive_locked_score
    FROM base
    WHERE fantasy_score >= 30.3
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM base
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
oracle AS (
    SELECT player_id, season_id, week_number, GREATEST(MAX(fantasy_score), 30) AS oracle_score
    FROM base
    GROUP BY player_id, season_id, week_number
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM base
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        CASE WHEN pw.season_id IN %(train)s THEN 'train' ELSE 'validate' END AS split,
        COALESCE(nfl.naive_locked_score, GREATEST(lg.final_score, 30)) AS naive_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN naive_first_lock nfl USING (player_id, season_id, week_number)
)
SELECT
    split,
    ROUND(AVG(naive_banked_score), 4) AS avg_naive_banked,
    ROUND(AVG(oracle_score), 4) AS avg_oracle
FROM banked
GROUP BY split
ORDER BY split;
"""


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(BASELINE_SQL, {'train': TRAIN_SEASONS})
    baseline_rows = {row[0]: {'avg_naive_banked': float(row[1]), 'avg_oracle': float(row[2])}
                     for row in cur.fetchall()}

    print("=== Naive baseline / oracle (penalty-independent) ===")
    for split in ('train', 'validate'):
        b = baseline_rows[split]
        print(f"{split:>9}: naive={b['avg_naive_banked']:.2f}  oracle={b['avg_oracle']:.2f}")
    print()

    print(f"{'penalty':>7} | {'train edge':>10} | {'validate edge':>13} | {'gap (train-validate)':>21}")
    print("-" * 62)

    results = []
    for penalty in CANDIDATE_PENALTIES:
        cur.execute(POLICY_SQL, {'penalty': penalty, 'train': TRAIN_SEASONS})
        policy_rows = {row[0]: {'player_weeks': row[1], 'avg_policy_banked': float(row[2])}
                       for row in cur.fetchall()}

        train_edge = policy_rows['train']['avg_policy_banked'] - baseline_rows['train']['avg_naive_banked']
        validate_edge = policy_rows['validate']['avg_policy_banked'] - baseline_rows['validate']['avg_naive_banked']
        gap = abs(train_edge - validate_edge)

        results.append((penalty, train_edge, validate_edge, gap))
        print(f"{penalty:>7.1f} | {train_edge:>10.4f} | {validate_edge:>13.4f} | {gap:>21.4f}")

    cur.close()
    conn.close()

    # Winner selection mirrors the original floor/ceiling_multiplier
    # search: don't just chase the single best train (or validate) edge
    # in isolation -- that's how to overfit a threshold to 5 seasons of
    # data. Prefer the candidate with the best MINIMUM of (train edge,
    # validate edge), so a penalty that looks great on train but falls
    # apart on validate (or vice versa) loses to one that's consistently
    # good on both, even if its single-split peak is lower.
    best = max(results, key=lambda r: min(r[1], r[2]))
    print()
    print(f"Best by min(train, validate) edge: penalty={best[0]}, "
          f"train_edge={best[1]:.4f}, validate_edge={best[2]:.4f}, gap={best[3]:.4f}")
    print("Compare this against penalty=0.0's row above -- if 0.0 wins or is "
          "statistically indistinguishable from the winner, the honest "
          "conclusion is the penalty isn't earning its keep and should be "
          "dropped, not kept because a small positive number looks nicer.")


if __name__ == '__main__':
    main()
