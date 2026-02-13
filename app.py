"""
CBB Matchup Analyzer — Streamlit App

Single-page app: pick a date → see games → select one → deep analysis.
Sidebar handles navigation; main area reacts to selection.
"""

import datetime as dt
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy.stats import norm

from scripts.matchup_model import MatchupProjection, project_matchup
from scripts.ncaa_client import NcaaClient
from scripts.odds_client import GameOdds, OddsClient
from scripts.stats_builder import (
    build_team_profile,
    get_or_fetch_team_season,
)
from scripts.team_name_map import (
    build_team_identity_map,
    get_espn_id,
    resolve_ncaa_name,
)
from scripts.value_finder import (
    analyze_game_value_all_books,
    select_best_bets,
)

# ---------------------------------------------------------------------------
# Page config & custom CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="CBB Matchup Analyzer",
    page_icon="🏀",
    layout="wide",
)

st.markdown("""
<style>
    /* Tighter metric cards */
    [data-testid="stMetric"] {
        background-color: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.78rem;
        color: rgba(250, 250, 250, 0.55);
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.35rem;
    }

    /* Divider styling */
    hr {
        border-color: rgba(255, 255, 255, 0.08);
        margin: 1.5rem 0;
    }

    /* Sidebar game radio — tighter */
    .stRadio > div {
        gap: 2px;
    }

    /* Section headers */
    h2 {
        padding-top: 0.5rem !important;
        border-bottom: 2px solid rgba(255, 107, 53, 0.3);
        padding-bottom: 0.3rem;
    }

    /* DataFrames: smaller font */
    .stDataFrame {
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NcaaGameMeta:
    game_id: str
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


@dataclass
class ResolvedGame:
    game_id: str
    home_ncaa: str
    away_ncaa: str
    home: str  # canonical ESPN name
    away: str
    home_espn_id: str
    away_espn_id: str


# ---------------------------------------------------------------------------
# Cached resources / data
# ---------------------------------------------------------------------------

@st.cache_resource
def get_ncaa_client() -> NcaaClient:
    return NcaaClient()


@st.cache_resource
def get_odds_client() -> Optional[OddsClient]:
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        return None
    return OddsClient(api_key=key)


@st.cache_data(ttl=7 * 86400)
def get_identity_map() -> dict:
    return build_team_identity_map()


@st.cache_data(ttl=300)
def fetch_scoreboard(year: int, month: int, day: int) -> dict:
    client = get_ncaa_client()
    return client.get_mens_d1_scoreboard(year, month, day)


@st.cache_data(ttl=300)
def fetch_all_odds() -> list:
    client = get_odds_client()
    if client is None:
        return []
    try:
        return client.get_ncaab_odds(regions="us", markets="h2h,spreads,totals")
    except Exception:
        return []


@st.cache_data(ttl=12 * 3600)
def fetch_team_season(name: str, espn_id: str, season: int) -> Optional[pd.DataFrame]:
    try:
        df = get_or_fetch_team_season(name, espn_id, season)
        return df if not df.empty else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_scoreboard_games(scoreboard_json: dict) -> List[NcaaGameMeta]:
    out: List[NcaaGameMeta] = []
    for gwrap in scoreboard_json.get("games", []):
        game = gwrap.get("game", {})
        home = game.get("home", {})
        away = game.get("away", {})
        home_names = home.get("names", {})
        away_names = away.get("names", {})
        home_name = home_names.get("short") or home_names.get("full", "")
        away_name = away_names.get("short") or away_names.get("full", "")
        try:
            home_score = int(home.get("score", 0))
        except (ValueError, TypeError):
            home_score = 0
        try:
            away_score = int(away.get("score", 0))
        except (ValueError, TypeError):
            away_score = 0
        url = game.get("url", "")
        game_id = url.strip("/").split("/")[-1] if url else game.get("gameID", "")
        start_date_str = game.get("startDate")
        if start_date_str:
            normalized = start_date_str.replace("/", "-")
            try:
                date_obj = dt.datetime.strptime(normalized, "%m-%d-%Y").date()
                date_iso = date_obj.isoformat()
            except ValueError:
                date_iso = ""
        else:
            date_iso = ""
        out.append(NcaaGameMeta(
            game_id=str(game_id), date=date_iso,
            home_team=home_name, away_team=away_name,
            home_score=home_score, away_score=away_score,
        ))
    return out


def resolve_games(raw_games: List[NcaaGameMeta], identity_map: dict) -> List[ResolvedGame]:
    resolved = []
    for g in raw_games:
        home_canonical = resolve_ncaa_name(g.home_team, identity_map)
        away_canonical = resolve_ncaa_name(g.away_team, identity_map)
        if home_canonical and away_canonical:
            resolved.append(ResolvedGame(
                game_id=g.game_id,
                home_ncaa=g.home_team, away_ncaa=g.away_team,
                home=home_canonical, away=away_canonical,
                home_espn_id=get_espn_id(home_canonical, identity_map) or "",
                away_espn_id=get_espn_id(away_canonical, identity_map) or "",
            ))
    return resolved


def build_odds_lookup(all_odds: list) -> Dict[str, GameOdds]:
    return {f"{go.home_team}|{go.away_team}": go for go in all_odds}


def get_season(target_date: dt.date) -> int:
    return target_date.year if target_date.month >= 9 else target_date.year - 1


def fmt_odds(price: int) -> str:
    return f"{price:+d}" if price else "—"


def fmt_spread(point: Optional[float]) -> str:
    if point is None:
        return "—"
    return f"{point:+.1f}" if point != 0 else "PK"


def short_name(full_name: str) -> str:
    """'Purdue Boilermakers' -> 'Purdue'."""
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name
    # Most ESPN names are "Location Mascot" — return everything but last word
    # unless it's a two-word location like "George Washington"
    if len(parts) == 2:
        return parts[0]
    # Heuristic: if the second-to-last word is capitalized and short, it's likely mascot
    return " ".join(parts[:-1])


def confidence_badge(conf: str) -> str:
    colors = {"high": "#2ecc71", "medium": "#f39c12", "low": "#e74c3c"}
    color = colors.get(conf, "#888")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600">{conf.upper()}</span>'


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.markdown("## 🏀 CBB Analyzer")
st.sidebar.caption("Possession-based matchup projections & value finder")

# Date picker
try:
    import pytz
    eastern = pytz.timezone("US/Eastern")
    default_date = dt.datetime.now(eastern).date()
except Exception:
    default_date = dt.date.today()

selected_date = st.sidebar.date_input("Game Date", value=default_date)
season = get_season(selected_date)

identity_map = get_identity_map()

with st.sidebar:
    with st.spinner("Loading games..."):
        scoreboard = fetch_scoreboard(selected_date.year, selected_date.month, selected_date.day)
        raw_games = parse_scoreboard_games(scoreboard)
        games_today = resolve_games(raw_games, identity_map)

    if not games_today:
        st.warning("No resolved games for this date.")
        st.stop()

    # Fetch odds
    all_odds = fetch_all_odds()
    odds_lookup = build_odds_lookup(all_odds)

    # Game count + odds status
    odds_count = sum(1 for g in games_today if f"{g.home}|{g.away}" in odds_lookup)
    st.caption(f"{len(games_today)} games | {odds_count} with odds")

    odds_client = get_odds_client()
    if odds_client and odds_client.remaining_credits is not None:
        st.caption(f"API credits: {odds_client.remaining_credits} remaining")
    elif odds_client is None:
        st.caption("⚠ No ODDS_API_KEY set")

    st.markdown("---")

    # Game selection with short names
    game_labels = []
    for g in games_today:
        has_odds = "●" if f"{g.home}|{g.away}" in odds_lookup else "○"
        game_labels.append(f"{has_odds}  {short_name(g.away)} @ {short_name(g.home)}")

    selected_idx = st.radio(
        "Select a game",
        range(len(game_labels)),
        format_func=lambda i: game_labels[i],
    )

    st.markdown("---")
    st.caption("● = odds available  ○ = projection only")

game = games_today[selected_idx]

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

# ===== 1. Game Header =====
header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.title(f"{short_name(game.away)} @ {short_name(game.home)}")
    st.caption(
        f"{selected_date.strftime('%A, %B %d, %Y')} "
        f"| {season}-{str(season + 1)[-2:]} Season"
    )
with header_col2:
    # Quick matchup indicator (filled after profiles load)
    pass

# ===== Load team data =====
with st.spinner("Loading team data..."):
    home_df = fetch_team_season(game.home, game.home_espn_id, season)
    away_df = fetch_team_season(game.away, game.away_espn_id, season)

if home_df is None or away_df is None:
    st.error("Could not load season data for one or both teams.")
    st.stop()

try:
    home_raw = build_team_profile(home_df, game.home, game.home_espn_id)
    away_raw = build_team_profile(away_df, game.away, game.away_espn_id)
    raw_profiles = {game.home: home_raw, game.away: away_raw}
    home_profile = build_team_profile(
        home_df, game.home, game.home_espn_id, opponent_profiles=raw_profiles
    )
    away_profile = build_team_profile(
        away_df, game.away, game.away_espn_id, opponent_profiles=raw_profiles
    )
except Exception as e:
    st.error(f"Error building team profiles: {e}")
    st.stop()

projection = project_matchup(home_profile, away_profile)
proj = projection

odds_key = f"{game.home}|{game.away}"
game_odds = odds_lookup.get(odds_key)

# Quick matchup badge in header
with header_col2:
    fav = short_name(game.home) if proj.proj_spread < 0 else short_name(game.away)
    margin = abs(proj.proj_spread)
    st.metric("Model Favorite", f"{fav} by {margin:.1f}")

# ===== 2. Model Projection (moved up for action-first layout) =====
st.header("Model Projection")

# Model vs Book comparison row
if game_odds:
    # Get book lines for comparison
    book_spread = None
    book_total = None
    spread_mkt = game_odds.get_consensus_line("spreads")
    total_mkt = game_odds.get_consensus_line("totals")
    if spread_mkt:
        for oc in spread_mkt.outcomes:
            if oc.name == game_odds.home_team and oc.point is not None:
                book_spread = oc.point
    if total_mkt:
        for oc in total_mkt.outcomes:
            if oc.name == "Over" and oc.point is not None:
                book_total = oc.point

    cmp1, cmp2, cmp3, cmp4 = st.columns(4)
    cmp1.metric(
        "Model Spread",
        f"{proj.proj_spread:+.1f}",
        delta=f"{proj.proj_spread - book_spread:+.1f} vs book" if book_spread is not None else None,
        delta_color="off",
        help=f"90% CI: [{proj.spread_ci_lo:+.1f}, {proj.spread_ci_hi:+.1f}]"
        + (f"\nBook: {fmt_spread(book_spread)}" if book_spread is not None else ""),
    )
    cmp2.metric(
        "Model Total",
        f"{proj.proj_total:.1f}",
        delta=f"{proj.proj_total - book_total:+.1f} vs book" if book_total is not None else None,
        delta_color="off",
        help=f"90% CI: [{proj.total_ci_lo:.1f}, {proj.total_ci_hi:.1f}]"
        + (f"\nBook: {book_total}" if book_total is not None else ""),
    )
    cmp3.metric(
        f"{short_name(game.home)} Pts",
        f"{proj.home_pts:.1f}",
        help=f"90% CI: [{proj.home_pts_ci_lo:.1f}, {proj.home_pts_ci_hi:.1f}]",
    )
    cmp4.metric(
        f"{short_name(game.away)} Pts",
        f"{proj.away_pts:.1f}",
        help=f"90% CI: [{proj.away_pts_ci_lo:.1f}, {proj.away_pts_ci_hi:.1f}]",
    )
else:
    cmp1, cmp2, cmp3, cmp4 = st.columns(4)
    cmp1.metric(
        "Model Spread", f"{proj.proj_spread:+.1f}",
        help=f"90% CI: [{proj.spread_ci_lo:+.1f}, {proj.spread_ci_hi:+.1f}]",
    )
    cmp2.metric(
        "Model Total", f"{proj.proj_total:.1f}",
        help=f"90% CI: [{proj.total_ci_lo:.1f}, {proj.total_ci_hi:.1f}]",
    )
    cmp3.metric(
        f"{short_name(game.home)} Pts", f"{proj.home_pts:.1f}",
        help=f"90% CI: [{proj.home_pts_ci_lo:.1f}, {proj.home_pts_ci_hi:.1f}]",
    )
    cmp4.metric(
        f"{short_name(game.away)} Pts", f"{proj.away_pts:.1f}",
        help=f"90% CI: [{proj.away_pts_ci_lo:.1f}, {proj.away_pts_ci_hi:.1f}]",
    )

# Win probability — dual-color bar
wp_col1, wp_col2 = st.columns([4, 1])
with wp_col1:
    home_pct = proj.home_win_prob
    away_pct = 1 - home_pct
    fig_wp = go.Figure()
    fig_wp.add_trace(go.Bar(
        y=["Win Prob"], x=[home_pct], orientation="h",
        name=short_name(game.home), marker_color="#FF6B35",
        text=f"{home_pct:.0%}", textposition="inside",
        textfont=dict(color="white", size=14),
    ))
    fig_wp.add_trace(go.Bar(
        y=["Win Prob"], x=[away_pct], orientation="h",
        name=short_name(game.away), marker_color="#4ECDC4",
        text=f"{away_pct:.0%}", textposition="inside",
        textfont=dict(color="white", size=14),
    ))
    fig_wp.update_layout(
        barmode="stack", template="plotly_dark",
        height=70, margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showticklabels=False, showgrid=False, range=[0, 1]),
        yaxis=dict(showticklabels=False, showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.1, xanchor="center", x=0.5),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_wp, use_container_width=True, config={"displayModeBar": False})
with wp_col2:
    st.caption(f"{short_name(game.home)}: **{home_pct:.1%}**")
    st.caption(f"{short_name(game.away)}: **{away_pct:.1%}**")

# Score distribution bell curves
st.subheader("Score Distributions")

x_min = min(proj.home_pts, proj.away_pts) - 3 * max(proj.home_pts_std, proj.away_pts_std)
x_max = max(proj.home_pts, proj.away_pts) + 3 * max(proj.home_pts_std, proj.away_pts_std)
xs = np.linspace(x_min, x_max, 300)

home_pdf = norm.pdf(xs, proj.home_pts, proj.home_pts_std)
away_pdf = norm.pdf(xs, proj.away_pts, proj.away_pts_std)

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=xs, y=home_pdf, mode="lines", name=short_name(game.home),
    fill="tozeroy", line=dict(color="#FF6B35", width=2),
    fillcolor="rgba(255, 107, 53, 0.15)",
))
fig.add_trace(go.Scatter(
    x=xs, y=away_pdf, mode="lines", name=short_name(game.away),
    fill="tozeroy", line=dict(color="#4ECDC4", width=2),
    fillcolor="rgba(78, 205, 196, 0.15)",
))
fig.add_vline(x=proj.home_pts, line_dash="dash", line_color="#FF6B35",
              annotation_text=f"{proj.home_pts:.0f}", annotation_position="top")
fig.add_vline(x=proj.away_pts, line_dash="dash", line_color="#4ECDC4",
              annotation_text=f"{proj.away_pts:.0f}", annotation_position="top")
fig.update_layout(
    xaxis_title="Projected Points",
    yaxis_title="",
    yaxis_showticklabels=False,
    template="plotly_dark",
    height=320,
    margin=dict(t=40, b=40),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig, use_container_width=True)

# ===== 3. Sportsbook Odds Table =====
st.header("Sportsbook Odds")

if game_odds and game_odds.bookmakers:
    odds_rows = []
    for bk in game_odds.bookmakers:
        row: dict = {"Book": bk.title}
        for mkt in bk.markets:
            if mkt.key == "spreads":
                for oc in mkt.outcomes:
                    if oc.name == game_odds.home_team:
                        row["Spread"] = fmt_spread(oc.point)
                        row["Sprd Odds"] = fmt_odds(oc.price)
            elif mkt.key == "totals":
                for oc in mkt.outcomes:
                    if oc.name == "Over":
                        row["Total"] = f"{oc.point}" if oc.point else "—"
                        row["O"] = fmt_odds(oc.price)
                    elif oc.name == "Under":
                        row["U"] = fmt_odds(oc.price)
            elif mkt.key == "h2h":
                for oc in mkt.outcomes:
                    if oc.name == game_odds.home_team:
                        row["Home ML"] = fmt_odds(oc.price)
                    elif oc.name == game_odds.away_team:
                        row["Away ML"] = fmt_odds(oc.price)
        odds_rows.append(row)

    odds_df = pd.DataFrame(odds_rows)
    col_order = ["Book", "Spread", "Sprd Odds", "Total", "O", "U", "Home ML", "Away ML"]
    odds_df = odds_df.reindex(columns=[c for c in col_order if c in odds_df.columns])
    st.dataframe(odds_df, use_container_width=True, hide_index=True)
else:
    st.info("No odds available for this game.")

# ===== 4. Team Profiles =====
st.header("Team Profiles")

col_home, col_divider, col_away = st.columns([10, 1, 10])

with col_home:
    st.subheader(f"🏠 {short_name(game.home)}")
    hp = home_profile

    st.markdown("**Efficiency**")
    c1, c2, c3 = st.columns(3)
    c1.metric("Off PPP", f"{hp.off_ppp:.3f}")
    c2.metric("Def PPP", f"{hp.def_ppp:.3f}")
    c3.metric("Margin", f"{hp.eff_margin:+.3f}")

    st.markdown("**Pace & Shooting**")
    c4, c5, c6 = st.columns(3)
    c4.metric("Tempo", f"{hp.avg_possessions:.1f}")
    c5.metric("3P%", f"{hp.three_pct:.1%}")
    c6.metric("3P Rate", f"{hp.three_rate:.1%}")

    st.markdown("**Scoring**")
    c7, c8, c9 = st.columns(3)
    c7.metric("Avg Pts", f"{hp.avg_pts_for:.1f}")
    c8.metric("Opp Pts", f"{hp.avg_pts_against:.1f}")
    c9.metric("Games", f"{hp.games_played}")

    st.markdown("**Context**")
    c10, c11, c12 = st.columns(3)
    c10.metric("SOS Margin", f"{hp.sos_eff_margin:+.3f}",
               help="Avg opponent efficiency margin. Positive = played strong teams.")
    c11.metric("Recent Off PPP", f"{hp.recent_off_ppp:.3f}",
               help="Last 5 games offensive efficiency")
    c12.metric("ORB%", f"{hp.orb_pct:.1%}")

with col_divider:
    st.markdown("<div style='border-left:1px solid rgba(255,255,255,0.1);height:100%;min-height:300px'></div>",
                unsafe_allow_html=True)

with col_away:
    st.subheader(f"✈️ {short_name(game.away)}")
    ap = away_profile

    st.markdown("**Efficiency**")
    c1, c2, c3 = st.columns(3)
    c1.metric("Off PPP", f"{ap.off_ppp:.3f}")
    c2.metric("Def PPP", f"{ap.def_ppp:.3f}")
    c3.metric("Margin", f"{ap.eff_margin:+.3f}")

    st.markdown("**Pace & Shooting**")
    c4, c5, c6 = st.columns(3)
    c4.metric("Tempo", f"{ap.avg_possessions:.1f}")
    c5.metric("3P%", f"{ap.three_pct:.1%}")
    c6.metric("3P Rate", f"{ap.three_rate:.1%}")

    st.markdown("**Scoring**")
    c7, c8, c9 = st.columns(3)
    c7.metric("Avg Pts", f"{ap.avg_pts_for:.1f}")
    c8.metric("Opp Pts", f"{ap.avg_pts_against:.1f}")
    c9.metric("Games", f"{ap.games_played}")

    st.markdown("**Context**")
    c10, c11, c12 = st.columns(3)
    c10.metric("SOS Margin", f"{ap.sos_eff_margin:+.3f}",
               help="Avg opponent efficiency margin. Positive = played strong teams.")
    c11.metric("Recent Off PPP", f"{ap.recent_off_ppp:.3f}",
               help="Last 5 games offensive efficiency")
    c12.metric("ORB%", f"{ap.orb_pct:.1%}")

# ===== 5. Recent Games =====
st.header("Recent Games")

col_h_recent, col_a_recent = st.columns(2)

with col_h_recent:
    st.subheader(short_name(game.home))
    home_team_df = home_df[home_df["team"] == game.home].copy()
    home_recent = home_team_df.sort_values("date").tail(10)[
        ["date", "opponent", "pts_for", "pts_against", "off_ppp", "def_ppp"]
    ].copy()
    home_recent.columns = ["Date", "Opponent", "Pts", "Opp", "Off PPP", "Def PPP"]
    home_recent["Off PPP"] = home_recent["Off PPP"].round(3)
    home_recent["Def PPP"] = home_recent["Def PPP"].round(3)
    home_recent["Result"] = home_recent.apply(
        lambda r: "W" if r["Pts"] > r["Opp"] else "L", axis=1
    )
    home_recent = home_recent[["Date", "Opponent", "Result", "Pts", "Opp", "Off PPP", "Def PPP"]]
    st.dataframe(home_recent, use_container_width=True, hide_index=True)

with col_a_recent:
    st.subheader(short_name(game.away))
    away_team_df = away_df[away_df["team"] == game.away].copy()
    away_recent = away_team_df.sort_values("date").tail(10)[
        ["date", "opponent", "pts_for", "pts_against", "off_ppp", "def_ppp"]
    ].copy()
    away_recent.columns = ["Date", "Opponent", "Pts", "Opp", "Off PPP", "Def PPP"]
    away_recent["Off PPP"] = away_recent["Off PPP"].round(3)
    away_recent["Def PPP"] = away_recent["Def PPP"].round(3)
    away_recent["Result"] = away_recent.apply(
        lambda r: "W" if r["Pts"] > r["Opp"] else "L", axis=1
    )
    away_recent = away_recent[["Date", "Opponent", "Result", "Pts", "Opp", "Off PPP", "Def PPP"]]
    st.dataframe(away_recent, use_container_width=True, hide_index=True)

# ===== 6. Bet Analysis =====
st.header("Bet Analysis")

if game_odds:
    bets = analyze_game_value_all_books(projection, game_odds)

    if bets:
        # Filter to positive-edge bets, plus show top negatives
        positive_bets = [b for b in bets if b.edge_pct > 0]
        display_bets = positive_bets[:12] if positive_bets else bets[:6]

        if positive_bets:
            st.success(f"Found {len(positive_bets)} positive-edge opportunities across all books")
        else:
            st.warning("No positive-edge bets found. Showing top opportunities by safety score.")

        for i, bet in enumerate(display_bets):
            # Header with key info
            pref_icon = "⭐ " if bet.in_preferred_range else ""
            edge_color = "🟢" if bet.edge_pct >= 0.05 else ("🟡" if bet.edge_pct > 0 else "🔴")

            with st.expander(
                f"{pref_icon}{edge_color} "
                f"**{bet.bet_type.upper()} {bet.bet_side.upper()}** — "
                f"{bet.bookmaker} | "
                f"Edge {bet.edge_pct:+.1%} | "
                f"Safety {bet.safety_score:.2f}",
                expanded=(i < 3 and bet.edge_pct > 0),
            ):
                bc1, bc2, bc3, bc4, bc5 = st.columns(5)
                bc1.metric("Line", f"{bet.book_line}" if bet.book_line else "ML")
                bc2.metric("Odds", fmt_odds(bet.book_odds))
                bc3.metric("Win Prob", f"{bet.model_win_prob:.1%}")
                bc4.metric("Edge", f"{bet.edge_pct:+.1%}")
                bc5.metric("Kelly", f"{bet.kelly_fraction:.2%}")

                # Confidence badge + reasoning
                st.markdown(
                    f"Confidence: {confidence_badge(bet.confidence)} &nbsp; "
                    f"Safety: **{bet.safety_score:.3f}** &nbsp; "
                    f"Book implied: {bet.implied_prob:.1%}"
                    + (" &nbsp; | &nbsp; **PREFERRED RANGE (-400 to -250)**" if bet.in_preferred_range else ""),
                    unsafe_allow_html=True,
                )
                st.caption(bet.reasoning)
    else:
        st.info("No bet opportunities found for this game.")
else:
    st.info("No odds available — set `ODDS_API_KEY` environment variable.")

# ===== 7. Today's Top Bets (across ALL games) =====
st.header("Today's Top Bets")
st.caption("Best opportunities across all games, deduplicated to the best book per bet")

all_opportunities = []
for g in games_today:
    ok = f"{g.home}|{g.away}"
    go_match = odds_lookup.get(ok)
    if not go_match:
        continue

    h_df = fetch_team_season(g.home, g.home_espn_id, season)
    a_df = fetch_team_season(g.away, g.away_espn_id, season)
    if h_df is None or a_df is None:
        continue

    try:
        h_raw = build_team_profile(h_df, g.home, g.home_espn_id)
        a_raw = build_team_profile(a_df, g.away, g.away_espn_id)
        rp = {g.home: h_raw, g.away: a_raw}
        h_prof = build_team_profile(h_df, g.home, g.home_espn_id, opponent_profiles=rp)
        a_prof = build_team_profile(a_df, g.away, g.away_espn_id, opponent_profiles=rp)
    except Exception:
        continue

    p = project_matchup(h_prof, a_prof)
    game_bets = analyze_game_value_all_books(p, go_match)
    all_opportunities.extend(game_bets)

if all_opportunities:
    best = select_best_bets(all_opportunities, top_n=8)

    top_rows = []
    for b in best:
        # Shorten game label
        parts = b.game.split(" vs ")
        short_game = f"{short_name(parts[0])} vs {short_name(parts[1])}" if len(parts) == 2 else b.game

        top_rows.append({
            "Game": short_game,
            "Bet": f"{b.bet_type.upper()} {b.bet_side.upper()}",
            "Book": b.bookmaker,
            "Line": f"{b.book_line}" if b.book_line else "ML",
            "Odds": fmt_odds(b.book_odds),
            "Win %": f"{b.model_win_prob:.0%}",
            "Edge": f"{b.edge_pct:+.1%}",
            "Safety": f"{b.safety_score:.2f}",
            "Kelly": f"{b.kelly_fraction:.1%}",
            "Cat.": b.category.upper() if b.category else "",
            "Pref": "⭐" if b.in_preferred_range else "",
        })

    top_df = pd.DataFrame(top_rows)
    st.dataframe(top_df, use_container_width=True, hide_index=True, height=400)
else:
    st.info("No bet opportunities across today's games.")

# Footer
st.markdown("---")
st.caption(
    "Model: possession-based efficiency projections with SOS adjustment, "
    "recency weighting (30-day half-life), logistic win probability, "
    "and safety-ranked bet recommendations. "
    "See README for full methodology."
)
