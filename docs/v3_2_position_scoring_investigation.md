# v3.2 — Position-Based Scoring Investigation (full log)

Full detail for the position-scoring work summarized in
`methodology_notes.md`. That doc has the conclusions and practical
guidance; this doc has every test, every result, and the two real
corrections the investigation went through along the way — kept here
in full rather than repeated there, same pattern as
`SLEEPER_LOCKIN_METHODOLOGY.md` / `v2_roadmap_section.md`.

---

## Part 1 — Data foundation: the crosswalk was wrong

`sleeper_player_crosswalk.sleeper_position` was sourced from Sleeper's
SINGULAR `position` field, not the real `fantasy_positions` array that
holds multi-eligibility. Diagnostic run against all 394 crosswalked
players: **268/394 (68%) are multi-position-eligible** in real Sleeper
data — the normal case, not an edge case. 4 additional players had a
stored position that didn't even appear in their own `fantasy_positions`
(Herbert Jones, Dyson Daniels, Quentin Grimes, Ethan Thompson) — all 4
confirmed against the real Sleeper app on mobile: the new table matched
the app in every case, the old singular field did not, in every case.

**Fix:** `sleeper_player_fantasy_positions` — one row per eligible
position, FK to the crosswalk, ON DELETE CASCADE. Full delete-then-
reinsert per player on each sync (matches `roster_ownership`'s
current-state-only precedent — Sleeper reassigns eligibility mid-season,
never historically tracked, confirmed intentional). Diagnostic confirmed
only 6 distinct values ever appear across Sleeper's whole directory: C,
PF, PG, SF, SG, DEF (DEF = team-defense, filtered out, never applies to
individual players). No generic slot labels (F/G/UTIL) ever appear
player-side.

---

## Part 2 — Player-side analysis (5 seasons, ~146k rows)

**Framing, deliberate:** "do position groups show different score
distributions" — descriptive, not predictive. The position table is
current-state-only, so applying today's labels retroactively to past
seasons can't support a genuine predictive claim without risking the
same drift just confirmed on 4 real players.

**Method:** `player_scores_by_position_tier` (new view, joins existing
tables). `analyze_position_scoring_distributions.py` — Kruskal-Wallis
(not ANOVA, right-skewed data), epsilon-squared effect size, Levene's
test, Dunn's post-hoc, Mann-Whitney for 2-group splits, plus later
additions: outlier-robustness, real games-played, single-player CLI
mode. All 5 backfilled seasons (22021-22025).

### Headline result
Nearly every test is statistically significant (large N detects tiny
differences), but almost every epsilon-squared is "negligible" —
position explains very little variance overall. One durable exception:
**elite-tier C-vs-Non-C is significant all 5/5 seasons, always positive**
(rank-biserial +0.06 to +0.12). Mid tier: noise in 4/5 seasons across
every test type. Lower/replacement tiers: C is the LOWEST-scoring
position nearly every season-tier combo — in the untiered/waiver pool
specifically, C is dead last all 5 seasons.

### Dunn's post-hoc nuance
C vs. PF is only significant in 2021 — every other season they're
statistically indistinguishable, while C differs from PG/SF/SG almost
every year. Suggested "frontcourt (C+PF) vs. perimeter" as the real
dividing line — **later directly tested and rejected** (see below):
Frontcourt vs. Perimeter was only significant at the elite tier in 2/5
seasons, weaker than plain Center-vs-Non-Center. Pulling PF in dilutes
rather than sharpens the signal.

### Variance/ceiling correction saga (worth keeping in full — a real
mid-investigation course-correction)
Levene's significant every season; elite-tier C has the highest stdev
in 4/5 seasons — centers are more boom-bust, not just higher-average.
**First take:** treated a tied-ceiling C as implying a lower floor than
a tied-ceiling guard, called it a caution against valuing tied-ceiling
C higher. **User correction:** that only applies to a same-CEILING
comparison; for a same-MEAN comparison, higher stdev is pure upside,
no cost. **Further correction:** even in the same-ceiling case, the
"lower floor" downside doesn't really bite here, because Lock-In mode
is hold-until-spike — bad games get held/skipped, not banked, so floor
matters far less than in a must-play-every-game format. **Net final
take:** the variance finding reinforces prioritizing elite centers, it
doesn't undercut it.

### Outlier-robustness — the decisive test
Excluding the top 3 highest-average Centers per season flips the
elite-tier sign entirely: positive/significant 5/5 seasons before
exclusion (rank-biserial +0.06 to +0.12) → negative/significant 3/5
seasons after (rank-biserial -0.04 to -0.12). Excluded nba_player_ids:
`203999` every single season (Jokić), `203076` (Anthony Davis) 3/5
seasons, `203954` (Embiid) 3/5 seasons.

**Incremental exclusion (top_n=1,2,3):** Jokić alone already collapses
the signal from significant-positive-every-season to noise in 4/5
seasons — he's the dominant single driver. Takes 2-3 total exclusions
to flip the sign fully negative (Embiid, AD, and in 2024/2025 Victor
Wembanyama, `1641705`, contribute the rest).

**Real games-played** (`game_logs`, all 5 seasons): Embiid's
participation collapsed 83%→80%→46%→**23%**→46%; AD's collapsed
49%→67%→90%→62%→**24%**. Jokić stayed steady 90%/83%/94%/85%/79% every
season — durable AND dominant, unlike the other two.

**Conclusion:** the entire elite-tier Center premium is carried by 2-3
recurring outlier players, not a broad positional effect. "Prioritize
elite centers" → "prioritize these specific outlier players; Embiid/AD
box-score averages no longer reflect real recent-season value given
severe durability collapse."

### Eligibility-count check — a second, independent finding
Tests whether HOW MANY positions a player is eligible for (1 vs. 2 vs.
3+) predicts scoring, independent of which specific ones. At the elite
tier, single-position players significantly outscore multi-eligible
ones, **and this survives excluding all 6 known outliers** (still
significant in 4/5 seasons after exclusion; pooled post-hoc p<0.02 for
every pairwise comparison). Outside the elite tier this doesn't hold
(mixed/non-significant elsewhere).

**Implication:** flexibility is NOT a free, scoring-neutral tiebreaker
at the top of the talent pool — real cost to preferring a multi-eligible
elite player over a single-position one, all else equal. Outside elite,
flexibility as a tiebreaker still holds (position barely predicts
scoring for the large majority of players).

### Single-player outlier check (`--player` CLI mode)
Generalizes the outlier test to any one player by name or ID — compares
their distribution against tier+position peers via Mann-Whitney,
reports the same real games-played numbers.

- **Wembanyama:** rookie season (2023) not yet a statistical outlier
  (rank-biserial +0.030, noise). 2024/2025: real, significant outlier
  both seasons (rank-biserial +0.264, +0.293) — stronger individual
  edge than the aggregate Center signal ever showed even with Jokić
  included. Durability: 85%→56%→78% (2024 dip = documented blood-clot
  diagnosis, recovering since).
- **Ivica Zubac** (test case for "solid but not transcendent"):
  positive outlier at `3_lower` tier in 2021/2022 (rank-biserial +0.16
  to +0.21), flips to significant NEGATIVE once promoted to `2_mid`/
  `1_elite` tier (2023/2024, rank-biserial -0.22/-0.20), back to noise
  in 2025. A clean example of the trap the whole investigation warns
  about: good box score + favorable tier label ≠ a real outlier at the
  tier he's actually compared against.

### Roster-flexibility math (discussion, not a script — pure
combinatorics on the real 9-slot format `{PG,SG,G,SF,PF,F,C,UTIL,
UTIL,BN×6}`)
Slots fillable = (# distinct positions) + (1 if any guard) + (1 if any
forward) + 2 (both UTILs always). Full ranking: 9 (all 5 positions), 8
(any 4-combo), 7 (most 3-combos, including PG/PF/C), 6 (one guard + one
forward pair), 5 (same-side pairs, or single position + C), 4 (any
single perimeter position alone), **3 (C alone — the genuine floor, not
PG alone as first guessed)**. Confirmed: PF/C only scores 5, not 6 (no
G-slot credit); PG/PF/C ties with 8 other 3-combos at 7, not uniquely
2nd-best.

### Elite-vs-replacement gap
C has the widest elite-to-replacement mean gap of any position (~33 pts
vs. ~27-30 for others, averaged across 5 seasons) — combined with C's
least-flexible roster math (3 slots, no generic backup), a 3-way
argument (highest ceiling, lowest floor, least flexibility) for why
getting an elite Center specifically matters more than at other
positions.

---

## Part 3 — Team-side analysis (2 real seasons: 2024, 2025)

### Blocker 1: starters[]/roster_positions[] alignment — CLEARED
`check_starters_roster_positions_alignment.sql` — confirms
`sleeper_matchups.starters[i]` reliably corresponds to
`sleeper_leagues.roster_positions[i]`: **90.83% exact match.** The
~9% gap is fully explained by known position-eligibility drift
(current-state-only table applied retroactively), not a real indexing
bug:
- **By season:** clean monotonic decline — 2024: 14.72% → 2025: 7.22%
  → 2026: 5.56% (older-worse/recent-better, exactly as drift predicts).
- **By slot:** concentrated at SG specifically (19.31% mismatch vs.
  3-5% for PG/G) — a position genuinely prone to real-world guard
  reclassification over a career.

**Editor lesson, now a standing file convention:** a comment placed
between a CTE's closing `)` and the `SELECT` that consumes it breaks
VS Code's SQL-extension statement-boundary detection — runs the bare
CTE alone, produces a false "syntax error at or near LIMIT" from the
extension's auto-appended row-limit. Fix: every comment sits above its
`WITH...SELECT` block, never inside it.

### Blocker 2: player_scores was never actually a live table — CLEARED
Discovered `player_scores` was validated (`verify_player_scores_
against_xlsx.py`, reads the xlsx directly) but never ingested into a
real table. Built `schema/tables/player_scores.sql` (keyed
`league_id`/`week`/`roster_id`/`sleeper_player_id`, matching
`team_scores`' key shape for a direct join to `sleeper_matchups` —
deliberately not `season_id`/`week_number`) + `backfill_manual_
player_scores.py` (plain upsert — NOT `team_scores`' append-only
change-log pattern, since player_scores has no competing live-sync
source needing history preserved). Run against `2024_2025_all_scores.
xlsx`: 4172 upserted, 148 skipped as BYE/NULL sentinels — exactly
matching the known-good 148 count from the original verify script.

### Slot-value analysis
`locked_scores_by_slot.sql` (joins `player_scores` to real slot via
the confirmed alignment, +tier +season) + `analyze_slot_scoring_
distributions.py` — outlier-robustness run by DEFAULT alongside the
full pass, not added after the fact, per the direct lesson from Part 2.
3231 non-UTIL locked rows + 909 UTIL rows, both seasons.

**Result survives outlier exclusion almost entirely** (effect sizes
barely move removing Jokić/Embiid/AD/Wemby) — a real, broad slot
effect, not a few-stars artifact. Effect sizes notably larger than
player-side (mostly "small" 0.03-0.09, one "medium"). Levene's NOT
significant either season — slots don't differ in spread here, only
typical level (a real contrast with the player-side variance finding).

**Key finding, directly opposite Part 2's replacement-tier result:
Center SLOT is consistently the HIGHEST-scoring slot at every tier
except elite** (2_mid/3_lower/None, both seasons, outliers excluded).

**Secondary:** generic G-flex slot consistently one of the weakest
slots (well below dedicated PG/SG most cells) — likely gets whichever
guard is left over. UTIL sits lower-middle of the pack (mean ~35-37 vs.
low-mid 40s for position slots) — not a hidden value booster in
practice.

### Resolving the contradiction: selectivity vs. intrinsic distribution
Two hypotheses for why C wins at the team level despite losing at the
player level: **H1** owners are more selective locking their one C slot
specifically (no generic C-flex the way G/F have); **H2** Centers just
have a fatter intrinsic right tail — the same selection criteria
applied everywhere would still favor C, no special behavior needed.

`analyze_selectivity_vs_intrinsic_distribution.py` (Python — see
incident note below for why not SQL):

- **Selectivity test:** average percentile rank of locked scores within
  their own position's raw distribution. Every position clusters
  tightly at 74-79%, both seasons — SF even edges out C in 2024
  (78.4 vs 76.8), PG edges it out in 2025 (78.7 vs 77.9). **No C-specific
  selectivity — H1 rejected.**
- **Right-tail test:** `(p90-p50)/(p50-p10)` per raw distribution,
  zero behavioral component. C's ratio is highest or near-highest at
  EVERY tier, both seasons, and gets MORE pronounced at lower tiers:
  1.37 (2024 untiered), 1.48 (2025 untiered) vs. 1.0-1.2 for other
  positions same tiers. **H2 confirmed directly.**

**Conclusion:** owners apply the same locking discipline everywhere;
Centers just have a genuinely fatter right tail — exactly what
`lock_bar` (`mean + 0.5*stddev`) is designed to exploit. **New wrinkle:**
replacement-level C has BOTH the lowest raw mean AND the highest
right-tail ratio simultaneously — worst average bet, biggest
spike-relative-to-floor upside. A real, non-obvious case for a
replacement-level Center as a speculative hold in a hold-until-spike
system specifically, even though its typical output is weakest.

### Postgres incident (real, worth keeping as a lesson)
First version of the selectivity test used per-row correlated
subqueries against `player_scores_by_position_tier` (a view built on a
~146k-row join) — recomputed that join on every locked row, ran 30+
seconds. User killed the query, but the backend process didn't
actually die — held a shared-memory lock, and every subsequent
`brew services restart postgresql@18` silently failed ("pre-existing
shared memory block still in use") without an obviously-connected
error message. Root cause found via Postgres's own log
(`$(brew --prefix)/var/log/postgresql@18.log`) and confirmed via
`ps aux | grep postgres` showing the zombie process pegged at 100% CPU
for 15+ minutes. Fixed: `kill -9 <pid>`, `rm postmaster.pid`, restart —
Postgres recovered cleanly via its own WAL redo.

**Standing convention adopted as a result:** statistical/analytical
checks in this project should be Python (cheap single-pass SQL SELECT
+ numpy/pandas for the actual math), matching every other script in
this suite — not multi-CTE SQL files attempting percentile/rank math
in-database against expensive views. (A `MATERIALIZED` CTE fix was
tried first and also didn't resolve the underlying issue — Postgres
12+ no longer treats CTEs as an automatic optimization fence, so even
that patch wasn't guaranteed correct; Python sidesteps the whole class
of problem structurally.)

---

## Practical guidance (final, both analyses combined)

- **Trade/keeper valuation:** don't pay a position premium for "elite
  Center" as a category — pay for specific, provably-outlier players
  (checkable via `--player`). Treat Embiid/AD's recent-season averages
  with real skepticism given their durability collapse. Jokić and
  (recently) Wembanyama are legitimately safe premium assets.
- **Waiver/injury replacement:** avoid a single-position Center pickup
  over a comparable flexible G/F on average-scoring grounds — but a
  replacement-level Center's disproportionate spike/right-tail upside
  is a real, separate consideration for a speculative hold specifically.
- **Roster construction generally:** flexibility is a legitimate
  tiebreaker when position is a wash (true for the large majority of
  non-elite players) — but not free at the elite tier, where
  single-position players show a genuine scoring edge independent of
  known outliers.
- **Team-side slot usage:** the C slot has real spike-driven value
  regardless of tier — worth deprioritizing the generic G-flex slot in
  favor of dedicated PG/SG when there's a real choice.
- **Untested, flagged for future work:** whether "single-position
  elite" correlates with usage-rate concentration (a plausible
  explanation for the eligibility-count finding) was never checked
  against usage data — a hypothesis, not a confirmed mechanism.
- **Not yet built:** none of this is wired into `waiver_wire_finder.py`
  / `opponent_scout.py` — findings delivered as analysis only.
