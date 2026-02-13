"""
Cross-API team name resolution.

ESPN displayName is our canonical name (also matches The Odds API).
This module resolves NCAA API `nameShort` → ESPN `displayName`.
"""

import json
import os
import pathlib
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

import requests

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
IDENTITY_CACHE = DATA_DIR / "team_identity_map.json"
OVERRIDES_FILE = DATA_DIR / "team_name_overrides.json"

ESPN_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/teams?limit=400&groups=50"
)


@dataclass
class TeamIdentity:
    espn_id: str
    display_name: str          # e.g. "Purdue Boilermakers" (canonical)
    short_display_name: str    # e.g. "Purdue"
    location: str              # e.g. "Purdue"
    abbreviation: str          # e.g. "PUR"
    nickname: str              # e.g. "Boilermakers"


def fetch_espn_teams() -> list[dict]:
    """GET all D1 teams from ESPN."""
    resp = requests.get(ESPN_TEAMS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    teams_raw = []
    for group in data.get("sports", []):
        for league in group.get("leagues", []):
            for t in league.get("teams", []):
                team = t.get("team", t)
                teams_raw.append(team)
    return teams_raw


def build_team_identity_map(force_refresh: bool = False) -> Dict[str, TeamIdentity]:
    """
    Build {canonical_name: TeamIdentity} dict.
    Caches to data/team_identity_map.json; reuses cache unless force_refresh.
    """
    if not force_refresh and IDENTITY_CACHE.exists():
        age_hrs = (time.time() - IDENTITY_CACHE.stat().st_mtime) / 3600
        if age_hrs < 168:  # 7-day cache
            with open(IDENTITY_CACHE, "r") as f:
                raw = json.load(f)
            return {k: TeamIdentity(**v) for k, v in raw.items()}

    teams_raw = fetch_espn_teams()
    identity_map: Dict[str, TeamIdentity] = {}

    for t in teams_raw:
        tid = TeamIdentity(
            espn_id=str(t.get("id", "")),
            display_name=t.get("displayName", ""),
            short_display_name=t.get("shortDisplayName", ""),
            location=t.get("location", ""),
            abbreviation=t.get("abbreviation", ""),
            nickname=t.get("nickname", ""),
        )
        if tid.display_name:
            identity_map[tid.display_name] = tid

    # persist cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(IDENTITY_CACHE, "w") as f:
        json.dump({k: asdict(v) for k, v in identity_map.items()}, f, indent=2)

    return identity_map


def _load_overrides() -> Dict[str, str]:
    """Load manual NCAA→ESPN name overrides."""
    if OVERRIDES_FILE.exists():
        with open(OVERRIDES_FILE, "r") as f:
            return json.load(f)
    return {}


def resolve_ncaa_name(
    ncaa_short: str,
    identity_map: Dict[str, TeamIdentity],
) -> Optional[str]:
    """
    Resolve an NCAA API nameShort (e.g. "Purdue") to ESPN displayName
    (e.g. "Purdue Boilermakers").

    Strategy:
    1. Check manual overrides
    2. Exact match on shortDisplayName or location
    3. Substring match on displayName
    """
    if not ncaa_short:
        return None

    # 1. Manual overrides
    overrides = _load_overrides()
    if ncaa_short in overrides:
        override_name = overrides[ncaa_short]
        if override_name in identity_map:
            return override_name
        # Override might not be in map; return it anyway as canonical
        return override_name

    # 2. Exact match on shortDisplayName or location
    for canonical, tid in identity_map.items():
        if ncaa_short == tid.short_display_name or ncaa_short == tid.location:
            return canonical

    # 3. Case-insensitive match
    ncaa_lower = ncaa_short.lower()
    for canonical, tid in identity_map.items():
        if (ncaa_lower == tid.short_display_name.lower()
                or ncaa_lower == tid.location.lower()):
            return canonical

    # 4. Substring match (NCAA name appears in ESPN displayName)
    for canonical, tid in identity_map.items():
        if ncaa_lower in canonical.lower():
            return canonical

    return None


def resolve_all_teams(
    ncaa_names: list[str],
    identity_map: Dict[str, TeamIdentity],
) -> Dict[str, Optional[str]]:
    """Resolve a list of NCAA names, returning {ncaa_name: canonical_name}."""
    resolved = {}
    unresolved = []
    for name in ncaa_names:
        canonical = resolve_ncaa_name(name, identity_map)
        resolved[name] = canonical
        if canonical is None:
            unresolved.append(name)

    if unresolved:
        print(f"[team_name_map] WARNING: {len(unresolved)} unresolved names: {unresolved}")
        print("  Add these to data/team_name_overrides.json")

    return resolved


def get_espn_id(canonical_name: str, identity_map: Dict[str, TeamIdentity]) -> Optional[str]:
    """Get ESPN team ID from canonical name."""
    tid = identity_map.get(canonical_name)
    return tid.espn_id if tid else None
