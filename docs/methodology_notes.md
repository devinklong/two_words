# Methodology Notes

## Lock/Hold Decision Logic — Self-Relative vs. League-Relative Thresholds

**Initial framing (README v1):** Compare a completed game's score against a
player's own rolling 5-game/10-game average and standard deviation to flag it
as LOCK / PASS / EVALUATE.

**Problem identified:** This only answers "is this a good game for this
specific player," not the actual question that matters — "is this score good
enough for my roster slot, given what's competitively available across the
league." These are different questions, and self-relative thresholds can give
the wrong answer in both directions:

- A player with a low mean and high variance could trigger a LOCK under
  self-relative logic despite the score being below what's actually
  competitive for a lockable slot.
- A player with a high, stable mean (e.g. a top-tier star) sitting exactly at
  their own average might actually be worth holding, since it's statistically
  likely they beat that number later in the week.

**Decision: two-layer approach, league-relative first.**

1. **League-relative benchmark** — an absolute scoring bar (independent of
   the specific player) representing "good enough to stop worrying about it."
2. **Player-relative probability layer** — only applied *if* a score falls
   below the league-relative bar: use the player's own rolling mean/stdev to
   estimate the likelihood of a better score later in the week (hold-value).

The tool's real value is concentrated in players who don't average 40+
fantasy points/game — high-average stars clear the league-relative bar
automatically most nights, so the player-relative layer mainly matters for
the much larger role-player population.

## Status (as of 8/3/26)

Fantasy scoring formula is confirmed and implemented as `game_fantasy_scores`
(per-game fantasy score for every row in `game_logs`, all 5 seasons),
verified against a real Sleeper result (Jokić vs. Phoenix, 3/7/25 — computed
113.1 against a confirmed actual score of 113.10). This also empirically
resolved that double-double (+3) and triple-double (+5) bonuses stack rather
than one replacing the other.

`player_season_fantasy_stats` aggregates that view to per-player, per-season
averages/stddev, including a `mean_plus_1sd` column for testing the
player-relative layer's threshold hypothesis. Validated at 2,598
player-seasons across 5 years — only ~4.8% (125) average 40+ fantasy
points/game, confirming the target population assumption.

**Open, unresolved:** the actual league-relative benchmark number itself.
Both views above are currently built on the full NBA player universe, not
scoped to this league's ~160-210 actually-ownable players (10 teams x 16
active roster spots, plus non-scoring IR/taxi slots) — that rescoping needs
Sleeper roster data, not yet integrated. The league-relative bar can't be
finalized until that scoping is in place.

Known accepted limitation: `game_logs` has no technical/flagrant foul
columns, so the -2 penalties for those aren't reflected in `fantasy_score`.
Accepted as-is — a technical/flagrant is usually accompanied by other
negative signals in the same game, so it's unlikely to be invisible in
practice even without being explicitly modeled.
