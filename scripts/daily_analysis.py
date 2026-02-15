#!/usr/bin/env python3
"""
Daily CBB analysis script - finds best picks and logs them.
Run via cron or manually.
"""
import sys
import os
import datetime as dt
from typing import List, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ncaa_client import NcaaClient
from scripts.odds_client import OddsClient, GameOdds
from scripts.stats_builder import build_team_profile, get_or_fetch_team_season
from scripts.matchup_model import project_matchup
from scripts.value_finder import analyze_game_value_all_books, select_best_bets
from scripts.team_name_map import build_team_identity_map, resolve_ncaa_name, get_espn_id
from scripts.pick_tracker import PickTracker, Pick


def get_season(target_date: dt.date) -> int:
    return target_date.year if target_date.month >= 9 else target_date.year - 1


def short_name(full_name: str) -> str:
    """'Purdue Boilermakers' -> 'Purdue'."""
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name
    return " ".join(parts[:-1]) if len(parts) > 2 else parts[0]


def main():
    # Get today's date
    try:
        import pytz
        et = pytz.timezone('US/Eastern')
        today = dt.datetime.now(et).date()
    except:
        today = dt.date.today()
    
    print(f"🏀 CBB Daily Analysis - {today.strftime('%A, %B %d, %Y')}")
    print("=" * 60)
    
    # Check for API key
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("⚠️  No ODDS_API_KEY found - running projection-only mode")
        print("   Get a free key at https://the-odds-api.com")
        print()
    
    # Load identity map
    print("Loading team identity map...")
    identity_map = build_team_identity_map()
    
    # Fetch today's games
    print(f"Fetching games for {today}...")
    ncaa = NcaaClient()
    scoreboard = ncaa.get_mens_d1_scoreboard(today.year, today.month, today.day)
    
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
    
    if not games:
        print("No games today - nothing to analyze")
        return
    
    # Fetch odds if available
    odds_lookup = {}
    if api_key:
        print("Fetching odds from sportsbooks...")
        try:
            odds_client = OddsClient(api_key=api_key)
            all_odds = odds_client.get_ncaab_odds(regions="us", markets="h2h,spreads,totals")
            odds_lookup = {f"{go.home_team}|{go.away_team}": go for go in all_odds}
            print(f"Got odds for {len(odds_lookup)} games")
            if odds_client.remaining_credits:
                print(f"API credits remaining: {odds_client.remaining_credits}")
        except Exception as e:
            print(f"Error fetching odds: {e}")
    
    # Analyze each game
    season = get_season(today)
    all_opportunities = []
    
    print()
    print("Analyzing matchups...")
    for i, g in enumerate(games, 1):
        print(f"  [{i}/{len(games)}] {short_name(g['away'])} @ {short_name(g['home'])}")
        
        try:
            # Load team data
            home_df = get_or_fetch_team_season(g['home'], g['home_id'], season)
            away_df = get_or_fetch_team_season(g['away'], g['away_id'], season)
            
            if home_df.empty or away_df.empty:
                print(f"    ⚠️  Missing data - skipping")
                continue
            
            # Build profiles
            home_raw = build_team_profile(home_df, g['home'], g['home_id'])
            away_raw = build_team_profile(away_df, g['away'], g['away_id'])
            raw_profiles = {g['home']: home_raw, g['away']: away_raw}
            
            home_profile = build_team_profile(home_df, g['home'], g['home_id'], opponent_profiles=raw_profiles)
            away_profile = build_team_profile(away_df, g['away'], g['away_id'], opponent_profiles=raw_profiles)
            
            # Project matchup
            projection = project_matchup(home_profile, away_profile)
            
            # Find value if we have odds
            odds_key = f"{g['home']}|{g['away']}"
            if odds_key in odds_lookup:
                game_odds = odds_lookup[odds_key]
                bets = analyze_game_value_all_books(projection, game_odds)
                
                # Filter to high-quality opportunities
                good_bets = [
                    b for b in bets 
                    if b.edge_pct > 0.03 and b.safety_score > 0.50
                ]
                
                if good_bets:
                    all_opportunities.extend(good_bets)
                    print(f"    ✅ Found {len(good_bets)} opportunities")
            else:
                print(f"    📊 Projection: {short_name(g['home'])} {projection.proj_spread:+.1f} | Total {projection.proj_total:.1f}")
        
        except Exception as e:
            print(f"    ❌ Error: {e}")
    
    # Select and display best bets
    print()
    print("=" * 60)
    
    if all_opportunities:
        best_bets = select_best_bets(all_opportunities, top_n=8)
        
        print(f"🎯 TOP PICKS ({len(best_bets)} bets)")
        print()
        
        tracker = PickTracker()
        
        for i, bet in enumerate(best_bets, 1):
            pref_icon = "⭐" if bet.in_preferred_range else "  "
            
            print(f"{pref_icon} {i}. {bet.game}")
            print(f"   {bet.bet_type.upper()} {bet.bet_side.upper()} @ {bet.bookmaker}")
            print(f"   Line: {bet.book_line or 'ML'} | Odds: {bet.book_odds:+d}")
            print(f"   Edge: {bet.edge_pct:+.1%} | Win Prob: {bet.model_win_prob:.1%} | Safety: {bet.safety_score:.2f}")
            print(f"   Kelly: {bet.kelly_fraction:.2%}")
            print()
            
            # Log pick to database
            pick = Pick(
                game_id=bet.game,  # using game string as ID for now
                date=today.isoformat(),
                game=bet.game,
                bet_type=bet.bet_type,
                bet_side=bet.bet_side,
                bookmaker=bet.bookmaker,
                line=bet.book_line,
                odds=bet.book_odds,
                model_edge=bet.edge_pct,
                model_win_prob=bet.model_win_prob,
                kelly_fraction=bet.kelly_fraction,
                safety_score=bet.safety_score,
                stake=0.0,  # Not betting yet, just tracking model picks
                result='pending',
            )
            tracker.log_pick(pick)
        
        print(f"✅ Logged {len(best_bets)} picks to database")
        
        # Save to markdown for easy review
        picks_md_path = f"picks_history/{today.isoformat()}.md"
        with open(picks_md_path, 'w') as f:
            f.write(f"# CBB Picks - {today.strftime('%A, %B %d, %Y')}\n\n")
            for i, bet in enumerate(best_bets, 1):
                pref = "⭐ PREFERRED RANGE" if bet.in_preferred_range else ""
                f.write(f"## {i}. {bet.game} {pref}\n")
                f.write(f"**{bet.bet_type.upper()} {bet.bet_side.upper()}** @ {bet.bookmaker}\n\n")
                f.write(f"- Line: {bet.book_line or 'ML'}\n")
                f.write(f"- Odds: {bet.book_odds:+d}\n")
                f.write(f"- Model Edge: {bet.edge_pct:+.1%}\n")
                f.write(f"- Win Probability: {bet.model_win_prob:.1%}\n")
                f.write(f"- Safety Score: {bet.safety_score:.2f}\n")
                f.write(f"- Kelly Fraction: {bet.kelly_fraction:.2%}\n")
                f.write(f"\n**Reasoning:** {bet.reasoning}\n\n")
                f.write("---\n\n")
        
        print(f"📄 Saved picks to {picks_md_path}")
        
        return best_bets
    else:
        print("No positive-edge opportunities found today")
        return []


if __name__ == "__main__":
    main()
