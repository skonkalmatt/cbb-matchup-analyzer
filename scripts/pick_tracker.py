"""
Pick tracking database for model performance analysis.
"""
import sqlite3
from datetime import datetime
from typing import Optional
from dataclasses import dataclass


@dataclass
class Pick:
    game_id: str
    date: str
    game: str  # "Team A vs Team B"
    bet_type: str  # spread, total, moneyline
    bet_side: str  # home, away, over, under
    bookmaker: str
    line: Optional[float]
    odds: int
    model_edge: float
    model_win_prob: float
    kelly_fraction: float
    safety_score: float
    stake: float = 0.0  # units
    result: Optional[str] = None  # win, loss, push, pending
    profit: Optional[float] = None


class PickTracker:
    def __init__(self, db_path: str = "picks_history/picks.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                date TEXT,
                game TEXT,
                bet_type TEXT,
                bet_side TEXT,
                bookmaker TEXT,
                line REAL,
                odds INTEGER,
                model_edge REAL,
                model_win_prob REAL,
                kelly_fraction REAL,
                safety_score REAL,
                stake REAL,
                result TEXT,
                profit REAL,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def log_pick(self, pick: Pick):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO picks 
            (game_id, date, game, bet_type, bet_side, bookmaker, line, odds,
             model_edge, model_win_prob, kelly_fraction, safety_score, stake,
             result, profit, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pick.game_id, pick.date, pick.game, pick.bet_type, pick.bet_side,
            pick.bookmaker, pick.line, pick.odds, pick.model_edge,
            pick.model_win_prob, pick.kelly_fraction, pick.safety_score,
            pick.stake, pick.result, pick.profit, datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
    
    def update_result(self, game_id: str, bet_type: str, bet_side: str, result: str, profit: float):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            UPDATE picks 
            SET result = ?, profit = ?
            WHERE game_id = ? AND bet_type = ? AND bet_side = ?
        """, (result, profit, game_id, bet_type, bet_side))
        conn.commit()
        conn.close()
    
    def get_performance_summary(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Overall stats
        c.execute("""
            SELECT 
                COUNT(*) as total_picks,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END) as pushes,
                SUM(profit) as total_profit,
                AVG(model_edge) as avg_edge,
                AVG(safety_score) as avg_safety
            FROM picks
            WHERE result IS NOT NULL
        """)
        
        row = c.fetchone()
        conn.close()
        
        if row and row[0] > 0:
            return {
                'total_picks': row[0],
                'wins': row[1],
                'losses': row[2],
                'pushes': row[3],
                'win_rate': row[1] / row[0] if row[0] > 0 else 0,
                'total_profit': row[4] or 0,
                'roi': (row[4] / row[0]) if row[0] > 0 else 0,
                'avg_edge': row[5] or 0,
                'avg_safety': row[6] or 0,
            }
        return None


if __name__ == "__main__":
    tracker = PickTracker()
    print("Pick tracker initialized")
    summary = tracker.get_performance_summary()
    if summary:
        print(f"Picks: {summary['total_picks']}")
        print(f"Win Rate: {summary['win_rate']:.1%}")
        print(f"ROI: {summary['roi']:.2f} units")
    else:
        print("No completed picks yet")
