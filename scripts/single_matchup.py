#!/usr/bin/env python3
"""Analyze a single matchup by canonical team names.

This avoids whole-slate joins and is useful to validate name mapping and
produce a pick for a specific game (e.g., Houston vs Iowa State).

Example:
  ODDS_API_KEY=... python3 scripts/single_matchup.py \
    --home "Iowa State Cyclones" --away "Houston Cougars" \
    --home-ml -135 --away-ml 114
"""

import argparse
import os
import sys
import datetime as dt

# Ensure project root on path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.team_name_map import build_team_identity_map, get_espn_id
from scripts.stats_builder import build_team_profile, get_or_fetch_team_season
from scripts.matchup_model import project_matchup
from scripts.value_finder import american_to_implied_prob


def get_season(target_date: dt.date) -> int:
    return target_date.year if target_date.month >= 9 else target_date.year - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (for season selection)', default=None)
    ap.add_argument('--home', required=True)
    ap.add_argument('--away', required=True)
    ap.add_argument('--home-ml', type=int, default=None)
    ap.add_argument('--away-ml', type=int, default=None)
    args = ap.parse_args()

    if args.date:
        d = dt.datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        d = dt.date.today()

    season = get_season(d)

    identity = build_team_identity_map()
    home_id = get_espn_id(args.home, identity) or ''
    away_id = get_espn_id(args.away, identity) or ''

    if not home_id or not away_id:
        raise SystemExit(f"Could not resolve ESPN ids: home={home_id} away={away_id}")

    home_df = get_or_fetch_team_season(args.home, home_id, season)
    away_df = get_or_fetch_team_season(args.away, away_id, season)

    home_raw = build_team_profile(home_df, args.home, home_id)
    away_raw = build_team_profile(away_df, args.away, away_id)
    raw_profiles = {args.home: home_raw, args.away: away_raw}

    home_prof = build_team_profile(home_df, args.home, home_id, opponent_profiles=raw_profiles)
    away_prof = build_team_profile(away_df, args.away, away_id, opponent_profiles=raw_profiles)

    proj = project_matchup(home_prof, away_prof)

    print(f"Matchup: {args.away} @ {args.home} | season {season}-{str(season+1)[-2:]}")
    print(f"Model spread (away-home): {proj.proj_spread:+.1f}  | total: {proj.proj_total:.1f}")
    print(f"Win prob (home): {proj.home_win_prob:.1%}")

    if args.home_ml is not None and args.away_ml is not None:
        home_impl = american_to_implied_prob(args.home_ml)
        away_impl = american_to_implied_prob(args.away_ml)
        # normalize implied probs (removes vig roughly)
        s = home_impl + away_impl
        home_impl_n = home_impl / s
        away_impl_n = away_impl / s

        home_edge = proj.home_win_prob - home_impl_n
        away_edge = (1 - proj.home_win_prob) - away_impl_n

        print("\nMoneyline")
        print(f"  Home ML {args.home_ml:+d} implied(vig-adj) {home_impl_n:.1%} edge {home_edge:+.1%}")
        print(f"  Away ML {args.away_ml:+d} implied(vig-adj) {away_impl_n:.1%} edge {away_edge:+.1%}")


if __name__ == '__main__':
    main()
