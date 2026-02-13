import requests
from typing import Dict, Any


class NcaaClient:
    """
    Minimal client for ncaa-api.henrygd.me

    We currently use:
      - /schedule/basketball-men/d1/{year}/{month}
      - /scoreboard/basketball-men/d1/{year}/{month}/{day}/all-conf
      - /game/{id}/team-stats
    """

    def __init__(self, base_url: str = "https://ncaa-api.henrygd.me"):
        self.base_url = base_url.rstrip("/")

    def get_mens_d1_schedule(self, year: int, month: int) -> Dict[str, Any]:
        path = f"/schedule/basketball-men/d1/{year}/{month:02d}"
        url = self.base_url + path
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_mens_d1_scoreboard(self, year: int, month: int, day: int) -> Dict[str, Any]:
        """
        GET /scoreboard/basketball-men/d1/{year}/{month}/{day}/all-conf
        analogous to the football example in the README. 
        """
        path = f"/scoreboard/basketball-men/d1/{year}/{month:02d}/{day:02d}/all-conf"
        url = self.base_url + path
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_game_team_stats(self, game_id: str) -> Dict[str, Any]:
        """
        GET /game/{id}/team-stats 
        """
        path = f"/game/{game_id}/team-stats"
        url = self.base_url + path
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

