"""
scripts/inspect_playbyplay_fouls.py

ONE-OFF DIAGNOSTIC, not part of the pipeline. Pulls PlayByPlayV3 for a
single real game and prints every distinct (actionType, subType, description)
combination where actionType looks foul-related -- purpose is to confirm
the EXACT real string values NBA's API uses for technical and flagrant
fouls before building the actual backfill script.

Necessary because this can't be verified from the sandbox this project's
architecture_risks investigation is being run in (network there is
allowlisted to package registries only, not stats.nba.com) -- confirmed
from nba_api's own source that PlayByPlayV3 (not the deprecated, now-dead
PlayByPlayV2) returns actionType/subType per event, but the exact string
spelling for "technical foul" vs "flagrant foul 1" vs "flagrant foul 2"
needs to come from a real live response, not guessed.

Uses the real Jokić technical-foul game already confirmed this session
(Cam Thomas/Scottie Barnes/Haliburton/Zion/LeVert were the first 5
confirmed technicals) as a good default -- swap GAME_ID for any of
those if you have the specific game_id handy, otherwise pass one on
the command line.

Run:
    python scripts/inspect_playbyplay_fouls.py GAME_ID
Example:
    python scripts/inspect_playbyplay_fouls.py 0022400001
"""

import sys

from nba_api.stats.endpoints import playbyplayv3


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_playbyplay_fouls.py GAME_ID")
        sys.exit(1)

    game_id = sys.argv[1]
    print(f"Pulling PlayByPlayV3 for game_id={game_id}...")

    pbp = playbyplayv3.PlayByPlayV3(game_id=game_id)
    df = pbp.get_data_frames()[0]

    print(f"\n{len(df)} total events in this game.\n")
    print("All distinct actionType values seen (to confirm 'Foul' is the real label):")
    for at in sorted(df["actionType"].dropna().unique()):
        print(f"  {at!r}")

    print("\n--- All events where actionType looks foul-related ---")
    foul_like = df[df["actionType"].str.contains("foul", case=False, na=False)]
    if foul_like.empty:
        print("No rows matched 'foul' in actionType -- check the actionType list above "
              "for the real label if this looks wrong.")
    else:
        print(f"{len(foul_like)} foul-like events found. Distinct (actionType, subType) pairs:\n")
        pairs = foul_like[["actionType", "subType"]].drop_duplicates()
        for _, row in pairs.iterrows():
            print(f"  actionType={row['actionType']!r}  subType={row['subType']!r}")

        print("\nFull detail for each foul-like event (personId, description):")
        for _, row in foul_like.iterrows():
            print(f"  personId={row['personId']}  playerName={row['playerName']}  "
                  f"actionType={row['actionType']!r}  subType={row['subType']!r}  "
                  f"description={row['description']!r}")


if __name__ == "__main__":
    main()
