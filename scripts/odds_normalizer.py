"""Odds normalization utilities.

The Odds API team names sometimes differ from ESPN displayName canonical names
(e.g. "Rutgers" vs "Rutgers Scarlet Knights").

These helpers map Odds API team strings to our ESPN-canonical names using the
existing team identity map + NCAA resolver.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional, Tuple

from scripts.odds_client import GameOdds, OddsMarket, OddsOutcome, BookmakerOdds
from scripts.team_name_map import TeamIdentity, resolve_ncaa_name


def resolve_odds_team_name(name: str, identity_map: Dict[str, TeamIdentity]) -> Optional[str]:
    """Resolve Odds API team string to our canonical ESPN displayName.

    Strategy:
    1) If already a canonical key, return
    2) Use resolve_ncaa_name (works for location/short names)
    3) Substring scan in canonical keys (fallback)
    """
    if not name:
        return None

    if name in identity_map:
        return name

    # try NCAA resolver logic (location/short name matching)
    resolved = resolve_ncaa_name(name, identity_map)
    if resolved:
        return resolved

    # final fallback: substring match
    low = name.lower()
    for canonical in identity_map.keys():
        if low == canonical.lower():
            return canonical
        if low in canonical.lower():
            return canonical

    return None


def canonicalize_game_odds(
    go: GameOdds,
    identity_map: Dict[str, TeamIdentity],
    desired_home: Optional[str] = None,
    desired_away: Optional[str] = None,
) -> Optional[GameOdds]:
    """Return a GameOdds with canonical team names (and outcomes names).

    If desired_home/away provided, force go.home_team/go.away_team to those
    canonical values (used to align with NCAA home/away).
    """
    home_can = resolve_odds_team_name(go.home_team, identity_map)
    away_can = resolve_odds_team_name(go.away_team, identity_map)

    if not home_can or not away_can:
        return None

    # Build outcome name mapping: original strings -> canonical
    name_map = {
        go.home_team: home_can,
        go.away_team: away_can,
        home_can: home_can,
        away_can: away_can,
    }

    new_books: list[BookmakerOdds] = []
    for bk in go.bookmakers:
        new_markets: list[OddsMarket] = []
        for mkt in bk.markets:
            new_outcomes: list[OddsOutcome] = []
            for oc in mkt.outcomes:
                if oc.name in ("Over", "Under"):
                    new_outcomes.append(oc)
                else:
                    new_outcomes.append(replace(oc, name=name_map.get(oc.name, oc.name)))
            new_markets.append(replace(mkt, outcomes=new_outcomes))
        new_books.append(replace(bk, markets=new_markets))

    aligned_home = desired_home or home_can
    aligned_away = desired_away or away_can

    return replace(
        go,
        home_team=aligned_home,
        away_team=aligned_away,
        bookmakers=new_books,
    )


def canonical_pair_key(team_a: str, team_b: str) -> Tuple[str, str]:
    """Order-independent pair key."""
    return tuple(sorted([team_a, team_b]))
