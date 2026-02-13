"""
Statistical projection engine for CBB matchups.

Projects tempo, scoring, totals, and spreads using SOS-adjusted efficiency.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from scripts.stats_builder import TeamSeasonProfile, LEAGUE_AVG_PPP

# Home court advantage in PPP (~3.2 pts / 68 poss, empirical)
HOME_COURT_PPP = 0.045


@dataclass
class MatchupProjection:
    home_team: str
    away_team: str

    # Tempo
    proj_tempo: float

    # Per-team projections
    home_ppp: float
    away_ppp: float
    home_pts: float
    away_pts: float

    # Game lines
    proj_total: float
    proj_spread: float   # negative = home favored

    # Free throws
    home_fta: float
    away_fta: float

    # Uncertainty (std dev)
    total_std: float
    spread_std: float
    home_pts_std: float
    away_pts_std: float

    # Win probability
    home_win_prob: float = 0.0

    # 90% confidence intervals
    home_pts_ci_lo: float = 0.0
    home_pts_ci_hi: float = 0.0
    away_pts_ci_lo: float = 0.0
    away_pts_ci_hi: float = 0.0
    total_ci_lo: float = 0.0
    total_ci_hi: float = 0.0
    spread_ci_lo: float = 0.0
    spread_ci_hi: float = 0.0


def logistic_win_prob(spread: float, spread_std: float) -> float:
    """Logistic win probability from projected spread.

    Uses k=0.175 calibrated so an 11-pt spread yields ~87% win probability.
    Fatter tails than normal CDF → more realistic upset rates.

    Args:
        spread: proj_spread (negative = home favored)
        spread_std: standard deviation of spread estimate (unused in logistic
                    but kept for API consistency; the k constant absorbs it)

    Returns:
        Home team win probability [0, 1].
    """
    k = 0.175
    # spread is away_pts - home_pts; negative means home favored
    # P(home wins) increases as spread becomes more negative
    return 1.0 / (1.0 + math.exp(k * spread))


def compute_confidence_intervals(
    value: float, std: float, confidence: float = 0.90
) -> Tuple[float, float]:
    """Symmetric confidence interval using normal quantile.

    Returns (lo, hi) such that P(lo <= X <= hi) = confidence.
    """
    # z for 90% CI = 1.645, 95% = 1.96
    from scipy.stats import norm
    alpha = 1.0 - confidence
    z = norm.ppf(1.0 - alpha / 2.0)
    return value - z * std, value + z * std


def compute_adjusted_efficiency(
    profile: TeamSeasonProfile,
    league_avg: float = LEAGUE_AVG_PPP,
) -> tuple[float, float]:
    """
    SOS-adjusted PPP.
    - If opponents had tough defenses (low sos_def_ppp), our offense is understated -> adjust up.
    - If opponents had strong offenses (high sos_off_ppp), our defense is understated -> adjust up.

    Returns (adj_off_ppp, adj_def_ppp).
    """
    if profile.sos_def_ppp > 0:
        adj_off = profile.off_ppp + (league_avg - profile.sos_def_ppp)
    else:
        adj_off = profile.off_ppp

    if profile.sos_off_ppp > 0:
        adj_def = profile.def_ppp + (league_avg - profile.sos_off_ppp)
    else:
        adj_def = profile.def_ppp

    return adj_off, adj_def


def project_tempo(
    home: TeamSeasonProfile,
    away: TeamSeasonProfile,
) -> float:
    """Harmonic mean of both teams' tempos."""
    t1 = home.avg_possessions
    t2 = away.avg_possessions
    if t1 <= 0 or t2 <= 0:
        return max(t1, t2, 65.0)
    return 2 * t1 * t2 / (t1 + t2)


def project_ppp(
    off_profile: TeamSeasonProfile,
    def_profile: TeamSeasonProfile,
    is_home: bool,
    league_avg: float = LEAGUE_AVG_PPP,
) -> float:
    """
    Project PPP for the offensive team against the defensive team.

    3-component blend:
    1. Matchup PPP = adj_off + adj_def - league_avg (log5-style, 85% weight)
    2. Recent form = last-5-game off_ppp (15% weight)
    3. Home court = +0.045 PPP if home team
    """
    adj_off, _ = compute_adjusted_efficiency(off_profile, league_avg)
    _, adj_def = compute_adjusted_efficiency(def_profile, league_avg)

    # 1. Matchup component
    matchup_ppp = adj_off + adj_def - league_avg

    # 2. Recent form
    recent_ppp = off_profile.recent_off_ppp if off_profile.recent_off_ppp > 0 else adj_off

    # 3. Blend
    blended = 0.85 * matchup_ppp + 0.15 * recent_ppp

    # Home court advantage
    if is_home:
        blended += HOME_COURT_PPP

    return blended


def project_fta(
    off_profile: TeamSeasonProfile,
    def_profile: TeamSeasonProfile,
    league_avg_fta: float = 18.0,
) -> float:
    """
    Project free throw attempts.
    40% team avg FTA + 40% opponent FTA allowed + 20% league avg.
    """
    return (
        0.4 * off_profile.avg_fta
        + 0.4 * def_profile.avg_opp_fta
        + 0.2 * league_avg_fta
    )


def estimate_uncertainty(
    home: TeamSeasonProfile,
    away: TeamSeasonProfile,
) -> tuple[float, float, float, float]:
    """
    Estimate uncertainty (std devs) from historical scoring variance.

    Applies a game-count penalty: sqrt(30/n) widens uncertainty when a team
    has played fewer than 30 games.

    Returns (total_std, spread_std, home_pts_std, away_pts_std).
    Floors: 10 for total, 6 for team total.
    """
    # Game-count penalty: fewer games → wider uncertainty
    home_penalty = math.sqrt(30.0 / max(home.games_played, 1))
    away_penalty = math.sqrt(30.0 / max(away.games_played, 1))

    home_pts_std = max(home.pts_for_std * home_penalty, 6.0)
    away_pts_std = max(away.pts_for_std * away_penalty, 6.0)

    # Total std: combined variance of both teams' scoring
    total_std = max(math.sqrt(home_pts_std**2 + away_pts_std**2), 10.0)

    # Spread std: similar to total (both sides vary)
    spread_std = max(math.sqrt(home_pts_std**2 + away_pts_std**2), 10.0)

    return total_std, spread_std, home_pts_std, away_pts_std


def project_matchup(
    home: TeamSeasonProfile,
    away: TeamSeasonProfile,
) -> MatchupProjection:
    """
    Full matchup projection: tempo, scoring, totals, spread, FTA, uncertainty,
    win probability, and 90% confidence intervals.
    """
    tempo = project_tempo(home, away)

    home_ppp = project_ppp(home, away, is_home=True)
    away_ppp = project_ppp(away, home, is_home=False)

    home_pts = tempo * home_ppp
    away_pts = tempo * away_ppp

    proj_total = home_pts + away_pts
    proj_spread = away_pts - home_pts  # negative means home favored

    home_fta = project_fta(home, away)
    away_fta = project_fta(away, home)

    total_std, spread_std, home_pts_std, away_pts_std = estimate_uncertainty(home, away)

    # Win probability via logistic model
    home_win_prob = logistic_win_prob(proj_spread, spread_std)

    # 90% confidence intervals
    home_pts_ci_lo, home_pts_ci_hi = compute_confidence_intervals(home_pts, home_pts_std)
    away_pts_ci_lo, away_pts_ci_hi = compute_confidence_intervals(away_pts, away_pts_std)
    total_ci_lo, total_ci_hi = compute_confidence_intervals(proj_total, total_std)
    spread_ci_lo, spread_ci_hi = compute_confidence_intervals(proj_spread, spread_std)

    return MatchupProjection(
        home_team=home.team,
        away_team=away.team,
        proj_tempo=tempo,
        home_ppp=home_ppp,
        away_ppp=away_ppp,
        home_pts=home_pts,
        away_pts=away_pts,
        proj_total=proj_total,
        proj_spread=proj_spread,
        home_fta=home_fta,
        away_fta=away_fta,
        total_std=total_std,
        spread_std=spread_std,
        home_pts_std=home_pts_std,
        away_pts_std=away_pts_std,
        home_win_prob=home_win_prob,
        home_pts_ci_lo=home_pts_ci_lo,
        home_pts_ci_hi=home_pts_ci_hi,
        away_pts_ci_lo=away_pts_ci_lo,
        away_pts_ci_hi=away_pts_ci_hi,
        total_ci_lo=total_ci_lo,
        total_ci_hi=total_ci_hi,
        spread_ci_lo=spread_ci_lo,
        spread_ci_hi=spread_ci_hi,
    )
