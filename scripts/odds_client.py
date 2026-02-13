"""
The Odds API client for NCAAB betting odds.

Free tier: 500 credits/month.
- All-odds call (h2h+spreads+totals): 3 credits
- Per-event call (team_totals): 1 credit each
- Budget ~13 credits/day → supports ~38 days/month
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_ncaab"


@dataclass
class OddsOutcome:
    name: str           # team name or "Over"/"Under"
    price: int          # American odds, e.g. -110, +150
    point: Optional[float] = None  # spread/total line, e.g. -3.5, 145.5


@dataclass
class OddsMarket:
    key: str            # "h2h", "spreads", "totals", "team_totals"
    outcomes: List[OddsOutcome] = field(default_factory=list)


@dataclass
class BookmakerOdds:
    key: str            # e.g. "draftkings", "fanduel"
    title: str          # e.g. "DraftKings"
    markets: List[OddsMarket] = field(default_factory=list)


@dataclass
class GameOdds:
    event_id: str
    commence_time: str
    home_team: str
    away_team: str
    bookmakers: List[BookmakerOdds] = field(default_factory=list)

    def get_consensus_line(self, market_key: str) -> Optional[OddsMarket]:
        """Get the first available bookmaker's line for a market."""
        for bk in self.bookmakers:
            for mkt in bk.markets:
                if mkt.key == market_key:
                    return mkt
        return None

    def get_best_line(self, market_key: str, side: str) -> Optional[OddsOutcome]:
        """Get best available odds for a side across all bookmakers."""
        best = None
        for bk in self.bookmakers:
            for mkt in bk.markets:
                if mkt.key != market_key:
                    continue
                for oc in mkt.outcomes:
                    if oc.name == side:
                        if best is None or oc.price > best.price:
                            best = oc
        return best

    def get_all_book_lines(self, market_key: str) -> List[tuple]:
        """Get every bookmaker's line for a market.

        Returns list of (bookmaker_title, OddsMarket) tuples.
        """
        results = []
        for bk in self.bookmakers:
            for mkt in bk.markets:
                if mkt.key == market_key:
                    results.append((bk.title, mkt))
        return results


class OddsClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.remaining_credits: Optional[int] = None
        self.used_credits: Optional[int] = None

    def _update_quota(self, resp: requests.Response):
        """Track quota from response headers."""
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if remaining is not None:
            self.remaining_credits = int(remaining)
        if used is not None:
            self.used_credits = int(used)

    def print_usage(self):
        """Print current quota status."""
        print(f"Odds API — used: {self.used_credits}, remaining: {self.remaining_credits}")

    def get_ncaab_odds(
        self,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
        bookmakers: Optional[str] = None,
        odds_format: str = "american",
    ) -> List[GameOdds]:
        """
        Fetch all NCAAB odds in one call.
        Cost: 3 credits for h2h+spreads+totals.
        """
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        url = f"{BASE_URL}/sports/{SPORT_KEY}/odds/"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        self._update_quota(resp)

        return [self._parse_event(e) for e in resp.json()]

    def get_event_odds(
        self,
        event_id: str,
        markets: str = "team_totals",
        regions: str = "us",
        odds_format: str = "american",
    ) -> Optional[GameOdds]:
        """
        Fetch odds for a single event. Cost: 1 credit.
        Use sparingly — mainly for team_totals on top games.
        """
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        url = f"{BASE_URL}/sports/{SPORT_KEY}/events/{event_id}/odds"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        self._update_quota(resp)

        data = resp.json()
        if not data:
            return None
        return self._parse_event(data)

    def _parse_event(self, event: dict) -> GameOdds:
        """Parse a single event from API response into GameOdds."""
        bookmakers = []
        for bk in event.get("bookmakers", []):
            markets = []
            for mkt in bk.get("markets", []):
                outcomes = []
                for oc in mkt.get("outcomes", []):
                    outcomes.append(OddsOutcome(
                        name=oc.get("name", ""),
                        price=oc.get("price", 0),
                        point=oc.get("point"),
                    ))
                markets.append(OddsMarket(
                    key=mkt.get("key", ""),
                    outcomes=outcomes,
                ))
            bookmakers.append(BookmakerOdds(
                key=bk.get("key", ""),
                title=bk.get("title", ""),
                markets=markets,
            ))

        return GameOdds(
            event_id=event.get("id", ""),
            commence_time=event.get("commence_time", ""),
            home_team=event.get("home_team", ""),
            away_team=event.get("away_team", ""),
            bookmakers=bookmakers,
        )
