"""
scripts/analysis/analyze_position_scoring_distributions.py

Answers the v3.2 player-side question: within this league's actual data,
do position-eligible groups show meaningfully different fantasy-score
distributions? Deliberately descriptive, not predictive (framing
decision, 8/24/26) -- reports observed percentile distributions, a
per-season and per-season-per-tier Kruskal-Wallis significance check,
plus (8/24/26 extension) a G/F/C-collapsed version of the same tests,
Dunn's post-hoc pairwise test, an epsilon-squared effect size, and a
Levene's test on variance -- never a forecast. Reads
player_scores_by_position_tier (view, no new table/schema change) so
multi-eligible players correctly appear once per eligible position.

Kruskal-Wallis (not ANOVA) chosen since fantasy scores are expected to
be right-skewed (boom games), not normal -- a mean/stddev-based test
would be shakier here. Run separately per season (not pooled) since
"is this signal or noise" is inherently a per-season question, and
separately per season+tier since a pooled-across-tier result could
just be reflecting which tier clusters at which position rather than a
real position effect (confirmed 8/24/26: pooled-across-tier is
significant every season, but within-tier results genuinely diverge by
tier -- exactly the confound this stratification is meant to catch).

G/F/C COLLAPSE (new): PG/SG->G, SF/PF->F, C stays C, matching the real
roster slot categories (G/F/C/UTIL) rather than the 5 granular
positions. A multi-eligible player (e.g. PG/SG) would otherwise
contribute the SAME (player, game) score twice to the collapsed "G"
bucket -- deduped here on (season, tier, group, player, game) so each
real game only counts once per collapsed group.

DUNN'S POST-HOC (new): Kruskal-Wallis is an omnibus test -- it says
SOME group differs, not which ones. Run per season (pooled across
tiers, for enough per-position sample size), Bonferroni-corrected for
the multiple pairwise comparisons.

EFFECT SIZE (new): epsilon-squared (Tomczak & Tomczak, 2014:
(H - k + 1) / (n - k)) alongside every Kruskal-Wallis result -- with
group sizes in the hundreds to thousands, statistical significance
alone doesn't tell you whether a real difference is actually large.
Interpreted via the common small/medium/large rule-of-thumb thresholds
(~0.01/0.06/0.14) also used for eta-squared -- a convention, not a
definitive standard.

LEVENE'S TEST (new): tests whether positions differ in SPREAD
(consistency vs. boom-bust), not just typical score -- directly
relevant to the trade-valuation case (two players tied on ceiling,
differentiated by floor/variance). Median-centered (Brown-Forsythe
variant), robust to the same right-skew that ruled out ANOVA above.

MANN-WHITNEY U, CENTER VS. NON-CENTER (new, 8/24/26): a 2-group version
of the same question, collapsing G+F into one "Non-Center" pool. Not
new information beyond what Dunn's post-hoc already shows for C-vs-
other pairs (Kruskal-Wallis with 2 groups reduces to the same ranking
logic as Mann-Whitney) -- the value here is a direct answer to the
actual recurring question ("treat centers differently, yes/no") with
more statistical power on the non-center side (pooled sample) than any
single-position comparison, useful specifically for checking whether
the noisy 2_mid tier result is real noise or just underpowered at the
5-way split. Effect size reported as both P(C > non-C) (the common-
language / probability-of-superiority statistic, directly from the U
statistic: U / (n1*n2)) and rank-biserial correlation (2*P - 1).

FRONTCOURT VS. PERIMETER (new, 8/24/26): Dunn's post-hoc showed C and
PF are statistically indistinguishable from each other most seasons,
while both differ from PG/SF/SG -- suggesting the real dividing line
isn't "Centers vs. everyone" but frontcourt (C+PF) vs. perimeter
(PG/SF/SG). Same 2-group Mann-Whitney pattern as Center vs. Non-Center,
now generalized (`run_mannwhitney_2group`) to take arbitrary group
labels so both comparisons share one implementation.

OUTLIER-ROBUSTNESS CHECK (new, 8/24/26): the elite-tier Center premium
could be a broad positional pattern, or it could really be a handful of
generational players (Jokić-type outliers) carrying the whole group's
mean. Re-runs the elite-tier C-vs-Non-C Mann-Whitney test with the
TOP_N_OUTLIERS highest-average Center players excluded per season --
if the signal collapses without them, the honest takeaway is "prioritize
specific elite players," not "prioritize the Center position" broadly.
Compare this block's per-season result directly against the untrimmed
elite-tier row in the "Center vs. Non-Center, within each tier" section
above. Players are identified only by nba_player_id (no name lookup in
this script's scope) -- cross-reference against `players` table by hand
if you want to know who got excluded.

INCREMENTAL EXCLUSION (new, 8/24/26): the top-3 exclusion flipped the
elite-tier sign entirely, with the same nba_player_id (203999) excluded
every single season -- strongly suggestive of one player (real-world:
Nikola Jokić) doing most of the work rather than the group of 3 equally.
Reruns the same robustness check at top_n = 1, 2, 3 in sequence so the
MARGINAL contribution of each additional excluded player is visible
(does the sign already flip at top_n=1, or only once 2-3 are removed?).

GAMES-PLAYED CHECK (new, 8/24/26): everything above is per-game
distribution only -- completely blind to how many games a player
actually played that season. A player with an elite per-game average
who missed half the season isn't just "worth less in total" in this
tool's specific hold-until-spike mechanic -- fewer games also means
fewer weekly chances to catch a spike and lock it, a real structural
penalty beyond a simple season-total discount. Pulls real games-played
counts (from `game_logs`) for every player ever flagged in the top-3
outlier-exclusion set, across all 5 backfilled seasons, alongside each
season's max games played by any single player (a rough "fully healthy"
reference point) -- checks whether the flagged Centers' high averages
are offset by real durability gaps, particularly in the most recent
seasons. ASSUMPTION flagged for review: assumes `game_logs` has
`player_id`, `season_id`, `game_id` columns -- adjust the two new
queries below if the real schema differs.

SINGLE-PLAYER OUTLIER CHECK (new, 8/24/26): the practical follow-up to
the whole investigation -- given ANY one player (name or nba_player_id),
answers "is this player a real statistical outlier relative to their
own tier+position peers, the same way Jokić/Wemby were flagged, or are
they riding a position-level assumption that this analysis found doesn't
hold?" Run via `--player <name-or-id>` instead of the full battery (e.g.
`python analyze_position_scoring_distributions.py --player Wembanyama`
or `--player 1641705`). Per season/tier/position the player appears in,
compares their own score distribution against every OTHER player in
that same (season, tier, position) bucket via Mann-Whitney U, plus the
same real games-played check used above. A name match resolves against
`players.full_name` (ILIKE, same ASSUMPTION as `build_sleeper_player_
crosswalk.py`'s NBA_NAME_QUERY) -- an ambiguous name prints all
candidates and asks for the exact ID instead of guessing.

ELIGIBILITY-COUNT CHECK (new, 8/24/26): everything above tests WHICH
position(s) a player is eligible for. This tests something different --
does HOW MANY positions a player is eligible for (1 vs. 2 vs. 3+)
predict scoring at all, independent of which specific ones? The
practical roster-construction advice coming out of this whole
investigation leans on flexibility being a scoring-NEUTRAL tiebreaker
(prefer multi-eligible players when position itself doesn't move the
needle) -- this checks that assumption directly rather than just
asserting it. A player's eligibility count is computed from how many
distinct positions appear across ALL their rows in this view (current-
state, so fixed per player regardless of season -- same caveat as
everywhere else in this script). Deduped to one row per (season, tier,
player, game) before bucketing, since a multi-eligible player would
otherwise contribute the same score once per eligible position.

ELIGIBILITY-COUNT, OUTLIERS EXCLUDED (new, 8/24/26): the raw
eligibility-count result showed elite-tier single-position players
significantly outscoring multi-eligible ones -- but the known outlier
Centers (Jokić et al.) are themselves single-position eligible, so that
result could just be the same outlier effect wearing a different label
rather than a real, independent flexibility effect. Reruns the
elite-tier eligibility-count test with every game belonging to a known
outlier (the union of all top-3-per-season exclusions from the
outlier-robustness check above) removed entirely, plus prints each
outlier's real eligible-position set directly from the data (answers
e.g. "is Wembanyama single- or multi-position eligible" concretely
instead of inferring it from which buckets he showed up in elsewhere).

Requires (not otherwise a project dependency yet):
    pip install scipy pandas scikit-posthocs --break-system-packages
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
import pandas as pd
from scipy.stats import kruskal, levene, mannwhitneyu
import scikit_posthocs as sp

PERCENTILES = [10, 25, 50, 75, 90]
MIN_GROUP_SIZE = 5  # below this, a position group's test result is too noisy to trust

POSITION_TO_GROUP = {"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"}
POSITION_TO_CENTER_GROUP = {"PG": "Non-C", "SG": "Non-C", "SF": "Non-C", "PF": "Non-C", "C": "C"}
POSITION_TO_FRONTCOURT_GROUP = {"PG": "Perimeter", "SG": "Perimeter", "SF": "Perimeter", "PF": "Frontcourt", "C": "Frontcourt"}
TOP_N_OUTLIERS = 3  # number of highest-average Center players excluded per season in the robustness check

VIEW_QUERY = """
    SELECT season_id, tier, position, fantasy_score, nba_player_id, game_id
    FROM player_scores_by_position_tier;
"""

GAMES_PLAYED_QUERY = """
    SELECT player_id, season_id, COUNT(DISTINCT game_id) AS games_played
    FROM game_logs
    WHERE player_id = ANY(%s)
    GROUP BY player_id, season_id
    ORDER BY player_id, season_id;
"""

SEASON_MAX_GAMES_QUERY = """
    SELECT season_id, MAX(games_played) AS max_games_any_player
    FROM (
        SELECT season_id, player_id, COUNT(DISTINCT game_id) AS games_played
        FROM game_logs
        GROUP BY season_id, player_id
    ) per_player
    GROUP BY season_id;
"""

PLAYER_NAME_QUERY = """
    SELECT player_id, full_name FROM players WHERE full_name ILIKE %s ORDER BY full_name;
"""


def fetch_rows(cur):
    cur.execute(VIEW_QUERY)
    return cur.fetchall()


def group_scores(rows):
    """Buckets fantasy_score by (season_id, tier, position) -- 5-way, granular.
    No dedup needed here: each (player, game, position) row is already
    distinct by construction (one row per real eligible position)."""
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, _, _ in rows:
        buckets[(season_id, tier, position)].append(float(fantasy_score))
    return buckets


def group_scores_gfc(rows):
    """Buckets fantasy_score by (season_id, tier, G/F/C group) -- collapsed.
    Deduped on (season, tier, group, player, game): a PG/SG player's game
    would otherwise land in "G" twice (once via PG, once via SG)."""
    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        group = POSITION_TO_GROUP[position]
        key = (season_id, tier, group, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        buckets[(season_id, tier, group)].append(float(fantasy_score))
    return buckets


def group_scores_center_vs_noncenter(rows):
    """Buckets fantasy_score by (season_id, tier, 'C'/'Non-C') -- 2-group
    collapse of the G/F/C grouping. Same dedup as group_scores_gfc: a
    multi-eligible non-center player (e.g. PG/SG, or SF/PF) would
    otherwise contribute the same (player, game) score to "Non-C" more
    than once."""
    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        group = POSITION_TO_CENTER_GROUP[position]
        key = (season_id, tier, group, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        buckets[(season_id, tier, group)].append(float(fantasy_score))
    return buckets


def group_scores_frontcourt_perimeter(rows):
    """Buckets fantasy_score by (season_id, tier, 'Frontcourt'/'Perimeter') --
    C+PF vs. PG+SF+SG, following the Dunn's post-hoc finding that C and PF
    aren't reliably distinguishable from each other. Same dedup pattern as
    the other collapsed groupings."""
    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        group = POSITION_TO_FRONTCOURT_GROUP[position]
        key = (season_id, tier, group, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        buckets[(season_id, tier, group)].append(float(fantasy_score))
    return buckets


def build_season_groups(buckets):
    by_season = defaultdict(lambda: defaultdict(list))
    for (season_id, tier, position), scores in buckets.items():
        by_season[season_id][position].extend(scores)
    return by_season


def build_season_tier_groups(buckets):
    by_season_tier = defaultdict(lambda: defaultdict(list))
    for (season_id, tier, position), scores in buckets.items():
        by_season_tier[(season_id, tier)][position].extend(scores)
    return by_season_tier


def print_percentile_table(buckets, label):
    print(f"\n=== Percentile table, {label} (season, tier, position) ===")
    header = (
        f"{'season':<10}{'tier':<10}{'pos':<5}{'n':>6}"
        + "".join(f"{'p' + str(p):>8}" for p in PERCENTILES)
        + f"{'mean':>8}{'stdev':>8}"
    )
    print(header)
    for (season_id, tier, position), scores in sorted(
        buckets.items(), key=lambda k: (str(k[0][0]), str(k[0][1]), k[0][2])
    ):
        arr = np.array(scores)
        pcts = np.percentile(arr, PERCENTILES)
        row = (
            f"{str(season_id):<10}{str(tier):<10}{position:<5}{len(arr):>6}"
            + "".join(f"{p:>8.2f}" for p in pcts)
            + f"{arr.mean():>8.2f}{arr.std(ddof=1):>8.2f}"
        )
        print(row)


def kruskal_with_effect_size(groups):
    """Runs Kruskal-Wallis across `groups` (list of lists of scores),
    returns (H, p, epsilon_squared, k, n). Epsilon-squared per Tomczak &
    Tomczak (2014): (H - k + 1) / (n - k) -- a rule-of-thumb effect size,
    not a definitive standard."""
    stat, p = kruskal(*groups)
    k = len(groups)
    n = sum(len(g) for g in groups)
    epsilon_sq = (stat - k + 1) / (n - k) if n > k else float("nan")
    return stat, p, epsilon_sq, k, n


def effect_label(eps):
    if np.isnan(eps):
        return "n/a"
    if eps < 0.01:
        return "negligible"
    elif eps < 0.06:
        return "small"
    elif eps < 0.14:
        return "medium"
    return "large"


def run_kruskal(grouped, label):
    print(f"\n=== Kruskal-Wallis, {label} ===")
    for key, position_groups in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        groups = {pos: scores for pos, scores in position_groups.items() if len(scores) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{key}: not enough groups with >={MIN_GROUP_SIZE} games, skipped")
            continue
        stat, p, eps, k, n = kruskal_with_effect_size(list(groups.values()))
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(f"{key}: H={stat:.2f}  p={p:.4f}  eps2={eps:.4f} ({effect_label(eps)})  {verdict}")


def run_levene(grouped, label):
    print(f"\n=== Levene's test for equal variance (median-centered), {label} ===")
    for key, position_groups in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        groups = {pos: scores for pos, scores in position_groups.items() if len(scores) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{key}: not enough groups with >={MIN_GROUP_SIZE} games, skipped")
            continue
        stat, p = levene(*groups.values(), center="median")
        verdict = "positions differ in spread" if p < 0.05 else "spread similar across positions"
        print(f"{key}: W={stat:.2f}  p={p:.4f}  {verdict}")


def run_posthoc_dunn(buckets, label):
    """Per season (pooled across tiers, for adequate per-group sample
    size), Bonferroni-corrected pairwise comparisons -- identifies WHICH
    position pairs actually differ, not just that some pair does."""
    print(f"\n=== Dunn's post-hoc pairwise test, Bonferroni-corrected, {label} ===")
    by_season = defaultdict(list)
    for (season_id, tier, position), scores in buckets.items():
        for s in scores:
            by_season[season_id].append((position, s))

    for season_id, rows in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        df = pd.DataFrame(rows, columns=["position", "score"])
        counts = df["position"].value_counts()
        valid_positions = counts[counts >= MIN_GROUP_SIZE].index.tolist()
        if len(valid_positions) < 2:
            print(f"{season_id}: not enough position groups, skipped")
            continue
        df = df[df["position"].isin(valid_positions)]
        result = sp.posthoc_dunn(df, val_col="score", group_col="position", p_adjust="bonferroni")
        positions = result.columns.tolist()
        sig_pairs = []
        for i, p1 in enumerate(positions):
            for p2 in positions[i + 1:]:
                p_val = result.loc[p1, p2]
                if p_val < 0.05:
                    sig_pairs.append(f"{p1} vs {p2} (p={p_val:.4f})")
        print(f"{season_id}: " + ("; ".join(sig_pairs) if sig_pairs else "no significant pairwise differences"))


def run_mannwhitney_2group(grouped, label, group_a, group_b):
    """Generic 2-group Mann-Whitney U runner -- used for both Center vs.
    Non-Center and Frontcourt vs. Perimeter. Effect size: P(group_a >
    group_b) straight from the U statistic (U / (n1*n2)), plus
    rank-biserial correlation (2*P - 1)."""
    print(f"\n=== Mann-Whitney U, {group_a} vs. {group_b}, {label} ===")
    for key, position_groups in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        a_scores = position_groups.get(group_a, [])
        b_scores = position_groups.get(group_b, [])
        if len(a_scores) < MIN_GROUP_SIZE or len(b_scores) < MIN_GROUP_SIZE:
            print(f"{key}: not enough {group_a} and/or {group_b} games, skipped")
            continue
        stat, p = mannwhitneyu(a_scores, b_scores, alternative="two-sided")
        n1, n2 = len(a_scores), len(b_scores)
        prob_a_greater = stat / (n1 * n2)
        rank_biserial = 2 * prob_a_greater - 1
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(
            f"{key}: U={stat:.1f}  p={p:.4f}  P({group_a}>{group_b})={prob_a_greater:.3f}  "
            f"rank-biserial={rank_biserial:+.3f}  {verdict}"
        )


def build_center_player_means(rows, tier_filter):
    """For a given tier's Centers only: dedup on (season, player, game),
    then compute each Center player's own mean score for that season --
    used to identify potential outlier-driven effects."""
    seen = set()
    player_scores = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        if tier != tier_filter or position != "C":
            continue
        key = (season_id, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        player_scores[(season_id, nba_player_id)].append(float(fantasy_score))
    return player_scores


def run_outlier_robustness_check(rows, tier_filter="1_elite", top_n=TOP_N_OUTLIERS):
    """Re-runs the given tier's C-vs-Non-C Mann-Whitney test with the top_n
    highest-average Center players excluded per season -- checks whether
    the Center premium is a broad positional pattern or is mostly being
    carried by a small number of outlier (e.g. generational-talent)
    players. Compare each season's result here directly against the same
    season's row in the untrimmed "Center vs. Non-Center, within each
    tier" section above. Returns excluded_by_season (season_id -> set of
    excluded nba_player_ids) so callers can reuse the exclusion set."""
    print(f"\n=== Outlier-robustness check: {tier_filter} C vs. Non-C, top {top_n} Center(s) excluded per season ===")
    center_player_means = build_center_player_means(rows, tier_filter)

    means_by_season = defaultdict(list)
    for (season_id, player_id), scores in center_player_means.items():
        means_by_season[season_id].append((player_id, np.mean(scores)))

    excluded_by_season = {}
    for season_id, player_list in means_by_season.items():
        top_players = sorted(player_list, key=lambda x: -x[1])[:top_n]
        excluded_by_season[season_id] = {pid for pid, _ in top_players}

    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        if tier != tier_filter:
            continue
        group = POSITION_TO_CENTER_GROUP[position]
        if group == "C" and nba_player_id in excluded_by_season.get(season_id, set()):
            continue
        key = (season_id, group, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        buckets[(season_id, group)].append(float(fantasy_score))

    by_season = defaultdict(dict)
    for (season_id, group), scores in buckets.items():
        by_season[season_id][group] = scores

    for season_id, position_groups in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        c_scores = position_groups.get("C", [])
        nc_scores = position_groups.get("Non-C", [])
        excluded_ids = excluded_by_season.get(season_id, set())
        if len(c_scores) < MIN_GROUP_SIZE or len(nc_scores) < MIN_GROUP_SIZE:
            print(f"{season_id}: not enough remaining C and/or Non-C games after exclusion, skipped")
            continue
        stat, p = mannwhitneyu(c_scores, nc_scores, alternative="two-sided")
        n1, n2 = len(c_scores), len(nc_scores)
        prob_c_greater = stat / (n1 * n2)
        rank_biserial = 2 * prob_c_greater - 1
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(
            f"{season_id}: excluded {len(excluded_ids)} Center(s) (nba_player_id {sorted(excluded_ids)})  "
            f"{n1} C games remain, {n2} Non-C  U={stat:.1f}  p={p:.4f}  "
            f"P(C>non-C)={prob_c_greater:.3f}  rank-biserial={rank_biserial:+.3f}  {verdict}"
        )

    return excluded_by_season


def run_games_played_check(cur, player_ids):
    """Pulls real games-played counts (game_logs) for the given player_ids
    across all 5 backfilled seasons, plus each season's max games played
    by any single player (a rough 'fully healthy' reference) -- checks
    whether a flagged outlier Center's high per-game average is offset by
    real durability gaps, which this analysis is otherwise blind to."""
    if not player_ids:
        print("\n=== Games-played check: no outlier players to check ===")
        return

    print("\n=== Games-played check, flagged outlier Centers (all 5 backfilled seasons) ===")
    cur.execute(GAMES_PLAYED_QUERY, (list(player_ids),))
    rows = cur.fetchall()
    played = defaultdict(dict)
    for player_id, season_id, games in rows:
        played[player_id][season_id] = games

    cur.execute(SEASON_MAX_GAMES_QUERY)
    max_by_season = {season_id: max_games for season_id, max_games in cur.fetchall()}
    seasons = sorted(max_by_season.keys())

    header = f"{'nba_player_id':<15}" + "".join(f"{s:>14}" for s in seasons)
    print(header)
    print(f"{'(season max)':<15}" + "".join(f"{max_by_season[s]:>14}" for s in seasons))
    for player_id in sorted(played.keys()):
        row = f"{player_id:<15}"
        for s in seasons:
            games = played[player_id].get(s)
            if games is None:
                row += f"{'--':>14}"
            else:
                pct = 100 * games / max_by_season[s]
                row += f"{f'{games} ({pct:.0f}%)':>14}"
        print(row)


def resolve_player(cur, identifier):
    """Resolves a --player CLI argument to a single nba_player_id. Accepts
    either a numeric nba_player_id directly, or a name (ILIKE match
    against `players.full_name`, same ASSUMPTION as `build_sleeper_player_
    crosswalk.py`'s NBA_NAME_QUERY) -- an ambiguous name prints every
    candidate and returns None rather than guessing which one was meant."""
    try:
        return int(identifier)
    except ValueError:
        pass
    cur.execute(PLAYER_NAME_QUERY, (f"%{identifier}%",))
    matches = cur.fetchall()
    if not matches:
        print(f"No player found matching '{identifier}'.")
        return None
    if len(matches) > 1:
        print(f"Multiple players match '{identifier}':")
        for pid, name in matches:
            print(f"  {pid}: {name}")
        print("Rerun with the exact nba_player_id instead.")
        return None
    player_id, full_name = matches[0]
    print(f"Resolved '{identifier}' -> {full_name} (nba_player_id={player_id})")
    return player_id


def run_single_player_outlier_check(rows, player_id):
    """For one player: per (season, tier, position) they actually appear
    in, compares their own score distribution against every OTHER player
    in that exact bucket via Mann-Whitney U -- the same test used to flag
    Jokić/Wemby as real outliers, generalized to any player so a specific
    trade target can be judged the same way rather than by position
    assumption."""
    print(f"\n=== Single-player outlier check: nba_player_id {player_id} ===")
    own = defaultdict(list)
    rest = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        key = (season_id, tier, position)
        if nba_player_id == player_id:
            own[key].append(float(fantasy_score))
        else:
            rest[key].append(float(fantasy_score))

    if not own:
        print("No rows found for this player_id in player_scores_by_position_tier -- check the ID.")
        return

    for key in sorted(own.keys(), key=lambda k: (str(k[0]), str(k[1]), k[2])):
        season_id, tier, position = key
        own_scores = own[key]
        rest_scores = rest.get(key, [])
        if len(own_scores) < MIN_GROUP_SIZE or len(rest_scores) < MIN_GROUP_SIZE:
            print(
                f"{season_id} {tier} {position}: not enough games "
                f"({len(own_scores)} own, {len(rest_scores)} peers) to test, skipped"
            )
            continue
        stat, p = mannwhitneyu(own_scores, rest_scores, alternative="two-sided")
        n1, n2 = len(own_scores), len(rest_scores)
        prob_greater = stat / (n1 * n2)
        rank_biserial = 2 * prob_greater - 1
        verdict = "SIGNAL (real outlier vs. peers)" if p < 0.05 else "noise (not distinguishable from peers)"
        own_arr = np.array(own_scores)
        print(
            f"{str(season_id):<10}{str(tier):<10}{position:<4} n={n1:<5} "
            f"mean={own_arr.mean():>6.2f} stdev={own_arr.std(ddof=1):>6.2f}  vs {n2} peers  "
            f"P(player>peers)={prob_greater:.3f}  rank-biserial={rank_biserial:+.3f}  {verdict}"
        )


def build_eligibility_counts(rows):
    """Each player's real position-eligibility count (current-state, fixed
    per player regardless of season) -- computed from how many distinct
    positions appear across ALL their rows in this view."""
    positions_by_player = defaultdict(set)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        positions_by_player[nba_player_id].add(position)
    return {pid: len(positions) for pid, positions in positions_by_player.items()}


def eligibility_bucket(n_positions):
    if n_positions <= 1:
        return "1 position"
    if n_positions == 2:
        return "2 positions"
    return "3+ positions"


def group_scores_by_eligibility_count(rows, eligibility_counts):
    """Buckets fantasy_score by (season_id, tier, eligibility-count bucket)
    -- deduped to one row per (season, tier, player, game), since a
    multi-eligible player would otherwise contribute the same score once
    per eligible position. Tests whether HOW MANY positions a player is
    eligible for correlates with scoring, independent of WHICH positions."""
    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        key = (season_id, tier, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        bucket = eligibility_bucket(eligibility_counts.get(nba_player_id, 1))
        buckets[(season_id, tier, bucket)].append(float(fantasy_score))
    return buckets


def print_outlier_position_sets(rows, player_ids):
    """Prints each flagged outlier's real eligible-position set directly
    from the data -- answers "is this player single- or multi-position
    eligible" concretely rather than inferring it from which buckets
    they showed up in elsewhere in this output."""
    if not player_ids:
        print("\n=== Outlier position sets: no players to check ===")
        return
    positions_by_player = defaultdict(set)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        if nba_player_id in player_ids:
            positions_by_player[nba_player_id].add(position)
    print("\n=== Real eligible-position set, flagged outlier Centers ===")
    for player_id in sorted(positions_by_player.keys()):
        positions = sorted(positions_by_player[player_id])
        label = "position" if len(positions) == 1 else "positions"
        print(f"nba_player_id {player_id}: {'/'.join(positions)} ({len(positions)} {label})")


def run_eligibility_check_excluding_known_outliers(rows, eligibility_counts, tier_filter, outlier_ids):
    """Reruns the eligibility-count Kruskal-Wallis + Dunn's post-hoc test,
    restricted to tier_filter, with every game belonging to a known
    outlier player removed entirely -- checks whether "1-position players
    outscore multi-position players" is a real, independent flexibility
    effect or just the same handful of outliers (who happen to be
    single-position) showing up again under a different label."""
    print(f"\n=== Eligibility-count check, {tier_filter} only, known outliers excluded ===")
    seen = set()
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score, nba_player_id, game_id in rows:
        if tier != tier_filter or nba_player_id in outlier_ids:
            continue
        key = (season_id, nba_player_id, game_id)
        if key in seen:
            continue
        seen.add(key)
        bucket = eligibility_bucket(eligibility_counts.get(nba_player_id, 1))
        buckets[(season_id, bucket)].append(float(fantasy_score))

    by_season = defaultdict(dict)
    for (season_id, bucket), scores in buckets.items():
        by_season[season_id][bucket] = scores

    for season_id, bucket_groups in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        groups = {b: s for b, s in bucket_groups.items() if len(s) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{season_id}: not enough eligibility groups with >={MIN_GROUP_SIZE} games, skipped")
            continue
        stat, p, eps, k, n = kruskal_with_effect_size(list(groups.values()))
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(f"{season_id}: H={stat:.2f}  p={p:.4f}  eps2={eps:.4f} ({effect_label(eps)})  {verdict}")

    df_rows = [
        (bucket, score)
        for bucket_groups in by_season.values()
        for bucket, scores in bucket_groups.items()
        for score in scores
    ]
    if df_rows:
        df = pd.DataFrame(df_rows, columns=["bucket", "score"])
        counts = df["bucket"].value_counts()
        valid = counts[counts >= MIN_GROUP_SIZE].index.tolist()
        if len(valid) >= 2:
            df = df[df["bucket"].isin(valid)]
            result = sp.posthoc_dunn(df, val_col="score", group_col="bucket", p_adjust="bonferroni")
            cols = result.columns.tolist()
            sig_pairs = []
            for i, b1 in enumerate(cols):
                for b2 in cols[i + 1:]:
                    p_val = result.loc[b1, b2]
                    if p_val < 0.05:
                        sig_pairs.append(f"{b1} vs {b2} (p={p_val:.4f})")
            print(
                "Pooled-across-seasons post-hoc (outliers excluded): "
                + ("; ".join(sig_pairs) if sig_pairs else "no significant pairwise differences")
            )


def run():
    conn = get_connection()
    cur = conn.cursor()
    rows = fetch_rows(cur)
    print(f"{len(rows)} (player-game-position-tier) rows pulled from player_scores_by_position_tier.")

    # --- Original 5-way (PG/SG/SF/PF/C) analysis ---
    buckets = group_scores(rows)
    print_percentile_table(buckets, "5-way")

    by_season = build_season_groups(buckets)
    run_kruskal(by_season, "5-way, pooled across tiers, per season")

    by_season_tier = build_season_tier_groups(buckets)
    run_kruskal(by_season_tier, "5-way, within each tier, per season")

    run_levene(by_season, "5-way, pooled across tiers, per season")
    run_posthoc_dunn(buckets, "5-way")

    # --- G/F/C collapsed analysis (matches real roster slot categories) ---
    buckets_gfc = group_scores_gfc(rows)
    print_percentile_table(buckets_gfc, "G/F/C collapsed")

    by_season_gfc = build_season_groups(buckets_gfc)
    run_kruskal(by_season_gfc, "G/F/C collapsed, pooled across tiers, per season")

    by_season_tier_gfc = build_season_tier_groups(buckets_gfc)
    run_kruskal(by_season_tier_gfc, "G/F/C collapsed, within each tier, per season")

    run_levene(by_season_gfc, "G/F/C collapsed, pooled across tiers, per season")
    run_posthoc_dunn(buckets_gfc, "G/F/C collapsed")

    # --- Center vs. Non-Center (direct 2-group answer, more power than any single pairing) ---
    buckets_cvnc = group_scores_center_vs_noncenter(rows)
    print_percentile_table(buckets_cvnc, "Center vs. Non-Center")

    by_season_cvnc = build_season_groups(buckets_cvnc)
    run_mannwhitney_2group(by_season_cvnc, "pooled across tiers, per season", "C", "Non-C")

    by_season_tier_cvnc = build_season_tier_groups(buckets_cvnc)
    run_mannwhitney_2group(by_season_tier_cvnc, "within each tier, per season", "C", "Non-C")

    # --- Frontcourt (C+PF) vs. Perimeter (PG+SF+SG) -- follows Dunn's post-hoc finding
    # that C and PF aren't reliably distinguishable from each other ---
    buckets_fcp = group_scores_frontcourt_perimeter(rows)
    print_percentile_table(buckets_fcp, "Frontcourt vs. Perimeter")

    by_season_fcp = build_season_groups(buckets_fcp)
    run_mannwhitney_2group(by_season_fcp, "pooled across tiers, per season", "Frontcourt", "Perimeter")

    by_season_tier_fcp = build_season_tier_groups(buckets_fcp)
    run_mannwhitney_2group(by_season_tier_fcp, "within each tier, per season", "Frontcourt", "Perimeter")

    # --- Eligibility-count check: does flexibility (# eligible positions) predict scoring,
    # independent of which specific positions? Tests the assumption behind treating
    # flexibility as a scoring-neutral roster-construction tiebreaker. ---
    eligibility_counts = build_eligibility_counts(rows)
    buckets_elig = group_scores_by_eligibility_count(rows, eligibility_counts)
    print_percentile_table(buckets_elig, "Eligibility count (1 / 2 / 3+ positions)")

    by_season_elig = build_season_groups(buckets_elig)
    run_kruskal(by_season_elig, "eligibility count, pooled across tiers, per season")

    by_season_tier_elig = build_season_tier_groups(buckets_elig)
    run_kruskal(by_season_tier_elig, "eligibility count, within each tier, per season")

    run_levene(by_season_elig, "eligibility count, pooled across tiers, per season")
    run_posthoc_dunn(buckets_elig, "eligibility count")

    # --- Outlier-robustness check: is the elite-tier C premium broad-based or a few-players effect? ---
    # Incremental (top_n = 1, 2, 3) to see each additional excluded player's marginal contribution --
    # the same nba_player_id (203999) was excluded every season at top_n=3, so this checks whether
    # the sign flip already happens with just that one player removed.
    all_excluded_ids = set()
    for n in (1, 2, 3):
        excluded_by_season = run_outlier_robustness_check(rows, tier_filter="1_elite", top_n=n)
        for ids in excluded_by_season.values():
            all_excluded_ids.update(ids)

    # --- Does the eligibility-count finding survive removing the known outliers? ---
    print_outlier_position_sets(rows, all_excluded_ids)
    run_eligibility_check_excluding_known_outliers(rows, eligibility_counts, "1_elite", all_excluded_ids)

    # --- Games-played check: are the flagged outlier Centers durability-limited in recent seasons? ---
    run_games_played_check(cur, all_excluded_ids)

    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Position-scoring distribution analysis. With no arguments, runs the full "
        "battery of tests. With --player, runs a single-player outlier check instead."
    )
    parser.add_argument(
        "--player",
        help="nba_player_id or (partial) player name -- runs a single-player outlier check "
        "against that player's tier+position peers instead of the full battery, e.g. "
        "--player Wembanyama or --player 1641705.",
    )
    args = parser.parse_args()

    if args.player:
        conn = get_connection()
        cur = conn.cursor()
        player_id = resolve_player(cur, args.player)
        if player_id is not None:
            rows = fetch_rows(cur)
            run_single_player_outlier_check(rows, player_id)
            run_games_played_check(cur, {player_id})
        cur.close()
        conn.close()
    else:
        run()
