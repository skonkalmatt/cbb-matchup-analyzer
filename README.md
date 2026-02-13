# CBB Matchup Analyzer

A possession-based college basketball matchup projection system with a Streamlit web app. It pulls live game schedules, fetches real-time sportsbook odds from every major book, builds SOS-adjusted efficiency profiles, projects scores with confidence intervals, and surfaces safety-ranked betting opportunities.

## Quick Start

```bash
source .venv/bin/activate
pip install -r requirements.txt
export ODDS_API_KEY="your_key_here"   # get one free at https://the-odds-api.com
streamlit run app.py
```

Pick a date in the sidebar, select a game, and the full analysis renders instantly.

---

## How the Model Works

The model follows a four-stage pipeline: **Profile** teams, **Project** the matchup, **Compare** to the market, and **Rank** opportunities.

### Stage 1: Team Profiling (`stats_builder.py`)

Every team gets a `TeamSeasonProfile` built from ESPN box score data (cached as CSV, refreshed every 12 hours).

**Core stats computed per team:**
- **Possessions** — estimated via `FGA - ORB + TOV + 0.475 * FTA` (the standard KenPom formula)
- **Offensive PPP** — points scored per possession
- **Defensive PPP** — points allowed per possession
- **Efficiency margin** — Off PPP minus Def PPP (the single best predictor of team quality)
- **Tempo** — average possessions per game
- **Shooting** — three-point rate (3PA/FGA), three-point percentage, offensive rebound rate
- **Free throws** — average FTA, FTA rate, opponent FTA allowed

**Recency weighting:** All means (PPP, tempo, scoring) use exponential decay weights with a 30-day half-life. A game from yesterday gets weight ~1.0; a game from 30 days ago gets ~0.5; a game from 60 days ago gets ~0.25. This captures teams that are trending up or down without discarding early-season data entirely. Standard deviations stay unweighted so the model doesn't underestimate variance.

**Strength of schedule (SOS):** A second pass computes SOS by averaging opponents' offensive and defensive PPP. This feeds into the projection engine to adjust for teams that look good because they played weak schedules (or look bad because they played killers).

**Recent form:** The last 5 games get their own PPP averages, used as a 15% blend component in projections.

### Stage 2: Matchup Projection (`matchup_model.py`)

Given two team profiles, the model projects every number you need for a game.

**Tempo projection:**
Harmonic mean of both teams' season tempos. The harmonic mean biases toward the slower team — which is correct, because the slow team controls pace more than the fast team can push it.

**PPP projection (the key formula):**
For each team's offense against the opponent's defense:

```
adj_off = raw_off_ppp + (league_avg - sos_def_ppp)
adj_def = raw_def_ppp + (league_avg - sos_off_ppp)
matchup_ppp = adj_off + adj_def - league_avg
blended_ppp = 0.85 * matchup_ppp + 0.15 * recent_off_ppp
if home: blended_ppp += 0.045
```

The SOS adjustment inflates offenses that faced tough defenses and deflates offenses that feasted on weak ones. The 85/15 blend trusts the full-season matchup model but gives a nod to hot/cold streaks. Home court adds +0.045 PPP (~3.2 points at average tempo), calibrated to empirical D1 data.

**Score projection:**
```
projected_pts = projected_tempo * projected_ppp
total = home_pts + away_pts
spread = away_pts - home_pts  (negative = home favored)
```

**Win probability:**
Uses a logistic function instead of a normal CDF:

```
P(home wins) = 1 / (1 + e^(0.175 * spread))
```

The constant k=0.175 is calibrated so an 11-point spread yields ~87% win probability, matching historical D1 results. The logistic function has fatter tails than a Gaussian — meaning the model assigns more realistic upset probabilities. A 20-point favorite is ~97%, not 99.5%.

**Uncertainty estimation:**
Each team's historical scoring variance provides per-team standard deviations, with a game-count penalty: `std * sqrt(30 / games_played)`. Teams with only 10 games get ~1.7x wider confidence intervals than teams with 30+ games. This prevents the model from being overconfident about small-sample teams.

**90% confidence intervals:**
Every projection gets symmetric CIs using `value +/- 1.645 * std`. These appear as tooltips on the projected total, spread, and per-team point projections.

### Stage 3: Market Comparison (`value_finder.py`)

The model compares its projections against every sportsbook's lines across three markets:

**Spread analysis:**
Converts the book spread to a margin the team must cover, then computes `P(team covers)` via normal CDF using the model's projected margin and spread_std. Compares to the implied probability from the American odds.

**Total analysis:**
Computes `P(actual_total > book_total)` for overs and `P(actual_total < book_total)` for unders, again via normal CDF against total_std.

**Moneyline analysis:**
Computes `P(team wins outright)` from the model's projected margin, compared to the book's implied probability.

For every bet, the model calculates:
- **Edge %** — model win probability minus book implied probability (positive = value)
- **Kelly fraction** — optimal bet sizing via Kelly criterion, capped at 5% of bankroll
- **Confidence** — "HIGH" (edge >= 10%), "MEDIUM" (>= 5%), "LOW" (< 5%)

### Stage 4: Safety Ranking

Not all edges are created equal. A +15% edge on a coin-flip game is scarier than a +5% edge on a 75% favorite. The safety score captures this:

```
safety_score = 0.60 * win_probability
             + 0.25 * edge_pct
             + 0.15 * preferred_range_bonus
```

**Why this weighting:**
- **60% win probability** — the most important factor. High-probability bets lose less often, meaning your bankroll survives variance. A bet with 80% model win prob and 3% edge is more bankroll-friendly than one with 55% win prob and 10% edge.
- **25% edge** — the mathematical expectation. This is what makes you money long-term. But edge alone is noisy; small modeling errors get amplified at low win probabilities.
- **15% preferred range bonus** — a 1.0 bonus for bets in the -400 to -250 odds range. This range hits a sweet spot: the favorite is strong enough that your win rate stays high (~71-80% implied), but the payout isn't so compressed that vig eats your edge. At -150, you're taking too much variance. At -600, even a real edge barely covers the juice.

**Deduplication:** When the same bet (game + type + side) appears at multiple books, only the one with the highest safety score survives. This means the "Today's Top Bets" table always shows the best available price across all books for each unique opportunity.

---

## How to Read the App as a Bettor

The page is designed to be read top-to-bottom as a funnel from context to action.

### 1. Start with the Sidebar

Pick your date. The game list loads — these are all D1 games with resolved names. Games without odds data will still show projections but won't have bet analysis. Click through games; switching is instant (no API re-fetch).

### 2. Sportsbook Odds Table

**What to look for:** Line discrepancies between books. If DraftKings has a spread at -5.5 and BetMGM has -4.5, that 1-point gap is where value lives. The model analyzes all books independently, so it will surface the best number automatically, but seeing the raw lines helps you confirm which book to place at.

### 3. Team Profiles

**What to look for:** Efficiency margin is the single best number. A team with +0.10 margin (scoring 0.10 more points per possession than they allow) is genuinely good. Look at the *delta* between the two teams' margins — that's roughly the expected per-possession advantage.

Also check tempo. A fast team (72+ possessions) playing a slow team (64 possessions) will land closer to the slow team's pace. That depresses totals and compresses scoring variance. High-tempo vs. high-tempo games have wider distributions and more total value.

SOS matters most for mid-major matchups. A team with great raw numbers but 0.95 SOS defensive PPP played cupcakes — the model adjusts for this, but it's worth eyeballing.

### 4. Recent Games

**What to look for:** Injuries and cold streaks don't show up in stats — but you can spot them. If a team's last 3 games show Off PPP dropping from 1.10 to 0.85, something changed (key player injury, tough road stretch, lineup change). The model's recency weighting captures some of this, but a sharp bettor cross-references with injury reports.

Also look for opponent quality. A team that went 1.20 PPP against a 0.95 defensive PPP team is showing real offensive pop. A team that went 1.20 PPP against a 1.15 defensive PPP team was just playing bad defense.

### 5. Model Projection

**The four numbers that matter:**
- **Projected Total** vs the book total — if the model says 142 and the book says 152, that's a 10-point gap, strong under signal
- **Projected Spread** vs the book spread — tells you which side the model favors and by how much
- **Per-team points** — useful for team total props if your book offers them
- **Win Probability** — the bar tells you how lopsided the model thinks it is

**The bell curves** show score distributions overlaid. When the curves barely overlap, the model is confident one team wins. When they're on top of each other, it's a coin flip — which means spread value matters more than moneyline value.

Hover over any projection number to see the **90% confidence interval**. Wide intervals (e.g., 58-82 points) mean the model is uncertain — that's not necessarily bad for betting, but it means you should size smaller.

### 6. Bet Analysis

**Per-game opportunities** are sorted by safety score. The top bets auto-expand.

**How to read each bet card:**
- **Safety score** > 0.55 — worth a look
- **Safety score** > 0.65 — strong play
- **Edge %** > +5% — model sees real value
- **Kelly %** — suggested bet size as % of bankroll (already capped at 5%). If Kelly says 2.5%, that's a meaningful edge. If it says 0.3%, the edge is thin.
- **Win Prob** — your expected hit rate over many bets at this profile

**Preferred range bets** (tagged with a star) are in the -400 to -250 odds sweet spot. These are the model's "bread and butter" — high win rate, reasonable payout, manageable variance. If you're building a conservative daily card, start with preferred-range bets that also have positive edge.

### 7. Today's Top Bets

**This is the action table.** It aggregates the best opportunities across every game on the slate, deduplicated to the best book for each unique bet.

**A bettor's workflow:**
1. Scan the table for SAFE category bets with safety > 0.55 and positive edge
2. Check if any are in the preferred range
3. Click into the individual game to verify the projection makes sense (team profiles, recent form)
4. Size using Kelly % as a ceiling — most bettors should bet half-Kelly or less to manage variance
5. Shop the line — the "Book" column tells you where to place, but always check for a better number

**Red flags to watch for:**
- **Low game count** (< 15 games played) — wider confidence intervals, less reliable projections
- **Large discrepancy with every book** — if the model disagrees with every sportsbook by 10+ points, the model is probably wrong, not the market. Books have sharp bettors and insider info the model doesn't.
- **Edge only on one book** — if only one book shows value and the rest don't, it might be a stale line about to move, or it might be the model overfitting to a slightly different number. Better if 3+ books all show edge.

---

## Architecture

```
NCAA API ──→ Scoreboard (today's games)
                 │
ESPN API  ──→ Box scores ──→ CSV cache ──→ Team Profiles
                                              │
                                              ▼
                                      Matchup Projection
                                              │
Odds API  ──→ All bookmaker lines ────────────┤
                                              ▼
                                        Value Finder
                                              │
                                              ▼
                                    Safety-Ranked Bets
```

**Data sources:**
- **NCAA API** (`ncaa-api.henrygd.me`) — daily scoreboard, game schedules
- **ESPN API** — team box scores, season schedules, team identity resolution
- **The Odds API** — real-time odds from DraftKings, FanDuel, BetMGM, and other US books (free tier: 500 credits/month, ~3 credits per daily fetch)

**Caching strategy:**
- Team identity map: 7-day cache (team names don't change)
- Season box scores: 12-hour CSV cache (games are final)
- Scoreboard: 5-minute cache (games may be added/removed)
- Odds: 5-minute cache (lines move frequently)
- Game switching: zero API calls (all data already loaded)

## Project Structure

```
app.py                          # Streamlit web app
scripts/
  stats_builder.py              # Team profiling (recency-weighted, SOS-adjusted)
  matchup_model.py              # Projection engine (tempo, PPP, CIs, win prob)
  value_finder.py               # Multi-book value detection + safety ranking
  odds_client.py                # The Odds API client
  ncaa_client.py                # NCAA scoreboard client
  team_name_map.py              # Cross-API name resolution (NCAA <-> ESPN <-> Odds)
  fetch_espn_cbb.py             # ESPN box score fetcher
data/
  team_identity_map.json        # ESPN team identities (auto-cached)
  team_name_overrides.json      # Manual name resolution fixes
  seasons/                      # Cached season CSV files
.streamlit/config.toml          # Dark theme config
```

## Key Constants

| Constant | Value | Rationale |
|---|---|---|
| `HOME_COURT_PPP` | 0.045 | ~3.2 pts at 68 poss, empirical D1 average |
| `LEAGUE_AVG_PPP` | 1.00 | D1 men's historical baseline |
| Logistic `k` | 0.175 | 11-pt spread = ~87% win prob |
| Recency half-life | 30 days | Balances trend-capture vs. sample size |
| PPP blend | 85/15 | Season matchup vs. recent form |
| Safety weights | 60/25/15 | Win prob / edge / preferred range |
| Preferred odds range | -400 to -250 | High hit rate, reasonable payout |
| Kelly cap | 5% | Prevents over-concentration |
| CI confidence | 90% | z = 1.645 |
| Game-count penalty | sqrt(30/n) | Widens uncertainty for small samples |
