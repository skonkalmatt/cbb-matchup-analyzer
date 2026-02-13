"""
Compare model projections to sportsbook odds and rank betting opportunities.

Uses normal CDF (scipy.stats.norm) to estimate P(bet wins).
"""

from dataclasses import dataclass
from typing import List, Optional

from scipy.stats import norm

from scripts.matchup_model import MatchupProjection
from scripts.odds_client import GameOdds, OddsMarket


@dataclass
class BetOpportunity:
    game: str               # "Home vs Away"
    bet_type: str           # "spread", "total", "team_total", "moneyline"
    bet_side: str           # "home", "away", "over", "under"
    book_line: float        # the line from the book (e.g. -3.5, 145.5)
    book_odds: int          # American odds (e.g. -110)
    model_projection: float # our projected value
    edge_points: float      # model - book (positive = value on this side)
    edge_pct: float         # model_win_prob - implied_prob
    model_win_prob: float   # P(bet wins) from normal CDF
    implied_prob: float     # from book odds
    confidence: str         # "high", "medium", "low"
    category: str           # "safe", "value"
    reasoning: str


def american_to_implied_prob(odds: int) -> float:
    """
    Convert American odds to implied probability.
    -110 → 52.4%, +150 → 40.0%
    """
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def compute_model_win_prob(
    model_value: float,
    book_line: float,
    std_dev: float,
) -> float:
    """
    P(bet wins) via normal CDF.

    For spreads: P(actual_spread < book_spread) for home team.
    For totals: P(actual_total > book_total) for over.
    """
    if std_dev <= 0:
        std_dev = 10.0
    # How many std devs the model is from the book line
    z = (model_value - book_line) / std_dev
    return norm.cdf(z)


def _analyze_spread(
    proj: MatchupProjection,
    odds: GameOdds,
) -> List[BetOpportunity]:
    """Analyze spread bets."""
    bets = []
    spread_mkt = odds.get_consensus_line("spreads")
    if not spread_mkt:
        return bets

    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in spread_mkt.outcomes:
        if oc.point is None:
            continue

        # Determine if this is home or away spread
        if oc.name == odds.home_team:
            # Home spread: book says home wins by -point (e.g. -3.5 means home favored by 3.5)
            # Our model spread: away_pts - home_pts (negative = home favored)
            # P(home covers) = P(actual_margin > -spread_line)
            # where actual_margin = home_pts - away_pts
            model_margin = proj.home_pts - proj.away_pts
            book_margin = -oc.point  # flip sign: if line is -3.5, team needs to win by 3.5
            model_prob = compute_model_win_prob(model_margin, book_margin, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = model_margin - book_margin

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="spread",
                bet_side="home",
                book_line=oc.point,
                book_odds=oc.price,
                model_projection=proj.proj_spread,
                edge_points=edge_pts,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model: {proj.home_team} by {model_margin:.1f}, book: {oc.point}",
            ))
        elif oc.name == odds.away_team:
            model_margin = proj.away_pts - proj.home_pts
            book_margin = -oc.point
            model_prob = compute_model_win_prob(model_margin, book_margin, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = model_margin - book_margin

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="spread",
                bet_side="away",
                book_line=oc.point,
                book_odds=oc.price,
                model_projection=proj.proj_spread,
                edge_points=edge_pts,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model: {proj.away_team} by {model_margin:.1f}, book: {oc.point}",
            ))

    return bets


def _analyze_total(
    proj: MatchupProjection,
    odds: GameOdds,
) -> List[BetOpportunity]:
    """Analyze over/under total bets."""
    bets = []
    total_mkt = odds.get_consensus_line("totals")
    if not total_mkt:
        return bets

    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in total_mkt.outcomes:
        if oc.point is None:
            continue

        if oc.name == "Over":
            # P(actual_total > book_total)
            model_prob = compute_model_win_prob(proj.proj_total, oc.point, proj.total_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = proj.proj_total - oc.point

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="total",
                bet_side="over",
                book_line=oc.point,
                book_odds=oc.price,
                model_projection=proj.proj_total,
                edge_points=edge_pts,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model total: {proj.proj_total:.1f}, book: {oc.point}",
            ))
        elif oc.name == "Under":
            # P(actual_total < book_total)
            model_prob = 1.0 - compute_model_win_prob(proj.proj_total, oc.point, proj.total_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = oc.point - proj.proj_total

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="total",
                bet_side="under",
                book_line=oc.point,
                book_odds=oc.price,
                model_projection=proj.proj_total,
                edge_points=edge_pts,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model total: {proj.proj_total:.1f}, book: {oc.point}",
            ))

    return bets


def _analyze_moneyline(
    proj: MatchupProjection,
    odds: GameOdds,
) -> List[BetOpportunity]:
    """Analyze moneyline (h2h) bets."""
    bets = []
    ml_mkt = odds.get_consensus_line("h2h")
    if not ml_mkt:
        return bets

    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in ml_mkt.outcomes:
        if oc.name == odds.home_team:
            # P(home wins) = P(home_pts > away_pts)
            model_margin = proj.home_pts - proj.away_pts
            model_prob = compute_model_win_prob(model_margin, 0, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="moneyline",
                bet_side="home",
                book_line=0,
                book_odds=oc.price,
                model_projection=model_margin,
                edge_points=model_margin,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model: {proj.home_team} win prob {model_prob:.1%}, book implied: {implied:.1%}",
            ))
        elif oc.name == odds.away_team:
            model_margin = proj.away_pts - proj.home_pts
            model_prob = compute_model_win_prob(model_margin, 0, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied

            bets.append(BetOpportunity(
                game=game_label,
                bet_type="moneyline",
                bet_side="away",
                book_line=0,
                book_odds=oc.price,
                model_projection=model_margin,
                edge_points=model_margin,
                edge_pct=edge,
                model_win_prob=model_prob,
                implied_prob=implied,
                confidence=_confidence(edge),
                category="",
                reasoning=f"Model: {proj.away_team} win prob {model_prob:.1%}, book implied: {implied:.1%}",
            ))

    return bets


def _confidence(edge_pct: float) -> str:
    """Classify confidence based on edge percentage."""
    if edge_pct >= 0.10:
        return "high"
    elif edge_pct >= 0.05:
        return "medium"
    else:
        return "low"


def analyze_game_value(
    projection: MatchupProjection,
    game_odds: GameOdds,
) -> List[BetOpportunity]:
    """
    Analyze all available markets for a single game.
    Returns all bet opportunities sorted by edge_pct descending.
    """
    bets = []
    bets.extend(_analyze_spread(projection, game_odds))
    bets.extend(_analyze_total(projection, game_odds))
    bets.extend(_analyze_moneyline(projection, game_odds))

    # Sort by edge_pct descending
    bets.sort(key=lambda b: b.edge_pct, reverse=True)
    return bets


def select_best_bets(
    all_opportunities: List[BetOpportunity],
    top_n: int = 5,
) -> List[BetOpportunity]:
    """
    Select the best bets across all games.

    Returns up to top_n * 2 bets: per game, pick a:
    - Safe bet: highest model_win_prob (most likely to win)
    - Value bet: highest edge_pct that differs from safe bet (where books are most wrong)
    """
    # Group by game
    games: dict[str, List[BetOpportunity]] = {}
    for bet in all_opportunities:
        games.setdefault(bet.game, []).append(bet)

    # Score each game by its best edge
    game_scores = []
    for game_label, bets in games.items():
        best_edge = max(b.edge_pct for b in bets)
        game_scores.append((best_edge, game_label))

    game_scores.sort(reverse=True)

    results = []
    for _, game_label in game_scores[:top_n]:
        bets = games[game_label]

        # Safe bet: highest model_win_prob
        safe = max(bets, key=lambda b: b.model_win_prob)
        safe.category = "safe"
        results.append(safe)

        # Value bet: highest edge_pct, different from safe
        value_candidates = [b for b in bets if (b.bet_type, b.bet_side) != (safe.bet_type, safe.bet_side)]
        if value_candidates:
            value = max(value_candidates, key=lambda b: b.edge_pct)
            value.category = "value"
            results.append(value)

    return results
