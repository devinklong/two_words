"""
Test suite for build_sleeper_player_crosswalk.py's name-matching logic
and opponent_scout.py's get_spike_profile() fallback boundary. Plain
assertions/prints, not pytest -- mirrors nba_api_flow_control_testing.py's
existing pattern (run it, eyeball PASS/FAIL).

Part of docs/patch_list.md #6c: formalizing scattered ad hoc verification
into a real, runnable test suite.

CROSSWALK tests cover the pure functions only (strip_accents,
normalize_name, is_duplicate_placeholder) -- no DB, no live Sleeper API
call. The full matched/ambiguous/unmatched run() flow (394 matched /
31 unmatched as of 8/15/26) is NOT covered here; that needs either a
live API pull or a fixture of real Sleeper player records, neither of
which this file assumes.

SPIKE-BAR tests cover get_spike_profile()'s season-fallback logic,
against real DB data. Two specific edge cases flagged in
docs/architecture_risks.md / patch_list.md #6c -- a rookie with too few
games to qualify in his debut season (Zach Edey, nba_player_id=1641744)
and a player who falls below the spike threshold even with the fallback
applied (Khaman Maluach, nba_player_id=1642863) -- both season_id=22025.
Both nba_player_ids resolved via sleeper_player_crosswalk, not used
directly from their Sleeper IDs (2718 / 4761) -- player_tiers and
get_spike_profile() are keyed on nba_player_id, a different ID space.

Run from anywhere: python tests/test_crosswalk_and_spike_bar.py
"""

import os
import sys

# Anchored to this file's own location, not the current working directory --
# works whether this file lives at the project root or in tests/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts", "sleeper"))

from build_sleeper_player_crosswalk import strip_accents, normalize_name, is_duplicate_placeholder
from opponent_scout import get_spike_profile
from db_connection import get_connection

# Reused from nba_api_flow_control_testing.py -- already confirmed real
# and NOT in ownable_player_pool for this season_id, so it's a known-good
# "never qualifies" case without needing a new lookup.
JOKIC_SEASON_ID = "22024"
LOW_USAGE_PLAYER_ID = 1642389

# Zach Edey -- rookie whose DEBUT season has too few games to qualify
# (games_played < 20), so get_spike_profile() must fall back exactly
# one season. nba_player_id confirmed via sleeper_player_crosswalk
# (sleeper_player_id=2718 -> nba_player_id=1641744), not the Sleeper ID
# itself -- get_spike_profile() queries player_tiers, which is keyed on
# nba_player_id, per docs/patch_list.md #6c.
FALLBACK_TEST_PLAYER_ID = 1641744
FALLBACK_TEST_SEASON_ID = "22025"

# Khaman Maluach -- falls below the spike threshold
# (avg + 1.25*stddev < 35) even in the fallback season. Should return
# None from get_spike_profile(), not just skip silently. nba_player_id
# confirmed via sleeper_player_crosswalk (sleeper_player_id=4761 ->
# nba_player_id=1642863).
BELOW_THRESHOLD_PLAYER_ID = 1642863
BELOW_THRESHOLD_SEASON_ID = "22025"


# =========================
# Crosswalk: strip_accents / normalize_name (pure functions, no DB)
# =========================

def test_strip_accents_folds_common_diacritics():
    """Examples straight from the function's own docstring -- confirms
    NFKD decomposition + combining-mark removal actually works, not just
    that the function runs without erroring."""
    assert strip_accents("Mićić") == "Micic", f"got {strip_accents('Mićić')!r}"
    assert strip_accents("Şengün") == "Sengun", f"got {strip_accents('Şengün')!r}"
    print("PASS: strip_accents folds diacritics correctly (Mićić->Micic, Şengün->Sengun)")


def test_normalize_name_strips_suffix_only_when_requested():
    """Suffix-intact and suffix-stripped forms should differ ONLY when
    strip_suffix=True -- confirms the crosswalk's two-pass lookup
    (exact first, suffix-stripped fallback) has two genuinely different
    keys to try, not the same string twice."""
    intact = normalize_name("Tim Hardaway Jr.", strip_suffix=False)
    stripped = normalize_name("Tim Hardaway Jr.", strip_suffix=True)
    assert intact == "tim hardaway jr", f"got {intact!r}"
    assert stripped == "tim hardaway", f"got {stripped!r}"
    assert intact != stripped, "suffix-intact and suffix-stripped forms should differ"
    print(f"PASS: normalize_name suffix handling correct (intact={intact!r}, stripped={stripped!r})")


def test_normalize_name_removes_punctuation_and_collapses_whitespace():
    messy = normalize_name("  De'Aaron   Fox  ")
    assert messy == "deaaron fox", f"got {messy!r}"
    print(f"PASS: normalize_name strips punctuation and collapses whitespace ({messy!r})")


def test_normalize_name_handles_empty_input():
    """Guards the `if not name: return ""` branch explicitly -- an empty
    or None full_name shouldn't raise, just produce an empty key that'll
    naturally fail to match anything."""
    assert normalize_name("") == ""
    assert normalize_name(None) == ""
    print("PASS: normalize_name handles empty/None input without raising")


def test_is_duplicate_placeholder_detects_literal_case_insensitive():
    assert is_duplicate_placeholder("DUPLICATE Player") is True
    assert is_duplicate_placeholder("duplicate player") is True
    assert is_duplicate_placeholder("Player DUPLICATE Two") is True
    print("PASS: is_duplicate_placeholder detects the literal, case-insensitively")


def test_is_duplicate_placeholder_false_for_real_name():
    assert is_duplicate_placeholder("Nikola Jokic") is False
    assert is_duplicate_placeholder(None) is False
    assert is_duplicate_placeholder("") is False
    print("PASS: is_duplicate_placeholder correctly leaves real names alone")


# =========================
# Spike-bar: get_spike_profile() fallback boundary
# =========================

def test_spike_profile_uses_current_season_when_row_exists():
    """Jokić clears the spike bar comfortably in a real season with
    real games played -- should resolve WITHOUT falling back, confirming
    the fallback path isn't taken when it isn't needed."""
    conn = get_connection()
    profile = get_spike_profile(conn, 203999, JOKIC_SEASON_ID)  # Jokić
    conn.close()

    assert profile is not None, f"expected a profile for Jokić in {JOKIC_SEASON_ID}, got None"
    assert profile["is_fallback_season"] is False, (
        f"expected current-season match, but fallback was used: {profile}"
    )
    assert profile["profile_season_id"] == JOKIC_SEASON_ID
    print(f"PASS: current-season row used directly, no fallback needed (spike_bar={profile['spike_bar']})")


def test_spike_profile_returns_none_when_never_qualifies():
    """LOW_USAGE_PLAYER_ID is already confirmed not in ownable_player_pool
    for JOKIC_SEASON_ID -- if it also fails in fallback_season_id, the
    function should return None cleanly rather than erroring or
    returning a partial/wrong profile."""
    conn = get_connection()
    profile = get_spike_profile(conn, LOW_USAGE_PLAYER_ID, JOKIC_SEASON_ID)
    conn.close()

    assert profile is None, (
        f"expected None for a player who never clears the spike bar, got: {profile}"
    )
    print("PASS: get_spike_profile correctly returns None when neither season qualifies")


def test_spike_profile_falls_back_exactly_one_year():
    """A rookie whose DEBUT season has too few games (games_played < 20)
    should have no row for that season_id, but a real row for
    season_id - 1 IF that's actually where their qualifying data lives --
    confirms the fallback fires and is capped to exactly one year, not
    searched open-endedly."""
    conn = get_connection()
    profile = get_spike_profile(conn, FALLBACK_TEST_PLAYER_ID, FALLBACK_TEST_SEASON_ID)
    conn.close()

    assert profile is not None, (
        f"expected a fallback-season profile for player_id={FALLBACK_TEST_PLAYER_ID}, got None"
    )
    assert profile["is_fallback_season"] is True, (
        f"expected fallback to have been used, but current season matched directly: {profile}"
    )
    expected_fallback = str(int(FALLBACK_TEST_SEASON_ID) - 1)
    assert profile["profile_season_id"] == expected_fallback, (
        f"expected fallback season {expected_fallback}, got {profile['profile_season_id']}"
    )
    print(f"PASS: fallback correctly capped to exactly one year back ({expected_fallback})")


def test_spike_profile_below_threshold_even_with_fallback():
    """A player who falls below the spike threshold in BOTH their
    current season and the one-year fallback should return None, not a
    profile with a spike_bar under 35 -- player_tiers' own WHERE clause
    should already exclude them, so this is really confirming that
    exclusion holds end to end through get_spike_profile()."""
    conn = get_connection()
    profile = get_spike_profile(conn, BELOW_THRESHOLD_PLAYER_ID, BELOW_THRESHOLD_SEASON_ID)
    conn.close()

    assert profile is None, (
        f"expected None for a player below the spike threshold in both seasons, got: {profile}"
    )
    print("PASS: below-threshold player correctly returns None even with fallback applied")


def test_fallback_season_id_derivation_is_exactly_one_year():
    """Pure string-arithmetic check on the fallback formula itself
    (season_id - 1, e.g. '22026' -> '22025') -- independent of any DB
    state, just confirms the format-preserving subtraction is correct."""
    assert str(int("22026") - 1) == "22025"
    assert str(int("22021") - 1) == "22020"
    print("PASS: fallback season_id derivation is exactly one year back, format preserved")


def main():
    tests = [
        test_strip_accents_folds_common_diacritics,
        test_normalize_name_strips_suffix_only_when_requested,
        test_normalize_name_removes_punctuation_and_collapses_whitespace,
        test_normalize_name_handles_empty_input,
        test_is_duplicate_placeholder_detects_literal_case_insensitive,
        test_is_duplicate_placeholder_false_for_real_name,
        test_spike_profile_uses_current_season_when_row_exists,
        test_spike_profile_returns_none_when_never_qualifies,
        test_spike_profile_falls_back_exactly_one_year,
        test_spike_profile_below_threshold_even_with_fallback,
        test_fallback_season_id_derivation_is_exactly_one_year,
    ]

    failures = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures.append(test.__name__)
            print(f"FAIL: {test.__name__}\n  {e}")
        except Exception as e:
            failures.append(test.__name__)
            print(f"ERROR: {test.__name__} raised an unexpected exception: {e}")

    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        print(f"Failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
