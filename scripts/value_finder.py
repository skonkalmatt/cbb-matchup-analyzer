"""
Compare model projections to sportsbook odds and rank betting opportunities.

Uses normal CDF (scipy.stats.norm) to estimate P(bet wins).
Supports multi-bookmaker analysis and safety-ranked bet recommendations.
"""

from dataclasses import dataclass, field
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
    bookmaker: str = ""
    kelly_fraction: float = 0.0
    safety_score: float = 0.0
    in_preferred_range: bool = False


def american_to_implied_prob(odds: int) -> float:
    """
    Convert American odds to implied probability.
    -110 -> 52.4%, +150 -> 40.0%
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


def compute_kelly_fraction(
    model_win_prob: float,
    odds: int,
    max_fraction: float = 0.05,
) -> float:
    """Kelly criterion fraction, capped at max_fraction (5%).

    Kelly = (b*p - q) / b  where:
      b = decimal odds - 1 (net payout per unit)
      p = model win probability
      q = 1 - p
    """
    if odds < 0:
        b = 100.0 / abs(odds)
    else:
        b = odds / 100.0

    if b <= 0:
        return 0.0

    p = model_win_prob
    q = 1.0 - p
    kelly = (b * p - q) / b

    return max(0.0, min(kelly, max_fraction))


def _is_preferred_range(odds: int) -> bool:
    """Check if American odds fall in the preferred -400 to -250 range."""
    return -400 <= odds <= -250


def compute_safety_score(bet: BetOpportunity) -> float:
    """Composite safety score for ranking bets.

    Weights: 60% win_prob + 25% edge + 15% preferred_range bonus.
    """
    preferred_bonus = 1.0 if bet.in_preferred_range else 0.0
    return 0.60 * bet.model_win_prob + 0.25 * bet.edge_pct + 0.15 * preferred_bonus


def _analyze_spread_for_book(
    proj: MatchupProjection,
    game_odds: GameOdds,
    bookmaker_title: str,
    market: OddsMarket,
) -> List[BetOpportunity]:
    """Analyze spread bets for a specific bookmaker's market."""
    bets = []
    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in market.outcomes:
        if oc.point is None:
            continue

        if oc.name == game_odds.home_team:
            model_margin = proj.home_pts - proj.away_pts
            book_margin = -oc.point
            model_prob = compute_model_win_prob(model_margin, book_margin, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = model_margin - book_margin
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

        elif oc.name == game_odds.away_team:
            model_margin = proj.away_pts - proj.home_pts
            book_margin = -oc.point
            model_prob = compute_model_win_prob(model_margin, book_margin, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = model_margin - book_margin
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

    return bets


def _analyze_total_for_book(
    proj: MatchupProjection,
    game_odds: GameOdds,
    bookmaker_title: str,
    market: OddsMarket,
) -> List[BetOpportunity]:
    """Analyze over/under total bets for a specific bookmaker's market."""
    bets = []
    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in market.outcomes:
        if oc.point is None:
            continue

        if oc.name == "Over":
            model_prob = compute_model_win_prob(proj.proj_total, oc.point, proj.total_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = proj.proj_total - oc.point
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

        elif oc.name == "Under":
            model_prob = 1.0 - compute_model_win_prob(proj.proj_total, oc.point, proj.total_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            edge_pts = oc.point - proj.proj_total
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

    return bets


def _analyze_moneyline_for_book(
    proj: MatchupProjection,
    game_odds: GameOdds,
    bookmaker_title: str,
    market: OddsMarket,
) -> List[BetOpportunity]:
    """Analyze moneyline (h2h) bets for a specific bookmaker's market."""
    bets = []
    game_label = f"{proj.home_team} vs {proj.away_team}"

    for oc in market.outcomes:
        if oc.name == game_odds.home_team:
            model_margin = proj.home_pts - proj.away_pts
            model_prob = compute_model_win_prob(model_margin, 0, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

        elif oc.name == game_odds.away_team:
            model_margin = proj.away_pts - proj.home_pts
            model_prob = compute_model_win_prob(model_margin, 0, proj.spread_std)
            implied = american_to_implied_prob(oc.price)
            edge = model_prob - implied
            preferred = _is_preferred_range(oc.price)

            bet = BetOpportunity(
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
                bookmaker=bookmaker_title,
                kelly_fraction=compute_kelly_fraction(model_prob, oc.price),
                in_preferred_range=preferred,
            )
            bet.safety_score = compute_safety_score(bet)
            bets.append(bet)

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
    Analyze all available markets for a single game (consensus/first book only).
    Returns all bet opportunities sorted by safety_score descending.
    """
    bets = []
    # Use consensus (first book) for backwards compatibility
    for market_key, analyzer in [
        ("spreads", _analyze_spread_for_book),
        ("totals", _analyze_total_for_book),
        ("h2h", _analyze_moneyline_for_book),
    ]:
        mkt = game_odds.get_consensus_line(market_key)
        if mkt:
            # Find the bookmaker title for consensus
            title = ""
            for bk in game_odds.bookmakers:
                for m in bk.markets:
                    if m is mkt:
                        title = bk.title
                        break
                if title:
                    break
            bets.extend(analyzer(projection, game_odds, title, mkt))

    bets.sort(key=lambda b: b.safety_score, reverse=True)
    return bets


def analyze_game_value_all_books(
    projection: MatchupProjection,
    game_odds: GameOdds,
) -> List[BetOpportunity]:
    """
    Analyze every bookmaker's lines for a single game.
    Returns all bet opportunities across all books, sorted by safety_score.
    """
    bets = []

    for market_key, analyzer in [
        ("spreads", _analyze_spread_for_book),
        ("totals", _analyze_total_for_book),
        ("h2h", _analyze_moneyline_for_book),
    ]:
        book_lines = game_odds.get_all_book_lines(market_key)
        for bookmaker_title, market in book_lines:
            bets.extend(analyzer(projection, game_odds, bookmaker_title, market))

    bets.sort(key=lambda b: b.safety_score, reverse=True)
    return bets


def select_best_bets(
    all_opportunities: List[BetOpportunity],
    top_n: int = 5,
) -> List[BetOpportunity]:
    """
    Select the best bets across all games.

    Deduplicates per (game, bet_type, bet_side) keeping the highest safety_score.
    Then sorts by safety_score and returns up to top_n * 2.
    """
    # Deduplicate: keep best safety_score per (game, type, side)
    best_per_key: dict[tuple, BetOpportunity] = {}
    for bet in all_opportunities:
        key = (bet.game, bet.bet_type, bet.bet_side)
        if key not in best_per_key or bet.safety_score > best_per_key[key].safety_score:
            best_per_key[key] = bet

    deduped = list(best_per_key.values())

    # Group by game
    games: dict[str, List[BetOpportunity]] = {}
    for bet in deduped:
        games.setdefault(bet.game, []).append(bet)

    # Score each game by its best safety_score
    game_scores = []
    for game_label, bets in games.items():
        best_safety = max(b.safety_score for b in bets)
        game_scores.append((best_safety, game_label))

    game_scores.sort(reverse=True)

    results = []
    for _, game_label in game_scores[:top_n]:
        bets = games[game_label]

        # Safe bet: highest safety_score
        safe = max(bets, key=lambda b: b.safety_score)
        safe.category = "safe"
        results.append(safe)

        # Value bet: highest edge_pct, different from safe
        value_candidates = [b for b in bets if (b.bet_type, b.bet_side) != (safe.bet_type, safe.bet_side)]
        if value_candidates:
            value = max(value_candidates, key=lambda b: b.edge_pct)
            value.category = "value"
            results.append(value)

    # Final sort by safety_score
    results.sort(key=lambda b: b.safety_score, reverse=True)
    return results
