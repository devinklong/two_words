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

- A player with a low mean and high variance (e.g. 25-point average, 33 stdev)
  could trigger a LOCK under self-relative logic despite the score being below
  what's actually competitive for a lockable slot.
- A player with a high, stable mean (e.g. LeBron averaging ~40/game) sitting
  exactly at their own average might actually be worth holding, since it's
  statistically likely they beat that number by 1+ stdev later in the week.

League context: lockable scores are generally close to a 28-point mean, but a
competitive target is closer to 40+ per slot (~400+/week across 9 slots).
Self-relative logic doesn't account for this external bar at all.

**Direction forward (not yet implemented):**
Decision logic likely needs two layered pieces instead of one formula:

1. **League-relative benchmark** — an absolute or league-wide scoring bar
   (independent of the specific player) representing "good enough to stop
   worrying about it," accounting for roster construction (9 lockable slots,
   16 total bench options — more replacement flexibility than a naive
   "average of all scorers" framing suggests).
2. **Player-relative probability layer** — only applied *if* a score falls
   below the league-relative bar: use the player's own rolling mean/stdev to
   estimate the likelihood of a better score later in the week (hold-value).

This reframes the original Decision Engine scope — it likely needs some
notion of league-wide scoring distribution as an input, not just per-player
game logs. This may be sourced from Sleeper's own historical/league data
(scores actually locked and rostered across the league), which connects to
the already-planned Sleeper API integration rather than requiring an
additional new data source.

**Status:** Documented for future implementation. Not yet reflected in
schema or README — current v1.0 scope still describes the self-relative
version. To be revisited once core data pipeline (nba_api, Sleeper API,
nbainjuries) is functional.
