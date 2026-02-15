#!/usr/bin/env python3
"""
Automatic result scraper - checks completed games and updates pick results.
"""
import sys
import os
import datetime as dt
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ncaa_client import NcaaClient
from scripts.team_name_map import build_team_identity_map, resolve_ncaa_name
from scripts.pick_tracker import PickTracker
import sqlite3


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal for profit calculation."""
    if odds > 0:
        return 1 + (odds / 100)
    else:
        return 1 + (100 / abs(odds))


def check_spread_result(pick_side: str, pick_line: float, home_score: int, away_score: int) -> str:
    """
    Check if a spread bet won/lost/pushed.
    pick_side: 'home' or 'away'
    pick_line: the point spread (negative = favorite)
    """
    if pick_side == 'home':
        # Home team spread
        adjusted_score = home_score + pick_line
        margin = adjusted_score - away_score
    else:
        # Away team spread
        adjusted_score = away_score + pick_line
        margin = adjusted_score - home_score
    
    if abs(margin) < 0.5:
        return 'push'
    elif margin > 0:
        return 'win'
    else:
        return 'loss'


def check_total_result(pick_side: str, pick_line: float, home_score: int, away_score: int) -> str:
    """Check if a total (over/under) bet won/lost/pushed."""
    actual_total = home_score + away_score
    
    if abs(actual_total - pick_line) < 0.5:
        return 'push'
    elif pick_side == 'over':
        return 'win' if actual_total > pick_line else 'loss'
    else:  # under
        return 'win' if actual_total < pick_line else 'loss'


def check_moneyline_result(pick_side: str, home_score: int, away_score: int) -> str:
    """Check if a moneyline bet won/lost."""
    if pick_side == 'home':
        return 'win' if home_score > away_score else 'loss'
    else:
        return 'win' if away_score > home_score else 'loss'


def calculate_profit(result: str, odds: int, stake: float = 1.0) -> float:
    """Calculate profit for a bet result."""
    if result == 'push':
        return 0.0
    elif result == 'win':
        if odds > 0:
            return stake * (odds / 100)
        else:
            return stake * (100 / abs(odds))
    else:  # loss
        return -stake


def update_pick_results(date: dt.date, verbose: bool = True):
    """
    Check games from a specific date and update pick results.
    """
    if verbose:
        print(f"Checking results for {date.strftime('%Y-%m-%d')}...")
    
    # Get scoreboard
    ncaa = NcaaClient()
    scoreboard = ncaa.get_mens_d1_scoreboard(date.year, date.month, date.day)
    
    identity_map = build_team_identity_map()
    
    # Build game results lookup
    game_results = {}
    completed_count = 0
    
    for gwrap in scoreboard.get('games', []):
        game = gwrap.get('game', {})
        state = game.get('gameState', '')
        
        if state != 'final':
            continue
        
        completed_count += 1
        
        home_data = game.get('home', {})
        away_data = game.get('away', {})
        
        home_name = home_data.get('names', {}).get('short') or home_data.get('names', {}).get('full', '')
        away_name = away_data.get('names', {}).get('short') or away_data.get('names', {}).get('full', '')
        
        home_canonical = resolve_ncaa_name(home_name, identity_map)
        away_canonical = resolve_ncaa_name(away_name, identity_map)
        
        if not home_canonical or not away_canonical:
            continue
        
        try:
            home_score = int(home_data.get('score', 0))
            away_score = int(away_data.get('score', 0))
        except (ValueError, TypeError):
            continue
        
        game_key = f"{away_canonical} @ {home_canonical}"
        game_results[game_key] = {
            'home': home_canonical,
            'away': away_canonical,
            'home_score': home_score,
            'away_score': away_score,
        }
    
    if verbose:
        print(f"Found {completed_count} completed games")
    
    # Get pending picks from that date
    tracker = PickTracker()
    conn = sqlite3.connect(tracker.db_path)
    c = conn.cursor()
    
    c.execute("""
        SELECT id, game, bet_type, bet_side, line, odds
        FROM picks
        WHERE date = ? AND result = 'pending'
    """, (date.isoformat(),))
    
    picks = c.fetchall()
    
    if verbose:
        print(f"Found {len(picks)} pending picks")
    
    updated = 0
    
    for pick_id, game, bet_type, bet_side, line, odds in picks:
        # Match game
        result_data = game_results.get(game)
        
        if not result_data:
            if verbose:
                print(f"  No result found for: {game}")
            continue
        
        home_score = result_data['home_score']
        away_score = result_data['away_score']
        
        # Determine result
        if bet_type == 'spread':
            result = check_spread_result(bet_side, line, home_score, away_score)
        elif bet_type == 'total':
            result = check_total_result(bet_side, line, home_score, away_score)
        elif bet_type == 'moneyline':
            result = check_moneyline_result(bet_side, home_score, away_score)
        else:
            if verbose:
                print(f"  Unknown bet type: {bet_type}")
            continue
        
        # Calculate profit (assuming 1 unit stake)
        profit = calculate_profit(result, odds, stake=1.0)
        
        # Update database
        c.execute("""
            UPDATE picks
            SET result = ?, profit = ?
            WHERE id = ?
        """, (result, profit, pick_id))
        
        updated += 1
        
        if verbose:
            print(f"  ✅ {game} | {bet_type} {bet_side} | {result.upper()} | {profit:+.2f}u")
    
    conn.commit()
    conn.close()
    
    if verbose:
        print(f"\nUpdated {updated} picks")
    
    return updated


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Update pick results from completed games')
    parser.add_argument('--date', type=str, help='Date to check (YYYY-MM-DD), defaults to yesterday')
    parser.add_argument('--days', type=int, default=1, help='Number of days back to check')
    
    args = parser.parse_args()
    
    if args.date:
        check_date = dt.datetime.strptime(args.date, '%Y-%m-%d').date()
        update_pick_results(check_date)
    else:
        # Check last N days
        today = dt.date.today()
        for i in range(1, args.days + 1):
            check_date = today - dt.timedelta(days=i)
            print(f"\n{'='*60}")
            update_pick_results(check_date)
        
        # Show summary
        print(f"\n{'='*60}")
        print("PERFORMANCE SUMMARY")
        print('='*60)
        
        tracker = PickTracker()
        summary = tracker.get_performance_summary()
        
        if summary:
            print(f"Total picks: {summary['total_picks']}")
            print(f"Record: {summary['wins']}-{summary['losses']}-{summary['pushes']}")
            print(f"Win rate: {summary['win_rate']:.1%}")
            print(f"Total profit: {summary['total_profit']:+.2f} units")
            print(f"ROI: {summary['roi']:+.2f} units per pick")
            print(f"Avg edge: {summary['avg_edge']:+.1%}")
            print(f"Avg safety: {summary['avg_safety']:.2f}")
        else:
            print("No completed picks yet")


if __name__ == "__main__":
    main()
