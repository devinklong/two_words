# Step 6 Verification Results — Historical Standings Reconciliation (8/15/26)

Final verification closing out Step 6 (2025-26 weeks 1-18 Sleeper API
instability). Every value below is cross-checked directly against the
real app's League History standings pages, not inferred. `historical_standings`
sources from `sleeper_matchup_points_snapshots`, populated via the
manually-verified `team_scores` sheet (`backfill_manual_team_points.py`)
plus 3 individually-diagnosed corrective fixes.

**Result: full exact match, both seasons, every team.**

## 2024-25 Regular Season

| Owner | DB (W-L-T) | App (W-L-T) | DB Points | App Points | Match |
|---|---|---|---|---|---|
| TommyTableSalsa | 18-3-0 | 18-3 | 8785.50 | 8785.50 | ✓ |
| Folger11 | 15-5-1 | 15-5-1 | 8537.70 | 8537.70 | ✓ |
| Hendo64 | 12-9-0 | 12-9 | 8525.65 | 8525.65 | ✓ |
| sweetdiddlydee | 12-9-0 | 12-9 | 8243.30 | 8243.30 | ✓ |
| CountyShirriff | 12-9-0 | 12-9 | 8220.55 | 8220.55 | ✓ |
| SeanKelly13 | 12-9-0 | 12-9 | 7908.60 | 7908.60 | ✓ |
| cocohebbles | 8-13-0 | 8-13 | 7515.40 | 7515.40 | ✓ |
| Yaak0v | 6-15-0 | 6-15 | 7354.25 | 7354.25 | ✓ |
| Crash374 | 5-15-1 | 5-15-1 | 7905.55 | 7905.55 | ✓ |
| Pete1771 | 4-17-0 | 4-17 | 7219.20 | 7219.20 | ✓ |

## 2025-26 Regular Season

| Owner | DB (W-L-T) | App (W-L-T) | DB Points | App Points | Match |
|---|---|---|---|---|---|
| sweetdiddlydee | 18-3-0 | 18-3 | 8687.50 | 8687.50 | ✓ |
| CountyShirriff | 15-6-0 | 15-6 | 8289.55 | 8289.55 | ✓ |
| Folger11 | 14-7-0 | 14-7 | 8601.75 | 8601.75 | ✓ |
| Crash374 | 13-8-0 | 13-8 | 8468.60 | 8468.60 | ✓ |
| Hendo64 | 12-9-0 | 12-9 | 7933.65 | 7933.65 | ✓ |
| cocohebbles | 10-11-0 | 10-11 | 7826.70 | 7826.70 | ✓ |
| Pete1771 | 9-12-0 | 9-12 | 7667.05 | 7667.05 | ✓ |
| TommyTableSalsa | 8-13-0 | 8-13 | 7505.65 | 7505.65 | ✓ |
| Yaak0v | 5-16-0 | 5-16 | 6919.10 | 6919.10 | ✓ |
| SeanKelly13 | 1-20-0 | 1-20 | 6265.85 | 6265.85 | ✓ |

## What it took to get here

**2024-25** — previously assumed "verified exactly correct across every
check run," turned out to have 5 real discrepancies once the manual
sheet forced a value-by-value diff:
- 2 were manual-entry typos (the original DB value was actually right):
  wk7/TommyTableSalsa (445.05, not 455.05), wk11/SeanKelly13 (363.65,
  not 365.65). Corrected back via a new snapshot matching the true value.
- 3 were genuine, previously-undetected errors in the live-synced data
  that the manual entry correctly caught: wk14/Crash374 (447.25, not
  397.05), wk15/Pete1771 (379.80, not 339.25), wk20/cocohebbles
  (396.80, not 416.70).

**2025-26** — weeks 1-18 corrected wholesale via the manual entry (matches
the originally-flagged unreliable range from the Step 6 investigation:
9-10 of 10 rosters changed nearly every week in that range, 0 changes in
weeks 19 and 21-24). One additional isolated typo found post-upsert:
wk20/CountyShirriff (446.50, not 466.50).

## Conclusion

Step 6 is resolved for both completed seasons. The root cause of the
Sleeper API's live-endpoint instability (six theories tested, none
confirmed — see `SLEEPER_LOCKIN_METHODOLOGY.md`) remains officially
unexplained, but every affected value now has a trusted, independently
verified replacement, and both seasons' full standings match the real
app exactly.
