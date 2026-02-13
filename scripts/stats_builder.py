"""
Fetch season data and build team statistical profiles.

Uses ESPN API (via fetch_espn_cbb) for box scores, with CSV caching
to avoid re-fetching within 12 hours.
"""

import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from scripts.fetch_espn_cbb import (
    fetch_espn_team_schedule,
    fetch_game_boxscore,
    parse_boxscore_to_rows,
)

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
SEASONS_DIR = DATA_DIR / "seasons"

# League average PPP (D1 men's basketball ~1.00 historically)
LEAGUE_AVG_PPP = 1.00


def estimate_possessions(row: pd.Series) -> float:
    """Poss = FGA - ORB + TOV + 0.475 * FTA"""
    return row["fga"] - row["orb"] + row["tov"] + 0.475 * row["fta"]


@dataclass
class TeamSeasonProfile:
    team: str
    espn_id: str
    games_played: int

    # Tempo
    avg_possessions: float
    tempo_std: float

    # Efficiency
    off_ppp: float
    def_ppp: float
    eff_margin: float
    off_ppp_std: float
    def_ppp_std: float

    # Scoring
    avg_pts_for: float
    avg_pts_against: float
    pts_for_std: float
    pts_against_std: float

    # Fouls
    avg_fta: float
    avg_fta_rate: float
    avg_opp_fta: float

    # Shooting
    three_rate: float
    three_pct: float
    orb_pct: float

    # SOS (strength of schedule) — filled in second pass
    sos_off_ppp: float = 0.0
    sos_def_ppp: float = 0.0
    sos_eff_margin: float = 0.0

    # Recent form (last 5 games)
    recent_off_ppp: float = 0.0
    recent_def_ppp: float = 0.0
    recent_avg_pts: float = 0.0


def _slug(name: str) -> str:
    """Convert team name to filesystem-safe slug."""
    return name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("'", "")


def fetch_team_season_data(espn_id: str, season: int) -> pd.DataFrame:
    """
    Fetch all completed game box scores for an ESPN team ID.
    Returns a DataFrame with our canonical schema + computed possessions/PPP.
    """
    events = fetch_espn_team_schedule(int(espn_id), season)
    all_rows: List[dict] = []

    for event in events:
        eid = event.get("id")
        if not eid:
            continue
        # Only completed games
        status = event.get("competitions", [{}])[0].get("status", {})
        status_type = status.get("type", {}).get("name", "")
        if status_type not in ("STATUS_FINAL", ""):
            # Skip in-progress or scheduled games
            if status_type == "STATUS_SCHEDULED":
                continue

        try:
            box = fetch_game_boxscore(eid)
            game_rows = parse_boxscore_to_rows(box)
            all_rows.extend(game_rows)
        except Exception as e:
            print(f"  [stats_builder] Error fetching event {eid}: {e}")

        # Rate limit: 1 request/sec to be polite to ESPN
        time.sleep(1.0)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    # Filter out future/unplayed games (all stats are 0)
    df = df[df["fga"] > 0].copy()
    if df.empty:
        return pd.DataFrame()

    df["poss"] = df.apply(estimate_possessions, axis=1)

    # Guard against zero possessions (shouldn't happen after fga>0 filter, but be safe)
    df = df[df["poss"] > 0].copy()

    df["off_ppp"] = df["pts_for"] / df["poss"]
    df["def_ppp"] = df["pts_against"] / df["poss"]
    return df


def get_or_fetch_team_season(
    name: str,
    espn_id: str,
    season: int,
) -> pd.DataFrame:
    """
    CSV cache layer. Returns cached DataFrame if cache <12hrs old,
    otherwise fetches fresh data.
    """
    season_dir = SEASONS_DIR / str(season)
    season_dir.mkdir(parents=True, exist_ok=True)
    csv_path = season_dir / f"{_slug(name)}.csv"

    if csv_path.exists():
        age_hrs = (time.time() - csv_path.stat().st_mtime) / 3600
        if age_hrs < 12:
            df = pd.read_csv(csv_path)
            # Filter out unplayed games that may have been cached
            df = df[df["fga"] > 0].copy()
            if "poss" not in df.columns:
                df["poss"] = df.apply(estimate_possessions, axis=1)
                df = df[df["poss"] > 0].copy()
                df["off_ppp"] = df["pts_for"] / df["poss"]
                df["def_ppp"] = df["pts_against"] / df["poss"]
            return df

    print(f"  Fetching season data for {name} (ESPN ID: {espn_id})...")
    df = fetch_team_season_data(espn_id, season)

    if not df.empty:
        df.to_csv(csv_path, index=False)

    return df


def build_team_profile(
    df: pd.DataFrame,
    team_name: str,
    espn_id: str,
    opponent_profiles: Optional[Dict[str, "TeamSeasonProfile"]] = None,
) -> TeamSeasonProfile:
    """
    Build a TeamSeasonProfile from a team's season DataFrame.

    Pass opponent_profiles (keyed on canonical name) to compute SOS.
    If None, SOS fields default to 0.
    """
    team_df = df[df["team"] == team_name].copy()
    if team_df.empty:
        raise ValueError(f"No games found for {team_name}")

    n = len(team_df)

    # Tempo
    avg_poss = team_df["poss"].mean()
    tempo_std = team_df["poss"].std() if n > 1 else 0.0

    # Efficiency
    off_ppp = team_df["off_ppp"].mean()
    def_ppp = team_df["def_ppp"].mean()
    eff_margin = off_ppp - def_ppp
    off_ppp_std = team_df["off_ppp"].std() if n > 1 else 0.0
    def_ppp_std = team_df["def_ppp"].std() if n > 1 else 0.0

    # Scoring
    avg_pts_for = team_df["pts_for"].mean()
    avg_pts_against = team_df["pts_against"].mean()
    pts_for_std = team_df["pts_for"].std() if n > 1 else 0.0
    pts_against_std = team_df["pts_against"].std() if n > 1 else 0.0

    # Fouls
    avg_fta = team_df["fta"].mean()
    avg_fta_rate = (team_df["fta"] / team_df["fga"]).mean() if (team_df["fga"] > 0).all() else 0.0

    # Opponent FTA: look at what opponents shot against us
    # We need the opponent's rows from the same games
    opp_fta_values = []
    for _, row in team_df.iterrows():
        opp_name = row["opponent"]
        game_date = row["date"]
        opp_row = df[(df["team"] == opp_name) & (df["date"] == game_date)]
        if not opp_row.empty:
            opp_fta_values.append(opp_row["fta"].iloc[0])
    avg_opp_fta = np.mean(opp_fta_values) if opp_fta_values else avg_fta

    # Shooting
    if "three_pa" in team_df.columns and (team_df["fga"] > 0).all():
        three_rate = (team_df["three_pa"] / team_df["fga"]).mean()
    else:
        three_rate = 0.0

    if "three_pm" in team_df.columns and "three_pa" in team_df.columns:
        valid = team_df["three_pa"] > 0
        three_pct = (team_df.loc[valid, "three_pm"] / team_df.loc[valid, "three_pa"]).mean() if valid.any() else 0.0
    else:
        three_pct = 0.0

    if "orb" in team_df.columns and "fga" in team_df.columns:
        misses = team_df["fga"] - team_df.get("three_pm", 0)
        valid = misses > 0
        orb_pct = (team_df.loc[valid, "orb"] / misses[valid]).mean() if valid.any() else 0.0
    else:
        orb_pct = 0.0

    # Recent form (last 5 games by date)
    recent = team_df.sort_values("date").tail(5)
    recent_off_ppp = recent["off_ppp"].mean()
    recent_def_ppp = recent["def_ppp"].mean()
    recent_avg_pts = recent["pts_for"].mean()

    # SOS
    sos_off_ppp = 0.0
    sos_def_ppp = 0.0
    sos_eff_margin = 0.0

    if opponent_profiles:
        opp_off_ppps = []
        opp_def_ppps = []
        for opp_name in team_df["opponent"].unique():
            if opp_name in opponent_profiles:
                opp_prof = opponent_profiles[opp_name]
                opp_off_ppps.append(opp_prof.off_ppp)
                opp_def_ppps.append(opp_prof.def_ppp)
        if opp_off_ppps:
            sos_off_ppp = np.mean(opp_off_ppps)
            sos_def_ppp = np.mean(opp_def_ppps)
            sos_eff_margin = sos_off_ppp - sos_def_ppp

    return TeamSeasonProfile(
        team=team_name,
        espn_id=espn_id,
        games_played=n,
        avg_possessions=avg_poss,
        tempo_std=tempo_std,
        off_ppp=off_ppp,
        def_ppp=def_ppp,
        eff_margin=eff_margin,
        off_ppp_std=off_ppp_std,
        def_ppp_std=def_ppp_std,
        avg_pts_for=avg_pts_for,
        avg_pts_against=avg_pts_against,
        pts_for_std=pts_for_std,
        pts_against_std=pts_against_std,
        avg_fta=avg_fta,
        avg_fta_rate=avg_fta_rate,
        avg_opp_fta=avg_opp_fta,
        three_rate=three_rate,
        three_pct=three_pct,
        orb_pct=orb_pct,
        sos_off_ppp=sos_off_ppp,
        sos_def_ppp=sos_def_ppp,
        sos_eff_margin=sos_eff_margin,
        recent_off_ppp=recent_off_ppp,
        recent_def_ppp=recent_def_ppp,
        recent_avg_pts=recent_avg_pts,
    )
