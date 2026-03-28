import sqlite3
from pathlib import Path
from loguru import logger

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "apex.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
        logger.info(f"DB migration: added {table}.{column}")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id              INTEGER PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                sector          TEXT NOT NULL,
                price           REAL,
                ma20            REAL,
                rsi             REAL,
                volume          INTEGER,
                avg_vol_30d     REAL,
                volume_ratio    REAL,
                signal_score    REAL,
                momentum_score  REAL,
                volume_score    REAL,
                ev              REAL,
                kelly_size      REAL,
                lock1_pass      INTEGER DEFAULT 0,
                lock2_pass      INTEGER DEFAULT 0,
                lock3_pass      INTEGER DEFAULT 0,
                gate_decision   TEXT,
                lock3_reasoning TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                id                    INTEGER PRIMARY KEY,
                timestamp             TEXT NOT NULL,
                ticker                TEXT NOT NULL,
                sector                TEXT NOT NULL,
                action                TEXT NOT NULL,
                price                 REAL,
                shares                REAL,
                amount                REAL,
                signal_score          REAL,
                sentiment_score       REAL,
                claude_confidence     REAL,
                claude_reasoning      TEXT,
                outcome               TEXT DEFAULT 'OPEN',
                pnl                   REAL,
                wallet_balance_after  REAL
            );

            CREATE TABLE IF NOT EXISTS news (
                id           INTEGER PRIMARY KEY,
                timestamp    TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                headline     TEXT,
                source       TEXT,
                published_at TEXT,
                url          TEXT
            );

            CREATE TABLE IF NOT EXISTS live_trades (
                id              INTEGER PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                sector          TEXT NOT NULL,
                alpaca_order_id TEXT NOT NULL,
                entry_price     REAL,
                qty             REAL,
                notional        REAL,
                tp_price        REAL,
                sl_price        REAL,
                outcome         TEXT DEFAULT 'OPEN',
                exit_price      REAL,
                pnl             REAL,
                exited_at       TEXT,
                exit_reason     TEXT
            );

            CREATE TABLE IF NOT EXISTS live_gate_history (
                id              INTEGER PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                sector          TEXT NOT NULL,
                signal_score    REAL,
                lock1_pass      INTEGER DEFAULT 0,
                lock2_pass      INTEGER DEFAULT 0,
                lock3_pass      INTEGER DEFAULT 0,
                gate_decision   TEXT,
                lock3_reasoning TEXT,
                alpaca_order_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_signals_ticker    ON signals(ticker);
            CREATE INDEX IF NOT EXISTS idx_signals_sector    ON signals(sector);
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_trades_ticker     ON trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_news_ticker       ON news(ticker);
            CREATE INDEX IF NOT EXISTS idx_live_gate_ts      ON live_gate_history(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_live_trades_ticker ON live_trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_live_trades_outcome ON live_trades(outcome);

            CREATE TABLE IF NOT EXISTS sector_snapshots (
                id           INTEGER PRIMARY KEY,
                timestamp    TEXT NOT NULL,
                sector       TEXT NOT NULL,
                avg_score    REAL NOT NULL,
                top_ticker   TEXT,
                top_score    REAL,
                ticker_count INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_sector_snap_ts     ON sector_snapshots(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_sector_snap_sector ON sector_snapshots(sector);
        """)
        conn.commit()
        logger.info(f"DB initialised at {DB_PATH}")

        # Migrations — safe to run every startup
        _add_column_if_missing(conn, "trades",  "price_exit",  "REAL")
        _add_column_if_missing(conn, "trades",  "exited_at",   "TEXT")
        _add_column_if_missing(conn, "trades",  "exit_reason", "TEXT")
        _add_column_if_missing(conn, "signals", "high_60d",         "REAL")
        _add_column_if_missing(conn, "signals", "low_60d",          "REAL")
        _add_column_if_missing(conn, "signals", "atr_pct",          "REAL")
        _add_column_if_missing(conn, "signals", "effective_sl",     "REAL")
        _add_column_if_missing(conn, "signals", "lock3_reasoning",  "TEXT")
        # Session 7 — new signals
        _add_column_if_missing(conn, "signals", "trend_score",      "REAL")
        _add_column_if_missing(conn, "signals", "macd_hist",        "REAL")
        _add_column_if_missing(conn, "signals", "ma50",             "REAL")
        _add_column_if_missing(conn, "signals", "rs_score",         "REAL")
    finally:
        conn.close()


def get_rolling_win_rate() -> float | None:
    """
    Returns win rate from the most recent ROLLING_WIN_WINDOW closed trades.
    Using a rolling window prevents stale historical performance from distorting
    the current P(win) estimate — important when system quality changes over time.
    Returns None until WIN_RATE_MIN_TRADES have closed.
    """
    from backend.config import WIN_RATE_MIN_TRADES
    ROLLING_WIN_WINDOW = 50
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                          AS total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) AS wins
            FROM (
                SELECT outcome
                FROM trades
                WHERE outcome IN ('WIN', 'LOSS')
                ORDER BY exited_at DESC
                LIMIT ?
            )
        """, (ROLLING_WIN_WINDOW,)).fetchone()
        total = row["total"] or 0
        wins  = row["wins"]  or 0
        if total < WIN_RATE_MIN_TRADES:
            return None
        return wins / total
    finally:
        conn.close()


def insert_signal(row: dict) -> int:
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO signals
              (timestamp, ticker, sector, price, ma20, rsi, volume, avg_vol_30d,
               volume_ratio, signal_score, momentum_score, volume_score, ev, kelly_size,
               high_60d, low_60d, atr_pct, effective_sl,
               trend_score, macd_hist, ma50, rs_score)
            VALUES
              (:timestamp, :ticker, :sector, :price, :ma20, :rsi, :volume, :avg_vol_30d,
               :volume_ratio, :signal_score, :momentum_score, :volume_score, :ev, :kelly_size,
               :high_60d, :low_60d, :atr_pct, :effective_sl,
               :trend_score, :macd_hist, :ma50, :rs_score)
        """, row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def latest_signals(limit: int = 100) -> list[dict]:
    """Most recent signal per ticker."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT s.*
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(timestamp) AS max_ts
                FROM signals
                GROUP BY ticker
            ) latest ON s.ticker = latest.ticker AND s.timestamp = latest.max_ts
            ORDER BY s.signal_score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prev_signals_avg_by_sector() -> dict[str, float]:
    """
    Returns the average signal score from the second-most-recent poll per sector.
    Used to compute trend arrows (rising/falling/flat).
    """
    conn = get_db()
    try:
        # Get the two most recent distinct timestamps per sector, then avg the older one
        rows = conn.execute("""
            SELECT sector, AVG(signal_score) AS avg_score
            FROM signals
            WHERE timestamp IN (
                SELECT DISTINCT timestamp FROM signals s2
                WHERE s2.sector = signals.sector
                ORDER BY timestamp DESC
                LIMIT 2 OFFSET 1
            )
            GROUP BY sector
        """).fetchall()
        return {r["sector"]: round(r["avg_score"], 4) for r in rows}
    finally:
        conn.close()


def signals_for_sector(sector: str) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE sector = ?
            ORDER BY timestamp DESC
            LIMIT 40
        """, (sector,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_lock1_candidates(threshold: float | None = None) -> list[dict]:
    """Latest signal per ticker where signal_score >= threshold (defaults to LOCK1_THRESHOLD)."""
    from backend.config import LOCK1_THRESHOLD
    effective = threshold if threshold is not None else LOCK1_THRESHOLD
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT s.*
            FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(timestamp) AS max_ts
                FROM signals
                GROUP BY ticker
            ) latest ON s.ticker = latest.ticker AND s.timestamp = latest.max_ts
            WHERE s.signal_score >= ?
            ORDER BY s.signal_score DESC
        """, (effective,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_signal_gate(signal_id: int, result: dict) -> None:
    """Write gate verdicts back to the signal row."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE signals
            SET lock1_pass       = :lock1_pass,
                lock2_pass       = :lock2_pass,
                lock3_pass       = :lock3_pass,
                gate_decision    = :gate_decision,
                lock3_reasoning  = :lock3_reasoning
            WHERE id = :id
        """, {
            "lock1_pass":       result["lock1_pass"],
            "lock2_pass":       result["lock2_pass"],
            "lock3_pass":       result["lock3_pass"],
            "gate_decision":    result["gate_decision"],
            "lock3_reasoning":  result.get("claude_reasoning"),
            "id":               signal_id,
        })
        conn.commit()
    finally:
        conn.close()


def get_wallet_context() -> dict:
    """Returns current wallet state for Lock 3 context."""
    from backend.config import STARTING_BALANCE
    conn = get_db()
    try:
        exposure_rows = conn.execute("""
            SELECT sector, SUM(amount) AS total
            FROM trades
            WHERE outcome = 'OPEN' AND action = 'BUY'
            GROUP BY sector
        """).fetchall()

        closed_pnl_row = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) AS total
            FROM trades WHERE outcome IN ('WIN', 'LOSS', 'EXPIRED')
        """).fetchone()

        open_row = conn.execute("""
            SELECT COUNT(DISTINCT ticker) AS cnt
            FROM trades
            WHERE outcome = 'OPEN' AND action = 'BUY'
        """).fetchone()

        invested     = sum(r["total"] for r in exposure_rows) if exposure_rows else 0
        realized_pnl = closed_pnl_row["total"] if closed_pnl_row else 0
        balance      = STARTING_BALANCE - invested + realized_pnl
        open_positions = open_row["cnt"] if open_row else 0

        sector_exposure = {
            r["sector"]: round(r["total"] / STARTING_BALANCE, 3)
            for r in exposure_rows
        } if invested > 0 else {}

        return {
            "balance":         round(balance, 2),
            "open_positions":  open_positions,
            "sector_exposure": sector_exposure,
        }
    finally:
        conn.close()


def insert_trade(row: dict) -> int:
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO trades
              (timestamp, ticker, sector, action, price, shares, amount,
               signal_score, sentiment_score, claude_confidence, claude_reasoning,
               outcome, pnl)
            VALUES
              (:timestamp, :ticker, :sector, :action, :price, :shares, :amount,
               :signal_score, :sentiment_score, :claude_confidence, :claude_reasoning,
               :outcome, :pnl)
        """, row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def close_trade(trade_id: int, price_exit: float, pnl: float,
                outcome: str, exit_reason: str, exited_at: str) -> None:
    conn = get_db()
    try:
        conn.execute("""
            UPDATE trades
            SET price_exit  = ?,
                pnl         = ?,
                outcome     = ?,
                exit_reason = ?,
                exited_at   = ?
            WHERE id = ?
        """, (price_exit, pnl, outcome, exit_reason, exited_at, trade_id))
        conn.commit()
    finally:
        conn.close()


def get_open_trades() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM trades WHERE outcome = 'OPEN' ORDER BY timestamp ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_open_tickers() -> set[str]:
    """Return set of ticker symbols that currently have an open demo position."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM trades WHERE outcome = 'OPEN'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_recently_failed_tickers(hours: float) -> set[str]:
    """Return tickers that failed L2, L3, or MACRO in the demo gate within the last N hours."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM signals
            WHERE gate_decision IN ('FILTERED_L2', 'FILTERED_L3', 'FILTERED_MACRO')
            AND timestamp > ?
        """, (cutoff,)).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_open_live_tickers() -> set[str]:
    """Return set of ticker symbols that currently have an open live position."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM live_trades WHERE outcome = 'OPEN' OR outcome IS NULL").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_recently_failed_live_tickers(hours: float) -> set[str]:
    """Return tickers that failed L2, L3, or MACRO in the live gate within the last N hours."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM live_gate_history
            WHERE gate_decision IN ('FILTERED_L2', 'FILTERED_L3', 'FILTERED_MACRO')
            AND timestamp > ?
        """, (cutoff,)).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_all_trades() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM trades ORDER BY timestamp DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_gate_history(limit: int = 30) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, sector, timestamp, signal_score,
                   gate_decision, lock1_pass, lock2_pass, lock3_pass,
                   lock3_reasoning
            FROM signals
            WHERE gate_decision IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_equity_curve() -> list[dict]:
    """Running balance over time from closed trades. Starts at STARTING_BALANCE."""
    from backend.config import STARTING_BALANCE
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT exited_at AS ts, pnl
            FROM trades
            WHERE outcome IN ('WIN','LOSS','EXPIRED') AND exited_at IS NOT NULL
            ORDER BY exited_at ASC
        """).fetchall()
        points = [{"ts": "Start", "balance": STARTING_BALANCE}]
        balance = STARTING_BALANCE
        for r in rows:
            balance += r["pnl"] or 0
            points.append({"ts": r["ts"][:10], "balance": round(balance, 2)})
        return points
    finally:
        conn.close()


def get_portfolio_summary() -> dict:
    """Lightweight summary for risk checks during trade execution."""
    from backend.config import STARTING_BALANCE
    conn = get_db()
    try:
        open_rows = conn.execute("""
            SELECT sector, SUM(amount) AS total
            FROM trades WHERE outcome = 'OPEN'
            GROUP BY sector
        """).fetchall()

        closed_pnl_row = conn.execute("""
            SELECT COALESCE(SUM(pnl), 0) AS total
            FROM trades WHERE outcome IN ('WIN','LOSS','EXPIRED')
        """).fetchone()

        invested     = sum(r["total"] for r in open_rows)
        realized_pnl = closed_pnl_row["total"]
        cash         = STARTING_BALANCE - invested + realized_pnl

        sector_exposure = {
            r["sector"]: round(r["total"] / STARTING_BALANCE, 3)
            for r in open_rows
        }

        open_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM trades WHERE outcome = 'OPEN'"
        ).fetchone()["cnt"]

        return {
            "cash":                round(cash, 2),
            "open_position_count": open_count,
            "sector_exposure":     sector_exposure,
        }
    finally:
        conn.close()


def insert_live_gate_result(row: dict) -> int:
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO live_gate_history
              (timestamp, ticker, sector, signal_score,
               lock1_pass, lock2_pass, lock3_pass,
               gate_decision, lock3_reasoning, alpaca_order_id)
            VALUES
              (:timestamp, :ticker, :sector, :signal_score,
               :lock1_pass, :lock2_pass, :lock3_pass,
               :gate_decision, :lock3_reasoning, :alpaca_order_id)
        """, row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_live_gate_history(limit: int = 30) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM live_gate_history
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def insert_live_trade(row: dict) -> int:
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO live_trades
              (timestamp, ticker, sector, alpaca_order_id,
               entry_price, qty, notional, tp_price, sl_price)
            VALUES
              (:timestamp, :ticker, :sector, :alpaca_order_id,
               :entry_price, :qty, :notional, :tp_price, :sl_price)
        """, row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def close_live_trade(trade_id: int, exit_price: float, pnl: float,
                     outcome: str, exit_reason: str, exited_at: str) -> None:
    conn = get_db()
    try:
        conn.execute("""
            UPDATE live_trades
            SET exit_price = ?, pnl = ?, outcome = ?, exit_reason = ?, exited_at = ?
            WHERE id = ?
        """, (exit_price, pnl, outcome, exit_reason, exited_at, trade_id))
        conn.commit()
    finally:
        conn.close()


def get_open_live_trades() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM live_trades WHERE outcome = 'OPEN' ORDER BY timestamp ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_live_trades() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM live_trades ORDER BY timestamp DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def insert_sector_snapshots(snapshots: list[dict]) -> None:
    """
    Batch-insert sector summary rows after each poll cycle.
    Each snapshot: {timestamp, sector, avg_score, top_ticker, top_score, ticker_count}
    """
    if not snapshots:
        return
    conn = get_db()
    try:
        conn.executemany("""
            INSERT INTO sector_snapshots (timestamp, sector, avg_score, top_ticker, top_score, ticker_count)
            VALUES (:timestamp, :sector, :avg_score, :top_ticker, :top_score, :ticker_count)
        """, snapshots)
        conn.commit()
    finally:
        conn.close()


def get_sector_history(days: int = 30) -> list[dict]:
    """
    Returns sector avg_score time series.

    days=0  → all available history
    days≤7  → raw 15-min snapshots   (intraday detail visible)
    days>7  → daily averages          (keeps payload small at any scale)

    Frontend pivots the flat rows into per-sector time series.
    """
    from datetime import datetime, timezone, timedelta
    conn = get_db()
    try:
        if days == 0:
            cutoff_clause = ""
            params: tuple = ()
        else:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            cutoff_clause = "WHERE timestamp > ?"
            params = (cutoff,)

        if 1 <= days <= 7:
            # Raw resolution — one row per snapshot per sector
            rows = conn.execute(f"""
                SELECT timestamp, sector, avg_score, top_ticker, top_score
                FROM sector_snapshots
                {cutoff_clause}
                ORDER BY timestamp ASC
            """, params).fetchall()
        else:
            # Daily aggregation — AVG per calendar day per sector
            rows = conn.execute(f"""
                SELECT
                    strftime('%Y-%m-%dT12:00:00', timestamp) AS timestamp,
                    sector,
                    ROUND(AVG(avg_score), 4)  AS avg_score,
                    NULL                       AS top_ticker,
                    MAX(top_score)             AS top_score
                FROM sector_snapshots
                {cutoff_clause}
                GROUP BY strftime('%Y-%m-%d', timestamp), sector
                ORDER BY timestamp ASC
            """, params).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_existing_snapshot_dates(start_date: str, end_date: str) -> set[str]:
    """Return set of 'YYYY-MM-DD' strings that already have sector snapshots."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT strftime('%Y-%m-%d', timestamp) AS day
            FROM sector_snapshots
            WHERE timestamp >= ? AND timestamp <= ?
        """, (start_date, end_date + "T23:59:59")).fetchall()
        return {r["day"] for r in rows}
    finally:
        conn.close()


def get_latest_sector_scores() -> dict[str, float]:
    """
    Returns the most recent avg_score per sector.
    Used by dynamic sector cap computation.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT s.sector, s.avg_score
            FROM sector_snapshots s
            INNER JOIN (
                SELECT sector, MAX(timestamp) AS max_ts
                FROM sector_snapshots
                GROUP BY sector
            ) latest ON s.sector = latest.sector AND s.timestamp = latest.max_ts
        """).fetchall()
        return {r["sector"]: r["avg_score"] for r in rows}
    finally:
        conn.close()


def prune_sector_snapshots(keep_days: int = 1825) -> int:  # default 5 years
    """Delete sector_snapshots older than keep_days. Returns rows deleted."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM sector_snapshots WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def prune_signals(keep_per_ticker: int = 10) -> int:
    """
    Delete old signal rows, keeping only the most recent `keep_per_ticker`
    rows per ticker. Returns the number of rows deleted.
    """
    conn = get_db()
    try:
        cur = conn.execute("""
            DELETE FROM signals
            WHERE id NOT IN (
                SELECT id FROM signals s2
                WHERE s2.ticker = signals.ticker
                ORDER BY s2.timestamp DESC
                LIMIT ?
            )
        """, (keep_per_ticker,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
