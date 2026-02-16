#!/usr/bin/env python3
"""
Quick analysis - process games incrementally, report as we go.
"""
import sys
import os
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ncaa_client import NcaaClient
from scripts.odds_client import OddsClient
from scripts.stats_builder import build_team_profile, get_or_fetch_team_season
from scripts.matchup_model import project_matchup
from scripts.value_finder import analyze_game_value_all_books
from scripts.team_name_map import build_team_identity_map, resolve_ncaa_name, get_espn_id
from scripts.pick_tracker import PickTracker, Pick


def short_name(full_name: str) -> str:
    parts = full_name.split()
    return " ".join(parts[:-1]) if len(parts) > 2 else (parts[0] if parts else full_name)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Quick CBB analysis')
    parser.add_argument('--date', type=str, help='Target date YYYY-MM-DD (default: today ET)')
    args = parser.parse_args()

    if args.date:
        target_date = dt.datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        try:
            import pytz
            et = pytz.timezone('US/Eastern')
            target_date = dt.datetime.now(et).date()
        except:
            target_date = dt.date.today()

    season = target_date.year if target_date.month >= 9 else target_date.year - 1

    print(f"🏀 CBB Quick Analysis - {target_date.strftime('%A, %B %d, %Y')}\n")
    
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("No ODDS_API_KEY - projection only\n")
        return
    
    # Load resources
    print("Loading teams...")
    identity_map = build_team_identity_map()
    
    print("Fetching games...")
    ncaa = NcaaClient()
    scoreboard = ncaa.get_mens_d1_scoreboard(target_date.year, target_date.month, target_date.day)
    
    games = []
    for gwrap in scoreboard.get("games", []):
        game = gwrap.get("game", {})
        home = game.get("home", {})
        away = game.get("away", {})
        home_names = home.get("names", {})
        away_names = away.get("names", {})
        home_name = home_names.get("short") or home_names.get("full", "")
        away_name = away_names.get("short") or away_names.get("full", "")
        
        home_canonical = resolve_ncaa_name(home_name, identity_map)
        away_canonical = resolve_ncaa_name(away_name, identity_map)
        
        if home_canonical and away_canonical:
            games.append({
                'home': home_canonical,
                'away': away_canonical,
                'home_id': get_espn_id(home_canonical, identity_map) or "",
                'away_id': get_espn_id(away_canonical, identity_map) or "",
                'game_id': game.get("gameID", ""),
            })
    
    print(f"Found {len(games)} games")
    
    print("Fetching odds...")
    odds_client = OddsClient(api_key=api_key)
    all_odds = odds_client.get_ncaab_odds(regions="us", markets="h2h,spreads,totals")

    # Normalize Odds API team names -> ESPN canonical and match order-independently
    from scripts.odds_normalizer import canonicalize_game_odds, canonical_pair_key

    odds_by_pair = {}
    for go in all_odds:
        canon = canonicalize_game_odds(go, identity_map)
        if not canon:
            continue
        key = canonical_pair_key(canon.home_team, canon.away_team)
        odds_by_pair[key] = canon

    print(f"Got odds for {len(odds_by_pair)} games (canonicalized)")
    print(f"Credits remaining: {odds_client.remaining_credits}\n")
    
    # Process games incrementally
    tracker = PickTracker()
    picks_found = 0
    
    for i, g in enumerate(games[:20], 1):  # Limit to first 20 to avoid timeout
        print(f"[{i}/{min(20, len(games))}] {short_name(g['away'])} @ {short_name(g['home'])}", end=" ... ")

        from scripts.odds_normalizer import canonical_pair_key, canonicalize_game_odds
        pair_key = canonical_pair_key(g['home'], g['away'])
        base_odds = odds_by_pair.get(pair_key)
        if not base_odds:
            print("no odds")
            continue

        # Align odds object to NCAA home/away order
        game_odds = canonicalize_game_odds(base_odds, identity_map, desired_home=g['home'], desired_away=g['away'])
        if not game_odds:
            print("odds name mismatch")
            continue
        
        try:
            # Fetch team data (this is the slow part)
            home_df = get_or_fetch_team_season(g['home'], g['home_id'], season)
            away_df = get_or_fetch_team_season(g['away'], g['away_id'], season)
            
            if home_df.empty or away_df.empty:
                print("missing data")
                continue
            
            # Build profiles
            home_raw = build_team_profile(home_df, g['home'], g['home_id'])
            away_raw = build_team_profile(away_df, g['away'], g['away_id'])
            raw_profiles = {g['home']: home_raw, g['away']: away_raw}
            
            home_profile = build_team_profile(home_df, g['home'], g['home_id'], opponent_profiles=raw_profiles)
            away_profile = build_team_profile(away_df, g['away'], g['away_id'], opponent_profiles=raw_profiles)
            
            # Project
            projection = project_matchup(home_profile, away_profile)
            
            # Analyze value
            bets = analyze_game_value_all_books(projection, game_odds)
            
            # Filter to good opportunities
            good_bets = [b for b in bets if b.edge_pct > 0.03 and b.safety_score > 0.50]
            
            if good_bets:
                print(f"✅ {len(good_bets)} picks")
                for bet in good_bets[:2]:  # Show top 2 per game
                    print(f"   {bet.bet_type.upper()} {bet.bet_side.upper()} @ {bet.bookmaker} | Edge {bet.edge_pct:+.1%} | Safety {bet.safety_score:.2f}")
                    
                    # Log to DB
                    pick = Pick(
                        game_id=g['game_id'],
                        date=target_date.isoformat(),
                        game=f"{short_name(g['away'])} @ {short_name(g['home'])}",
                        bet_type=bet.bet_type,
                        bet_side=bet.bet_side,
                        bookmaker=bet.bookmaker,
                        line=bet.book_line,
                        odds=bet.book_odds,
                        model_edge=bet.edge_pct,
                        model_win_prob=bet.model_win_prob,
                        kelly_fraction=bet.kelly_fraction,
                        safety_score=bet.safety_score,
                        result='pending',
                    )
                    tracker.log_pick(pick)
                    picks_found += 1
            else:
                print("no value")
        
        except Exception as e:
            print(f"error: {e}")
    
    print(f"\n✅ Analysis complete - found {picks_found} picks")


if __name__ == "__main__":
    main()
