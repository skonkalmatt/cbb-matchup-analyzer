#!/usr/bin/env python3
"""
Parallel team data cache builder - fetch multiple teams concurrently.
"""
import sys
import os
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ncaa_client import NcaaClient
from scripts.team_name_map import build_team_identity_map, resolve_ncaa_name, get_espn_id
from scripts.stats_builder import get_or_fetch_team_season


def fetch_team_data(team_info: Tuple[str, str, int]) -> Tuple[str, bool, str]:
    """
    Fetch data for a single team.
    Returns (team_name, success, message)
    """
    team, espn_id, season = team_info
    
    try:
        df = get_or_fetch_team_season(team, espn_id, season)
        if df.empty:
            return (team, False, "empty dataframe")
        return (team, True, f"{len(df)} games")
    except Exception as e:
        return (team, False, str(e))


def get_uncached_teams(date: dt.date, season: int) -> List[Tuple[str, str, int]]:
    """Get list of teams that need caching for a given date."""
    ncaa = NcaaClient()
    scoreboard = ncaa.get_mens_d1_scoreboard(date.year, date.month, date.day)
    identity_map = build_team_identity_map()
    
    teams_needed = set()
    
    for gwrap in scoreboard.get('games', []):
        g = gwrap.get('game', {})
        home = g.get('home', {}).get('names', {}).get('short', '')
        away = g.get('away', {}).get('names', {}).get('short', '')
        
        home_canonical = resolve_ncaa_name(home, identity_map)
        away_canonical = resolve_ncaa_name(away, identity_map)
        
        for team in [home_canonical, away_canonical]:
            if team:
                cache_path = f'data/seasons/{season}/{team.lower().replace(" ", "_")}.csv'
                if not os.path.exists(cache_path):
                    espn_id = get_espn_id(team, identity_map)
                    if espn_id:
                        teams_needed.add((team, espn_id, season))
    
    return list(teams_needed)


def build_cache_parallel(teams: List[Tuple[str, str, int]], max_workers: int = 4, verbose: bool = True):
    """
    Build cache for multiple teams in parallel.
    max_workers: number of concurrent fetches (default 4 to be nice to ESPN)
    """
    if not teams:
        if verbose:
            print("All teams already cached")
        return
    
    if verbose:
        print(f"Fetching {len(teams)} teams with {max_workers} workers...")
    
    success_count = 0
    fail_count = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_team = {executor.submit(fetch_team_data, team_info): team_info[0] 
                          for team_info in teams}
        
        # Process as they complete
        for future in as_completed(future_to_team):
            team, success, message = future.result()
            
            if success:
                success_count += 1
                status = "✅"
            else:
                fail_count += 1
                status = "❌"
            
            if verbose:
                print(f"  [{success_count + fail_count}/{len(teams)}] {status} {team}: {message}")
    
    if verbose:
        print(f"\nComplete: {success_count} success, {fail_count} failed")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Build team data cache in parallel')
    parser.add_argument('--date', type=str, help='Date to cache (YYYY-MM-DD), defaults to today')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel workers')
    parser.add_argument('--all', action='store_true', help='Cache all uncached teams, not just today')
    
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
    
    print(f"🏀 Cache Builder - {target_date.strftime('%Y-%m-%d')}")
    print(f"Season: {season}-{str(season + 1)[-2:]}")
    print("=" * 60)
    
    teams_needed = get_uncached_teams(target_date, season)
    
    if not teams_needed:
        print("✅ All teams already cached!")
        return
    
    print(f"Found {len(teams_needed)} uncached teams")
    
    # Estimate time
    avg_fetch_time = 40  # seconds per team
    parallel_time = (len(teams_needed) * avg_fetch_time) / args.workers / 60
    print(f"Estimated time: {parallel_time:.1f} minutes with {args.workers} workers")
    print()
    
    build_cache_parallel(teams_needed, max_workers=args.workers)


if __name__ == "__main__":
    main()
