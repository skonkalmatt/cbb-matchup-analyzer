#!/usr/bin/env python3
"""Odds-driven slate analysis.

Problem: NCAA scoreboard game list often doesn't line up 1:1 with Odds API events
(name differences, neutral sites, missing games, etc.).

Solution: Use Odds API events as the source of truth for which games have lines,
canonicalize team names to ESPN displayName, then run projections for those.

This guarantees we only analyze games that actually have odds.

Usage:
  ODDS_API_KEY=... python3 scripts/odds_driven_analysis.py --date 2026-02-16

Output:
  Prints top opportunities and logs them to picks_history/picks.db
"""

import os
import sys
import argparse
import datetime as dt

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.odds_client import OddsClient
from scripts.team_name_map import build_team_identity_map, get_espn_id
from scripts.odds_normalizer import canonicalize_game_odds
from scripts.stats_builder import build_team_profile, get_or_fetch_team_season
from scripts.matchup_model import project_matchup
from scripts.value_finder import analyze_game_value_all_books, select_best_bets
from scripts.pick_tracker import PickTracker, Pick


def get_season(target_date: dt.date) -> int:
    return target_date.year if target_date.month >= 9 else target_date.year - 1


def short_name(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name
    return " ".join(parts[:-1]) if len(parts) > 2 else parts[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='YYYY-MM-DD (controls season selection only)', default=None)
    ap.add_argument('--top', type=int, default=8)
    args = ap.parse_args()

    if args.date:
        target_date = dt.datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        try:
            import pytz
            et = pytz.timezone('US/Eastern')
            target_date = dt.datetime.now(et).date()
        except Exception:
            target_date = dt.date.today()

    season = get_season(target_date)

    api_key = os.environ.get('ODDS_API_KEY', '')
    if not api_key:
        raise SystemExit('ODDS_API_KEY not set')

    print(f"🏀 Odds-driven analysis | date={target_date} | season={season}-{str(season+1)[-2:]}")

    identity = build_team_identity_map()

    client = OddsClient(api_key=api_key)
    all_odds = client.get_ncaab_odds(regions='us', markets='h2h,spreads,totals')
    print(f"Odds events: {len(all_odds)} | credits remaining: {client.remaining_credits}")

    opportunities = []

    for go in all_odds:
        canon = canonicalize_game_odds(go, identity)
        if not canon:
            continue

        home = canon.home_team
        away = canon.away_team

        home_id = get_espn_id(home, identity) or ''
        away_id = get_espn_id(away, identity) or ''
        if not home_id or not away_id:
            continue

        try:
            home_df = get_or_fetch_team_season(home, home_id, season)
            away_df = get_or_fetch_team_season(away, away_id, season)
            if home_df.empty or away_df.empty:
                continue

            home_raw = build_team_profile(home_df, home, home_id)
            away_raw = build_team_profile(away_df, away, away_id)
            raw_profiles = {home: home_raw, away: away_raw}
            home_prof = build_team_profile(home_df, home, home_id, opponent_profiles=raw_profiles)
            away_prof = build_team_profile(away_df, away, away_id, opponent_profiles=raw_profiles)

            proj = project_matchup(home_prof, away_prof)

            bets = analyze_game_value_all_books(proj, canon)
            good = [b for b in bets if b.edge_pct > 0.03 and b.safety_score > 0.50]
            opportunities.extend(good)

        except Exception:
            continue

    if not opportunities:
        print('No opportunities found (after filtering).')
        return

    best = select_best_bets(opportunities, top_n=args.top)

    print(f"\n🎯 TOP PICKS ({len(best)} bets)")

    tracker = PickTracker()
    for i, b in enumerate(best, 1):
        pref = '⭐ ' if b.in_preferred_range else ''
        print(f"{pref}{i}. {b.game} | {b.bet_type.upper()} {b.bet_side.upper()} @ {b.bookmaker}")
        print(f"   Line: {b.book_line if b.book_line else 'ML'} | Odds: {b.book_odds:+d}")
        print(f"   Edge: {b.edge_pct:+.1%} | WinProb: {b.model_win_prob:.1%} | Safety: {b.safety_score:.2f} | Kelly: {b.kelly_fraction:.2%}")

        pick = Pick(
            game_id=b.game,
            date=target_date.isoformat(),
            game=b.game,
            bet_type=b.bet_type,
            bet_side=b.bet_side,
            bookmaker=b.bookmaker,
            line=b.book_line,
            odds=b.book_odds,
            model_edge=b.edge_pct,
            model_win_prob=b.model_win_prob,
            kelly_fraction=b.kelly_fraction,
            safety_score=b.safety_score,
            result='pending',
        )
        tracker.log_pick(pick)

    print(f"\n✅ Logged {len(best)} picks to picks_history/picks.db")


if __name__ == '__main__':
    main()
