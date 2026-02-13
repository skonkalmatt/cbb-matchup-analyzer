import csv
import datetime as dt
from typing import List, Dict, Any

import requests


# --- Config ---
SEASON_YEAR = 2024  # adjust as needed


def fetch_espn_team_schedule(team_id: int, season: int) -> List[Dict[str, Any]]:
    """
    Fetch schedule + boxscore-like stats for a given ESPN team ID and season.
    This will hit an ESPN JSON endpoint; structure may need minor tweaking
    once you see real responses.
    """
    # Example endpoint pattern (for team schedule):
    # https://site.web.api.espn.com/apis/v2/sports/basketball/mens-college-basketball/teams/{team_id}/schedule?season={season}
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/"
        f"teams/{team_id}/schedule?season={season}"
    )

    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("events", [])
    return events

def fetch_game_boxscore(event_id: str) -> Dict[str, Any]:
    """
    Fetch detailed boxscore for a single ESPN event.
    """
    # Example pattern:
    # https://site.web.api.espn.com/apis/v2/sports/basketball/mens-college-basketball/summary?event={event_id}
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/"
        f"summary?event={event_id}"
    )
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def parse_boxscore_to_rows(box: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert ESPN boxscore JSON into our canonical row schema
    for both teams in the game.

    Output rows (one per team):
      - date
      - team
      - opponent
      - pts_for
      - pts_against
      - fga
      - fta
      - orb
      - tov
      - three_pa
      - three_pm
    """
    header = box.get("header", {})
    competitions = header.get("competitions", [])
    if not competitions:
        return []

    comp = competitions[0]
    date_str = comp.get("date")
    # date_str is ISO; we normalize to YYYY-MM-DD
    date = dt.datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()

    competitors = comp.get("competitors", [])

    rows: List[Dict[str, Any]] = []

    # Stats are usually in boxscore -> players or team-stats; we want team totals
    boxscore = box.get("boxscore", {})
    teams_stats = boxscore.get("teams", [])

    # Build a map from teamId -> stats
    stats_by_team_id: Dict[str, Dict[str, Any]] = {}

    for t in teams_stats:
        team_info = t.get("team", {})
        team_id = str(team_info.get("id"))
        # "statistics" is a list of dicts with "name"/"displayValue"
        stats_list = t.get("statistics", [])
        stat_map = {s["name"]: s for s in stats_list if "name" in s}
        stats_by_team_id[team_id] = stat_map

    # Now iterate competitors to pair them
    for team_comp in competitors:
        team_info = team_comp.get("team", {})
        team_id = str(team_info.get("id"))
        team_name = team_info.get("displayName")
        score = int(team_comp.get("score", 0))

        # Opponent
        opp_comp = [c for c in competitors if c is not team_comp][0]
        opp_info = opp_comp.get("team", {})
        opp_id = str(opp_info.get("id"))
        opp_name = opp_info.get("displayName")
        opp_score = int(opp_comp.get("score", 0))

        stat_map = stats_by_team_id.get(team_id, {})

        def stat_val(name: str, default: int = 0) -> int:
            """Extract a single numeric stat from the stat map."""
            entry = stat_map.get(name)
            if not entry:
                return default
            if "value" in entry:
                try:
                    return int(float(entry["value"]))
                except Exception:
                    return default
            try:
                return int(entry.get("displayValue", default))
            except Exception:
                return default

        def stat_split(name: str, index: int, default: int = 0) -> int:
            """Extract made (index=0) or attempted (index=1) from 'M-A' displayValue."""
            entry = stat_map.get(name)
            if not entry:
                return default
            dv = entry.get("displayValue", "")
            if "-" in dv:
                parts = dv.split("-")
                try:
                    return int(parts[index])
                except (IndexError, ValueError):
                    return default
            return default

        # ESPN stat names — handle both old (separate keys) and new (combined "made-attempted") formats
        fga = stat_val("fieldGoalsAttempted", 0) or stat_split("fieldGoalsMade-fieldGoalsAttempted", 1)
        fta = stat_val("freeThrowsAttempted", 0) or stat_split("freeThrowsMade-freeThrowsAttempted", 1)
        orb = stat_val("offensiveRebounds")
        tov = stat_val("turnovers") or stat_val("totalTurnovers")
        three_pa = stat_val("threePointFieldGoalsAttempted", 0) or stat_split("threePointFieldGoalsMade-threePointFieldGoalsAttempted", 1)
        three_pm = stat_val("threePointFieldGoalsMade", 0) or stat_split("threePointFieldGoalsMade-threePointFieldGoalsAttempted", 0)

        row = {
            "date": date,
            "team": team_name,
            "opponent": opp_name,
            "pts_for": score,
            "pts_against": opp_score,
            "fga": fga,
            "fta": fta,
            "orb": orb,
            "tov": tov,
            "three_pa": three_pa,
            "three_pm": three_pm,
        }
        rows.append(row)

    return rows

def fetch_team_season_to_csv(team_id: int, season: int, output_csv: str):
    """
    Fetch all games for one ESPN team ID in a given season,
    write them as rows into output_csv (append or create).
    """
    events = fetch_espn_team_schedule(team_id, season)

    rows: List[Dict[str, Any]] = []

    for event in events:
        eid = event.get("id")
        if not eid:
            continue
        try:
            box = fetch_game_boxscore(eid)
            game_rows = parse_boxscore_to_rows(box)
            rows.extend(game_rows)
        except Exception as e:
            print(f"Error fetching/parsing event {eid}: {e}")

    if not rows:
        print("No rows fetched.")
        return

    # Write CSV
    fieldnames = [
        "date",
        "team",
        "opponent",
        "pts_for",
        "pts_against",
        "fga",
        "fta",
        "orb",
        "tov",
        "three_pa",
        "three_pm",
    ]

    # append if exists, else create
    write_header = True
    try:
        with open(output_csv, "r", newline="", encoding="utf-8") as f:
            # if file exists and non-empty, skip header
            if f.read().strip():
                write_header = False
    except FileNotFoundError:
        write_header = True

    with open(output_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Wrote {len(rows)} rows to {output_csv}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", type=int, required=True, help="ESPN team ID")
    parser.add_argument("--season", type=int, default=SEASON_YEAR)
    parser.add_argument("--output", type=str, default="data/cbb_games_2024.csv")
    args = parser.parse_args()

    fetch_team_season_to_csv(args.team_id, args.season, args.output)