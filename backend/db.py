import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


def _assert_insert_fields(fn_name: str, sql: str, row: dict) -> None:
    """
    Extract named params from INSERT SQL and error-log any missing from row.
    Uses row.get() fallback so nothing raises at the call site — but the
    missing field will write NULL silently without this guard.
    """
    required = set(re.findall(r":(\w+)", sql))
    missing  = required - row.keys()
    if missing:
        logger.error(
            f"{fn_name}: call site missing fields {sorted(missing)} — "
            "these will write NULL; update the insert dict at the call site"
        )

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "apex.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
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
                pnl                   REAL
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

            CREATE TABLE IF NOT EXISTS demo_gate_history (
                id                  INTEGER PRIMARY KEY,
                timestamp           TEXT NOT NULL,
                ticker              TEXT NOT NULL,
                sector              TEXT NOT NULL,
                signal_score        REAL,
                lock1_pass          INTEGER DEFAULT 0,
                lock2_pass          INTEGER DEFAULT 0,
                lock_leading_pass   INTEGER DEFAULT 0,
                lock_leading_checks TEXT,
                lock3_pass          INTEGER DEFAULT 0,
                gate_decision       TEXT,
                lock3_reasoning     TEXT,
                l2_summary          TEXT,
                macro_reason        TEXT
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

            CREATE TABLE IF NOT EXISTS watchlist (
                ticker    TEXT PRIMARY KEY,
                sector    TEXT NOT NULL,
                added_at  TEXT NOT NULL,
                source    TEXT NOT NULL DEFAULT 'auto'
            );

            CREATE TABLE IF NOT EXISTS ticker_history (
                ticker       TEXT NOT NULL,
                sector       TEXT NOT NULL,
                day          TEXT NOT NULL,
                signal_score REAL NOT NULL,
                PRIMARY KEY (ticker, day)
            );

            CREATE INDEX IF NOT EXISTS idx_ticker_hist_sector ON ticker_history(sector);
            CREATE INDEX IF NOT EXISTS idx_ticker_hist_day    ON ticker_history(day DESC);

            CREATE TABLE IF NOT EXISTS drift_baselines (
                parameter      TEXT NOT NULL,
                sector         TEXT NOT NULL,
                metric         TEXT NOT NULL,
                baseline_value REAL NOT NULL,
                n_trades       INTEGER NOT NULL,
                computed_at    TEXT NOT NULL,
                PRIMARY KEY (parameter, sector, metric)
            );

            CREATE TABLE IF NOT EXISTS drift_alerts (
                id             INTEGER PRIMARY KEY,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                parameter      TEXT NOT NULL,
                sector         TEXT NOT NULL,
                metric         TEXT NOT NULL,
                baseline_value REAL NOT NULL,
                current_value  REAL NOT NULL,
                deviation_pct  REAL NOT NULL,
                confidence     REAL NOT NULL,
                severity       TEXT NOT NULL,
                n_trades       INTEGER NOT NULL,
                acknowledged   INTEGER NOT NULL DEFAULT 0,
                UNIQUE (parameter, sector, metric)
            );

            CREATE INDEX IF NOT EXISTS idx_drift_alerts_severity ON drift_alerts(severity);
            CREATE INDEX IF NOT EXISTS idx_drift_alerts_ack      ON drift_alerts(acknowledged);

            CREATE TABLE IF NOT EXISTS sector_posteriors (
                sector      TEXT PRIMARY KEY,
                posterior   REAL NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sector_posterior_history (
                date        TEXT NOT NULL,
                sector      TEXT NOT NULL,
                posterior   REAL NOT NULL,
                PRIMARY KEY (date, sector)
            );

            CREATE TABLE IF NOT EXISTS lock4_pcr_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker         TEXT    NOT NULL,
                date           TEXT    NOT NULL,
                pcr            REAL    NOT NULL,
                expiry_1       TEXT,
                expiry_2       TEXT,
                call_oi        INTEGER,
                put_oi         INTEGER,
                is_dislocation INTEGER NOT NULL DEFAULT 0,
                UNIQUE (ticker, date)
            );

            CREATE INDEX IF NOT EXISTS idx_pcr_ticker_date ON lock4_pcr_history(ticker, date DESC);

            CREATE TABLE IF NOT EXISTS alert_latches (
                key    TEXT PRIMARY KEY,
                set_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS l5_token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                ticker        TEXT,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd      REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_l5_token_ts ON l5_token_usage(timestamp DESC);
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
        # Leading lock columns
        _add_column_if_missing(conn, "signals",           "lock_leading_pass",   "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "signals",           "lock_leading_checks", "TEXT")
        _add_column_if_missing(conn, "live_gate_history", "lock_leading_pass",   "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "live_gate_history", "lock_leading_checks", "TEXT")
        # Trailing stop — peak price tracking
        _add_column_if_missing(conn, "trades",      "peak_price", "REAL")
        _add_column_if_missing(conn, "live_trades", "peak_price", "REAL")
        conn.execute("""
            UPDATE live_trades SET peak_price = entry_price
            WHERE outcome = 'OPEN' AND peak_price IS NULL
        """)
        conn.commit()
        # Gate fail reasoning — why each lock failed
        _add_column_if_missing(conn, "signals",           "l2_summary",   "TEXT")
        _add_column_if_missing(conn, "signals",           "macro_reason", "TEXT")
        _add_column_if_missing(conn, "live_gate_history", "l2_summary",   "TEXT")
        _add_column_if_missing(conn, "live_gate_history", "macro_reason", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_demo_gate_ts ON demo_gate_history(timestamp DESC)")
        # Lock 3 (Grok sentiment) score and conviction — added alongside L3 gate
        _add_column_if_missing(conn, "demo_gate_history", "lock3_sentiment_score", "REAL")
        _add_column_if_missing(conn, "demo_gate_history", "lock3_conviction",      "TEXT")
        _add_column_if_missing(conn, "live_gate_history", "lock3_sentiment_score", "REAL")
        _add_column_if_missing(conn, "live_gate_history", "lock3_conviction",      "TEXT")
        # Ticker signal state — per-ticker regime signal at gate evaluation time
        # Values: breakout | extended | trending | rising | breakdown | recovering | weak
        # NULL on pre-existing rows: historical rows predate signal state tracking
        _add_column_if_missing(conn, "demo_gate_history", "ticker_signal", "TEXT")
        _add_column_if_missing(conn, "live_gate_history", "ticker_signal", "TEXT")
        # Near-term earnings flag — tickers that passed L1 with earnings in the soft window
        _add_column_if_missing(conn, "demo_gate_history", "earnings_near",     "INTEGER")
        _add_column_if_missing(conn, "demo_gate_history", "days_to_earnings",  "INTEGER")
        _add_column_if_missing(conn, "live_gate_history", "earnings_near",     "INTEGER")
        _add_column_if_missing(conn, "live_gate_history", "days_to_earnings",  "INTEGER")
        # overflow_slot: 1 when the trade executed into a slot beyond max_positions
        _add_column_if_missing(conn, "live_gate_history", "overflow_slot",     "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "demo_gate_history", "overflow_slot",     "INTEGER DEFAULT 0")

        # Migrate old gate_decision values to canonical FILTERED_* form
        conn.executescript("""
            UPDATE signals SET gate_decision = 'TRADE_EXECUTED' WHERE gate_decision = 'BUY';
            UPDATE signals SET gate_decision = 'FILTERED_L3'    WHERE gate_decision = 'HOLD';
            UPDATE signals SET gate_decision = 'FILTERED_L3'    WHERE gate_decision = 'SELL';
            UPDATE signals SET gate_decision = 'FILTERED_L1'    WHERE gate_decision = 'L1_FAIL';
            UPDATE signals SET gate_decision = 'FILTERED_L2'    WHERE gate_decision = 'L2_FAIL';
            UPDATE live_gate_history SET gate_decision = 'TRADE_EXECUTED' WHERE gate_decision = 'BUY';
            UPDATE live_gate_history SET gate_decision = 'FILTERED_L3'    WHERE gate_decision = 'HOLD';
            UPDATE live_gate_history SET gate_decision = 'FILTERED_L3'    WHERE gate_decision = 'SELL';
        """)
        # Profit-lock ratchet tracking — one-way flag per live trade.
        # True means the bracket SL leg has been moved up to the profit-lock
        # trail level. Never reset within a trade's lifetime: regime changes,
        # price updates, and position events do not clear it. Once the ratchet
        # fires, subsequent cycles skip the check entirely via this flag.
        _add_column_if_missing(conn, "live_trades", "profit_lock_activated", "INTEGER DEFAULT 0")
        conn.commit()
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


def prev_signals_by_ticker() -> dict[str, float]:
    """
    Returns the signal score from the second-most-recent poll per ticker.
    Used to compute per-ticker trend arrows.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, signal_score
            FROM signals s1
            WHERE timestamp = (
                SELECT DISTINCT timestamp FROM signals s2
                WHERE s2.ticker = s1.ticker
                ORDER BY timestamp DESC
                LIMIT 1 OFFSET 1
            )
        """).fetchall()
        return {r["ticker"]: round(r["signal_score"], 4) for r in rows}
    finally:
        conn.close()


def get_ticker_daily_scores(days: int = 180) -> list[dict]:
    """
    Daily signal score per ticker for the last N days.
    Merges ticker_history (backfilled daily scores) with the signals table
    (live intraday → daily aggregate) so streak signals work even when
    ticker_history has a gap between backfill end and system start.
    ticker_history is preferred; signals fills days not present there.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, sector, day, AVG(avg_score) AS avg_score
            FROM (
                SELECT ticker, sector, day, signal_score AS avg_score
                FROM ticker_history
                WHERE day >= DATE('now', ? || ' days')

                UNION ALL

                SELECT ticker, sector, DATE(timestamp) AS day, AVG(signal_score) AS avg_score
                FROM signals
                WHERE timestamp >= DATE('now', ? || ' days')
                  AND DATE(timestamp) NOT IN (
                      SELECT day FROM ticker_history th
                      WHERE th.ticker = signals.ticker
                        AND th.day >= DATE('now', ? || ' days')
                  )
                GROUP BY ticker, sector, DATE(timestamp)
            )
            GROUP BY ticker, sector, day
            ORDER BY ticker, day
        """, (f"-{days}", f"-{days}", f"-{days}")).fetchall()
        return [dict(r) for r in rows]
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


def get_lock1_candidates(threshold: float | None = None,
                         sector_thresholds: dict | None = None) -> list[dict]:
    """
    Latest signal per ticker filtered by threshold.
    If sector_thresholds provided, each ticker is filtered against its sector's
    calibrated threshold (falling back to `threshold` for uncalibrated sectors).

    NOTE — two-source threshold divergence: this pre-filter uses only the
    ticker_thresholds DB table (no SECTOR_THRESHOLD_FLOORS). lock2_quant.
    _sector_threshold() applies SECTOR_THRESHOLD_FLOORS as a floor on top of
    the DB value. For calibrated sectors the difference is immaterial (DB value
    ≥ floor by construction). For uncalibrated expansion sectors, `threshold`
    (the live config fallback, currently 0.65) acts as the pre-filter, which
    is likely above their eventual calibrated threshold — making this pre-filter
    over-conservative until calibration runs. Tickers blocked here never reach
    gate evaluation and produce no gate_history row, so CHECK 38 funnel counts
    understate true candidate suppression during the calibration lag window.
    """
    from backend.config import LOCK1_THRESHOLD
    fallback = threshold if threshold is not None else LOCK1_THRESHOLD
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
        """).fetchall()
        candidates = [dict(r) for r in rows]
    finally:
        conn.close()

    if sector_thresholds:
        return [
            c for c in candidates
            if c["signal_score"] >= sector_thresholds.get(c.get("sector", ""), fallback)
        ]
    return [c for c in candidates if c["signal_score"] >= fallback]


def update_signal_gate(signal_id: int, result: dict) -> None:
    """Write gate verdicts back to the signal row."""
    conn = get_db()
    try:
        conn.execute("""
            UPDATE signals
            SET lock1_pass          = :lock1_pass,
                lock2_pass          = :lock2_pass,
                lock_leading_pass   = :lock_leading_pass,
                lock_leading_checks = :lock_leading_checks,
                lock3_pass          = :lock3_pass,
                gate_decision       = :gate_decision,
                lock3_reasoning     = :lock3_reasoning,
                l2_summary          = :l2_summary,
                macro_reason        = :macro_reason
            WHERE id = :id
        """, {
            "lock1_pass":          result["lock1_pass"],
            "lock2_pass":          result["lock2_pass"],
            "lock_leading_pass":   result.get("lock_leading_pass", 0),
            "lock_leading_checks": result.get("lock_leading_checks"),
            "lock3_pass":          result["lock3_pass"],
            "gate_decision":       result["gate_decision"],
            "lock3_reasoning":     result.get("claude_reasoning"),
            "l2_summary":          result.get("l2_summary"),
            "macro_reason":        result.get("macro_reason"),
            "id":                  signal_id,
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
        balance      = STARTING_BALANCE + realized_pnl
        open_positions = open_row["cnt"] if open_row else 0

        sector_exposure = {
            r["sector"]: round(r["total"] / STARTING_BALANCE, 3)
            for r in exposure_rows
        } if invested > 0 else {}

        return {
            "balance":         round(balance, 2),
            "open_positions":  open_positions,
            "sector_exposure": sector_exposure,
            "starting_balance": STARTING_BALANCE,
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
               outcome, pnl, peak_price)
            VALUES
              (:timestamp, :ticker, :sector, :action, :price, :shares, :amount,
               :signal_score, :sentiment_score, :claude_confidence, :claude_reasoning,
               :outcome, :pnl, :peak_price)
        """, {**row, "peak_price": row.get("peak_price", row.get("price"))})
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_trade_peak_price(trade_id: int, peak_price: float) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE trades SET peak_price = ? WHERE id = ?", (peak_price, trade_id))
        conn.commit()
    finally:
        conn.close()


def update_live_trade_peak_price(trade_id: int, peak_price: float) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE live_trades SET peak_price = ? WHERE id = ?", (peak_price, trade_id))
        conn.commit()
    finally:
        conn.close()


def set_live_trade_profit_lock_activated(trade_id: int) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE live_trades SET profit_lock_activated = 1 WHERE id = ?", (trade_id,))
        conn.commit()
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
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM signals
            WHERE gate_decision IN ('FILTERED_L2', 'FILTERED_L3')
            AND timestamp > ?
        """, (cutoff,)).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_recently_exited_tickers(sl_hours: float, tp_hours: float) -> set[str]:
    """Return tickers blocked from re-entry after a recent exit.

    Only genuine TP/TSL wins use tp_hours (7d).
    REGIME wins, TIME wins, LOSS, and EXPIRED all use sl_hours (24h).
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    sl_cutoff = (now - timedelta(hours=sl_hours)).isoformat()
    tp_cutoff = (now - timedelta(hours=tp_hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM trades
            WHERE exited_at IS NOT NULL
              AND outcome IN ('WIN', 'LOSS', 'EXPIRED')
              AND exited_at > CASE
                  WHEN outcome = 'WIN' AND exit_reason IN ('TP', 'TSL') THEN ?
                  ELSE ?
              END
        """, (tp_cutoff, sl_cutoff)).fetchall()
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


def get_ticker_gate_fails(ticker: str, limit: int = 5) -> list[dict]:
    """
    Recent non-skip gate decisions for a ticker from the demo signals table.
    Returns up to `limit` rows newest-first, with the most relevant fail detail
    collapsed into a single 'detail' string for Lock 3 context injection.
    """
    import json as _json
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT timestamp, gate_decision, l2_summary, macro_reason,
                   lock_leading_checks, lock3_reasoning
            FROM signals
            WHERE ticker = ?
              AND gate_decision IS NOT NULL
              AND gate_decision NOT IN ('SKIPPED_OPEN', 'SKIPPED_COOLOFF')
            ORDER BY timestamp DESC
            LIMIT ?
        """, (ticker, limit)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        decision = r["gate_decision"]
        detail = None
        if r["l2_summary"]:
            detail = r["l2_summary"]
        elif r["macro_reason"]:
            detail = r["macro_reason"]
        elif r["lock_leading_checks"]:
            try:
                checks = _json.loads(r["lock_leading_checks"])
                failed = [k for k, v in checks.items() if not v.get("pass")]
                reasons = [checks[k].get("reason", "") for k in failed if checks[k].get("reason")]
                detail = "leading failed: " + "; ".join(reasons) if reasons else "leading checks failed"
            except Exception:
                detail = "leading checks failed"
        elif r["lock3_reasoning"]:
            detail = r["lock3_reasoning"]
        result.append({
            "date":     r["timestamp"][:10],
            "outcome":  decision,
            "detail":   detail,
        })
    return result


def get_live_ticker_gate_fails(ticker: str, limit: int = 5) -> list[dict]:
    """
    Recent non-skip gate decisions for a ticker from live_gate_history.
    Same shape as get_ticker_gate_fails for consistent context injection.
    """
    import json as _json
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT timestamp, gate_decision, l2_summary, macro_reason,
                   lock_leading_checks, lock3_reasoning
            FROM live_gate_history
            WHERE ticker = ?
              AND gate_decision IS NOT NULL
              AND gate_decision NOT IN ('SKIPPED_OPEN', 'SKIPPED_COOLOFF')
            ORDER BY timestamp DESC
            LIMIT ?
        """, (ticker, limit)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        decision = r["gate_decision"]
        detail = None
        if r["l2_summary"]:
            detail = r["l2_summary"]
        elif r["macro_reason"]:
            detail = r["macro_reason"]
        elif r["lock_leading_checks"]:
            try:
                checks = _json.loads(r["lock_leading_checks"])
                failed = [k for k, v in checks.items() if not v.get("pass")]
                reasons = [checks[k].get("reason", "") for k in failed if checks[k].get("reason")]
                detail = "leading failed: " + "; ".join(reasons) if reasons else "leading checks failed"
            except Exception:
                detail = "leading checks failed"
        elif r["lock3_reasoning"]:
            detail = r["lock3_reasoning"]
        result.append({
            "date":     r["timestamp"][:10],
            "outcome":  decision,
            "detail":   detail,
        })
    return result


def get_recently_failed_live_tickers(hours: float) -> set[str]:
    """Return tickers that failed L2, L3, or MACRO in the live gate within the last N hours."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM live_gate_history
            WHERE gate_decision IN ('FILTERED_L2', 'FILTERED_L3')
            AND timestamp > ?
        """, (cutoff,)).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def get_recently_exited_live_tickers(sl_hours: float, tp_hours: float) -> set[str]:
    """Return live tickers blocked from re-entry after a recent exit.

    Only genuine TP/TSL wins use tp_hours (7d).
    REGIME wins, TIME wins, LOSS, and EXPIRED all use sl_hours (24h).
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    sl_cutoff = (now - timedelta(hours=sl_hours)).isoformat()
    tp_cutoff = (now - timedelta(hours=tp_hours)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM live_trades
            WHERE exited_at IS NOT NULL
              AND outcome IN ('WIN', 'LOSS', 'EXPIRED')
              AND exited_at > CASE
                  WHEN outcome = 'WIN' AND exit_reason IN ('TP', 'TSL') THEN ?
                  ELSE ?
              END
        """, (tp_cutoff, sl_cutoff)).fetchall()
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
                   gate_decision, lock1_pass, lock2_pass, lock_leading_pass,
                   lock_leading_checks, lock3_pass, lock3_reasoning,
                   l2_summary, macro_reason
            FROM signals
            WHERE gate_decision IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def insert_demo_gate_result(row: dict) -> None:
    conn = get_db()
    try:
        _SQL = """
            INSERT INTO demo_gate_history
                (timestamp, ticker, sector, signal_score,
                 lock1_pass, lock2_pass, lock_leading_pass, lock_leading_checks,
                 lock3_pass, gate_decision, lock3_reasoning, l2_summary,
                 lock3_sentiment_score, lock3_conviction, macro_reason, ticker_signal,
                 earnings_near, days_to_earnings, overflow_slot)
            VALUES
                (:timestamp, :ticker, :sector, :signal_score,
                 :lock1_pass, :lock2_pass, :lock_leading_pass, :lock_leading_checks,
                 :lock3_pass, :gate_decision, :lock3_reasoning, :l2_summary,
                 :lock3_sentiment_score, :lock3_conviction, :macro_reason, :ticker_signal,
                 :earnings_near, :days_to_earnings, :overflow_slot)
        """
        _assert_insert_fields("insert_demo_gate_result", _SQL, row)
        conn.execute(_SQL, {**row,
              "lock3_sentiment_score": row.get("lock3_sentiment_score"),
              "lock3_conviction":      row.get("lock3_conviction"),
              "ticker_signal":         row.get("ticker_signal"),
              "earnings_near":         row.get("earnings_near"),
              "days_to_earnings":      row.get("days_to_earnings"),
              "overflow_slot":         1 if row.get("overflow_slot") else 0})
        conn.commit()
    finally:
        conn.close()


def get_demo_gate_history(since: str | None = None, limit: int | None = 500) -> list[dict]:
    conn = get_db()
    try:
        where = "WHERE timestamp >= ?" if since else ""
        params: tuple = (since,) if since else ()
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        rows = conn.execute(f"""
            SELECT ticker, sector, timestamp, signal_score,
                   gate_decision, lock1_pass, lock2_pass, lock_leading_pass,
                   lock_leading_checks, lock3_pass, lock3_reasoning,
                   l2_summary, lock3_sentiment_score, lock3_conviction, macro_reason,
                   ticker_signal, earnings_near, days_to_earnings, overflow_slot
            FROM demo_gate_history
            {where}
            ORDER BY timestamp DESC
            {limit_clause}
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_demo_gate_funnel_counts(since: str | None = None) -> dict:
    conn = get_db()
    try:
        where = "WHERE timestamp >= ?" if since else ""
        params = (since,) if since else ()
        row = conn.execute(f"""
            SELECT
                count(*)                                                         AS total_candidates,
                sum(gate_decision = 'SKIPPED_OPEN')                              AS skipped_open,
                sum(gate_decision = 'SKIPPED_COOLOFF')                           AS skipped_cooloff,
                sum(gate_decision NOT IN ('SKIPPED_OPEN','SKIPPED_COOLOFF'))     AS evaluated,
                sum(gate_decision = 'FILTERED_ELIGIBILITY')                      AS eligibility_fail,
                sum(gate_decision = 'FILTERED_L1')                               AS l1_fail,
                sum(gate_decision = 'FILTERED_L2')                               AS l2_fail,
                sum(gate_decision = 'FILTERED_LEADING')                          AS leading_fail,
                sum(gate_decision = 'FILTERED_L3')                               AS l3_fail,
                sum(gate_decision = 'FILTERED_OVERFLOW_QUANT')                   AS overflow_fail,
                sum(gate_decision IN ('TRADE_EXECUTED','TRADE_REJECTED'))         AS executed
            FROM demo_gate_history
            {where}
        """, params).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_gate_funnel_counts(since: str | None = None) -> dict:
    """
    Full funnel counts including pre-gate skips.
    since: ISO timestamp cutoff (optional).
    """
    conn = get_db()
    try:
        where = "WHERE gate_decision IS NOT NULL"
        params: tuple = ()
        if since:
            where += " AND timestamp >= ?"
            params = (since,)

        rows = conn.execute(f"""
            SELECT gate_decision, COUNT(*) AS cnt
            FROM signals
            {where}
            GROUP BY gate_decision
        """, params).fetchall()

        counts = {r["gate_decision"]: r["cnt"] for r in rows}
        skipped_open    = counts.get("SKIPPED_OPEN", 0)
        skipped_cooloff = counts.get("SKIPPED_COOLOFF", 0)
        l1_fail         = counts.get("FILTERED_L1", 0)
        eligibility_fail = counts.get("FILTERED_ELIGIBILITY", 0)
        l2_fail         = counts.get("FILTERED_L2", 0)
        leading_fail    = counts.get("FILTERED_LEADING", 0)
        l3_fail         = counts.get("FILTERED_L3", 0)
        overflow_fail   = counts.get("FILTERED_OVERFLOW_QUANT", 0)
        executed        = counts.get("TRADE_EXECUTED", 0)
        rejected        = counts.get("TRADE_REJECTED", 0)

        total_candidates = sum(counts.values())
        evaluated = total_candidates - skipped_open - skipped_cooloff

        return {
            "total_candidates": total_candidates,
            "skipped_open":     skipped_open,
            "skipped_cooloff":  skipped_cooloff,
            "evaluated":        evaluated,
            "l1_fail":          l1_fail,
            "eligibility_fail":  eligibility_fail,
            "l2_fail":          l2_fail,
            "leading_fail":     leading_fail,
            "l3_fail":          l3_fail,
            "overflow_fail":    overflow_fail,
            "executed":         executed,
            "rejected":         rejected,
        }
    finally:
        conn.close()


def get_equity_curve(current_prices: dict[str, float] | None = None) -> list[dict]:
    """
    Running balance over time from closed trades, plus a live 'Now' point that
    marks open positions to market.

    current_prices: live price dict {ticker: price} supplied by the caller.
    If not provided, falls back to the latest stored signal price (up to 15 min stale).

    Without the MTM point, flat segments are ambiguous — they could mean
    'nothing happened' or 'open positions are moving but not yet closed'.
    """
    from datetime import datetime, timezone

    from backend.config import STARTING_BALANCE
    conn = get_db()
    try:
        closed = conn.execute("""
            SELECT exited_at AS ts, pnl
            FROM trades
            WHERE outcome IN ('WIN','LOSS','EXPIRED') AND exited_at IS NOT NULL
            ORDER BY exited_at ASC
        """).fetchall()

        points = [{"ts": "Start", "balance": STARTING_BALANCE, "mtm": False}]
        balance = STARTING_BALANCE
        for r in closed:
            balance += r["pnl"] or 0
            points.append({"ts": r["ts"][:10], "balance": round(balance, 2), "mtm": False})

        # Mark open positions to market
        open_trades = conn.execute("""
            SELECT t.ticker, t.price AS entry_price, t.amount,
                   s.price AS signal_price
            FROM trades t
            LEFT JOIN (
                SELECT ticker, price
                FROM signals s1
                WHERE timestamp = (
                    SELECT MAX(timestamp) FROM signals s2 WHERE s2.ticker = s1.ticker
                )
            ) s ON s.ticker = t.ticker
            WHERE t.outcome = 'OPEN' AND t.action = 'BUY'
        """).fetchall()

        unrealised = 0.0
        for t in open_trades:
            if current_prices is not None:
                price = current_prices.get(t["ticker"])
            else:
                price = t["signal_price"]
            if price and t["entry_price"] and t["entry_price"] > 0:
                unrealised += t["amount"] * (price - t["entry_price"]) / t["entry_price"]

        now_ts = datetime.now(timezone.utc).strftime("%m/%d %H:%M")
        points.append({
            "ts":      now_ts,
            "balance": round(balance + unrealised, 2),
            "mtm":     True,
            "unrealised": round(unrealised, 2),
        })

        return points
    finally:
        conn.close()


def get_live_equity_curve(unrealised_total: float = 0.0, since: str | None = None) -> list[dict]:
    """
    Running balance over time from closed live trades, plus a live MTM 'Now' point.
    Mirrors get_equity_curve() but reads from live_trades instead of trades.
    unrealised_total: summed unrealized P&L from Alpaca positions — caller supplies this
    so we use Alpaca's actual fill prices rather than our DB signal prices.
    since: ISO date string (YYYY-MM-DD) — only include trades on or after this date.
    """
    from datetime import datetime, timezone

    from backend.live_config import get_live_config
    cfg                  = get_live_config()
    starting_balance     = cfg.get("starting_balance", 100_000.0)
    conn = get_db()
    try:
        if since:
            closed = conn.execute("""
                SELECT exited_at AS ts, pnl
                FROM live_trades
                WHERE outcome IN ('WIN', 'LOSS') AND exited_at IS NOT NULL
                  AND timestamp >= ?
                ORDER BY exited_at ASC
            """, (since,)).fetchall()
        else:
            closed = conn.execute("""
                SELECT exited_at AS ts, pnl
                FROM live_trades
                WHERE outcome IN ('WIN', 'LOSS') AND exited_at IS NOT NULL
                ORDER BY exited_at ASC
            """).fetchall()

        points = [{"ts": "Start", "balance": starting_balance, "mtm": False}]
        balance = starting_balance
        for r in closed:
            balance += r["pnl"] or 0
            points.append({"ts": r["ts"][:10], "balance": round(balance, 2), "mtm": False})

        now_ts = datetime.now(timezone.utc).strftime("%m/%d %H:%M")
        points.append({
            "ts":         now_ts,
            "balance":    round(balance + unrealised_total, 2),
            "mtm":        True,
            "unrealised": round(unrealised_total, 2),
        })

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
        _SQL = """
            INSERT INTO live_gate_history
              (timestamp, ticker, sector, signal_score,
               lock1_pass, lock2_pass, lock_leading_pass, lock_leading_checks,
               lock3_pass, gate_decision, lock3_reasoning, alpaca_order_id,
               l2_summary, lock3_sentiment_score, lock3_conviction, macro_reason,
               ticker_signal, earnings_near, days_to_earnings, overflow_slot)
            VALUES
              (:timestamp, :ticker, :sector, :signal_score,
               :lock1_pass, :lock2_pass, :lock_leading_pass, :lock_leading_checks,
               :lock3_pass, :gate_decision, :lock3_reasoning, :alpaca_order_id,
               :l2_summary, :lock3_sentiment_score, :lock3_conviction, :macro_reason,
               :ticker_signal, :earnings_near, :days_to_earnings, :overflow_slot)
        """
        _assert_insert_fields("insert_live_gate_result", _SQL, row)
        cur = conn.execute(_SQL, {**row,
              "lock_leading_pass":      row.get("lock_leading_pass", 0),
              "lock_leading_checks":    row.get("lock_leading_checks"),
              "l2_summary":             row.get("l2_summary"),
              "lock3_sentiment_score":  row.get("lock3_sentiment_score"),
              "lock3_conviction":       row.get("lock3_conviction"),
              "macro_reason":           row.get("macro_reason"),
              "ticker_signal":          row.get("ticker_signal"),
              "earnings_near":          row.get("earnings_near"),
              "days_to_earnings":       row.get("days_to_earnings"),
              "overflow_slot":          1 if row.get("overflow_slot") else 0})
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_live_gate_history(since: str | None = None, limit: int | None = 500) -> list[dict]:
    conn = get_db()
    try:
        where = "WHERE timestamp >= ?" if since else ""
        params: tuple = (since,) if since else ()
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        rows = conn.execute(f"""
            SELECT * FROM live_gate_history
            {where}
            ORDER BY timestamp DESC
            {limit_clause}
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_live_gate_funnel_counts(since: str | None = None) -> dict:
    """Full funnel counts for the live gate, including pre-gate skips."""
    conn = get_db()
    try:
        where = "WHERE gate_decision IS NOT NULL"
        params: tuple = ()
        if since:
            where += " AND timestamp >= ?"
            params = (since,)

        rows = conn.execute(f"""
            SELECT gate_decision, COUNT(*) AS cnt
            FROM live_gate_history
            {where}
            GROUP BY gate_decision
        """, params).fetchall()

        overflow_executed_row = conn.execute(f"""
            SELECT COUNT(*) AS cnt
            FROM live_gate_history
            {where} AND gate_decision = 'TRADE_EXECUTED' AND overflow_slot = 1
        """, params).fetchone()

        counts = {r["gate_decision"]: r["cnt"] for r in rows}
        skipped_open      = counts.get("SKIPPED_OPEN", 0)
        skipped_cooloff   = counts.get("SKIPPED_COOLOFF", 0)
        l1_fail           = counts.get("FILTERED_L1", 0)
        eligibility_fail  = counts.get("FILTERED_ELIGIBILITY", 0)
        l2_fail           = counts.get("FILTERED_L2", 0)
        leading_fail      = counts.get("FILTERED_LEADING", 0)
        l3_fail           = counts.get("FILTERED_L3", 0)
        overflow_fail     = counts.get("FILTERED_OVERFLOW_QUANT", 0)
        overflow_executed = overflow_executed_row["cnt"] if overflow_executed_row else 0
        executed          = counts.get("TRADE_EXECUTED", 0)
        rejected          = counts.get("TRADE_REJECTED", 0)

        total_candidates = sum(counts.values())
        evaluated = total_candidates - skipped_open - skipped_cooloff

        return {
            "total_candidates":  total_candidates,
            "skipped_open":      skipped_open,
            "skipped_cooloff":   skipped_cooloff,
            "evaluated":         evaluated,
            "l1_fail":           l1_fail,
            "eligibility_fail":  eligibility_fail,
            "l2_fail":           l2_fail,
            "leading_fail":      leading_fail,
            "l3_fail":           l3_fail,
            "overflow_fail":     overflow_fail,
            "overflow_executed": overflow_executed,
            "executed":          executed,
            "rejected":          rejected,
        }
    finally:
        conn.close()


def insert_pcr_observation(
    ticker: str,
    date: str,
    pcr: float,
    expiry_1: str | None = None,
    expiry_2: str | None = None,
    call_oi: int | None = None,
    put_oi: int | None = None,
    is_dislocation: bool = False,
) -> None:
    """
    Insert a daily P/C ratio observation for Lock 4 baseline calibration.
    Skips silently on duplicate (ticker, date) — idempotent.
    """
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO lock4_pcr_history
              (ticker, date, pcr, expiry_1, expiry_2, call_oi, put_oi, is_dislocation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, date, round(pcr, 4), expiry_1, expiry_2, call_oi, put_oi,
             1 if is_dislocation else 0),
        )
        conn.commit()
    finally:
        conn.close()


def get_pcr_history(
    ticker: str | None = None,
    exclude_dislocation: bool = True,
    min_date: str | None = None,
) -> list[dict]:
    """
    Retrieve PCR history for percentile calibration.

    Args:
        ticker:              Filter to a specific ticker (None = all).
        exclude_dislocation: Drop rows where is_dislocation=1 (default True).
        min_date:            ISO date string lower bound (inclusive).
    """
    conn = get_db()
    try:
        clauses = []
        params: list = []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker)
        if exclude_dislocation:
            clauses.append("is_dislocation = 0")
        if min_date:
            clauses.append("date >= ?")
            params.append(min_date)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT ticker, date, pcr, expiry_1, expiry_2, call_oi, put_oi, is_dislocation "
            f"FROM lock4_pcr_history {where} ORDER BY ticker, date",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_pcr_dislocation(date: str, is_dislocation: bool) -> int:
    """Manually flag or unflag all observations on a given date as dislocation days."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE lock4_pcr_history SET is_dislocation = ? WHERE date = ?",
            (1 if is_dislocation else 0, date),
        )
        conn.commit()
        return cur.rowcount
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


def set_alert_latch(key: str) -> bool:
    """
    Persisted one-shot alert dedup — survives process restarts, unlike a
    module-level set() (2026-08-07: uvicorn --reload cleared _untracked_alerted
    mid-incident and re-sent an already-latched alert; a plain in-memory set
    can't tell a real recurrence from a redeploy). Returns True if the latch
    was newly set (caller should alert), False if it was already set.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO alert_latches (key, set_at) VALUES (?, ?)",
            (key, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_alert_latch(key: str) -> None:
    """Release a latch set by set_alert_latch() so a future recurrence re-alerts."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM alert_latches WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def clear_alert_latches_except(prefix: str, keep_keys: set[str]) -> None:
    """
    Release all latches under `prefix` (e.g. "untracked:") not in keep_keys —
    the persisted equivalent of set.intersection_update(), for latch families
    keyed per-ticker/per-item rather than a single fixed key.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key FROM alert_latches WHERE key LIKE ?", (f"{prefix}%",)
        ).fetchall()
        stale = [r["key"] for r in rows if r["key"] not in keep_keys]
        if stale:
            conn.executemany("DELETE FROM alert_latches WHERE key = ?", [(k,) for k in stale])
            conn.commit()
    finally:
        conn.close()


def mark_live_trade_unreconciled(trade_id: int, note: str) -> None:
    """
    Position vanished from the broker with no fill, order, or activity record
    to explain it (distinct from a normal SL/TP/time exit). Freezes the trade
    out of get_open_live_trades() polling — no exit_price/pnl is fabricated —
    until a human or a confirmed broker record resolves it.
    """
    from datetime import datetime, timezone
    conn = get_db()
    try:
        conn.execute("""
            UPDATE live_trades
            SET outcome = 'UNRECONCILED', exit_reason = ?, exited_at = ?
            WHERE id = ?
        """, (note, datetime.now(timezone.utc).isoformat(), trade_id))
        conn.commit()
    finally:
        conn.close()


def get_unreconciled_live_trades() -> list[dict]:
    """
    Trades frozen by mark_live_trade_unreconciled() and not yet resolved.
    The live gate must refuse all new entries while this is non-empty — see
    gate_runner_live.run() (CHECK 66). A day-boundary equity reset or the
    broker quietly restoring a wiped position must not be able to release
    the halt on its own; only resolve_unreconciled() can.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM live_trades WHERE outcome = 'UNRECONCILED' ORDER BY timestamp ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_unreconciled(trade_id: int, resolution: str, evidence: str,
                          exit_price: float | None = None) -> None:
    """
    Close out an UNRECONCILED trade. Two paths:

    - resolution="CONFIRMED_EXIT": a broker record (order, activity, or Alpaca
      support confirmation) surfaced the real fill. exit_price is required;
      pnl and outcome (WIN/LOSS) are computed from it same as a normal exit.
    - resolution="WRITTEN_OFF": no broker record ever surfaced; Alpaca support
      confirmed the wipe (or it's being written off after investigation).
      Books the full position value as lost. exit_price is not used.

    Both paths assume the trade actually ended. If the position turns out to
    still be held at the broker — a snapshot omission that self-corrected
    rather than a real exit or a real loss — use reopen_unreconciled()
    instead; forcing that case through either path here fabricates an exit
    that never happened (see the 2026-08-06/07 HON incident, CHECK 66).

    `evidence` must be non-empty and is appended verbatim to exit_reason — this
    is the human sign-off trail; there is no other way for a trade to leave
    the UNRECONCILED state, and CHECK 66 will keep flagging until one of these
    is called. `outcome` is deliberately never WIN/LOSS/EXPIRED for a written-off
    trade so it can't be silently binned into win-rate/cooloff stats alongside
    real exits — see WRITTEN_OFF handling in downstream `outcome` consumers.
    """
    if not evidence:
        raise ValueError("resolve_unreconciled requires non-empty evidence")
    if resolution not in ("CONFIRMED_EXIT", "WRITTEN_OFF"):
        raise ValueError(f"resolution must be CONFIRMED_EXIT or WRITTEN_OFF, got {resolution!r}")

    from datetime import datetime, timezone
    conn = get_db()
    try:
        row = conn.execute("SELECT entry_price, qty FROM live_trades WHERE id = ?",
                            (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"no live_trades row with id={trade_id}")
        entry_price, qty = row["entry_price"], row["qty"]

        exited_at = datetime.now(timezone.utc).isoformat()
        note = f"[{resolution}] {evidence}"

        if resolution == "CONFIRMED_EXIT":
            if exit_price is None:
                raise ValueError("CONFIRMED_EXIT requires exit_price")
            pnl     = round((exit_price - entry_price) * qty, 2)
            outcome = "WIN" if pnl > 0 else "LOSS"
        else:
            exit_price = 0.0
            pnl         = round(-entry_price * qty, 2)
            outcome     = "WRITTEN_OFF"

        conn.execute("""
            UPDATE live_trades
            SET outcome = ?, exit_price = ?, pnl = ?, exit_reason = ?, exited_at = ?
            WHERE id = ?
        """, (outcome, exit_price, pnl, note, exited_at, trade_id))
        conn.commit()
    finally:
        conn.close()


def reopen_unreconciled(trade_id: int, evidence: str) -> None:
    """
    Release an UNRECONCILED trade back to normal open-position management
    because the position was confirmed still held at the broker, matching
    what it should be — the freeze was a reporting-layer false alarm, not a
    real exit or a real loss. Unlike resolve_unreconciled(), this does not
    fabricate an exit_price/pnl: it clears them (they were never real to
    begin with — see the fabricated -$4.04 fallback in the HON incident)
    and restores outcome='OPEN' so get_open_live_trades()/live_trades_tracker
    resume normal TP/SL/peak-price management on it, and CHECK 66 stops
    flagging it.

    `evidence` must be non-empty — same sign-off discipline as
    resolve_unreconciled(): what confirmed the position is still genuinely
    held (e.g. "Alpaca account/positions snapshot checked <date>, HON shown
    at qty/entry matching live_trades row, portfolio-history never showed a
    gap") and by whom.
    """
    if not evidence:
        raise ValueError("reopen_unreconciled requires non-empty evidence")

    from datetime import datetime, timezone
    conn = get_db()
    try:
        row = conn.execute("SELECT outcome FROM live_trades WHERE id = ?",
                            (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"no live_trades row with id={trade_id}")
        if row["outcome"] != "UNRECONCILED":
            raise ValueError(
                f"live_trades id={trade_id} outcome is {row['outcome']!r}, not UNRECONCILED"
            )

        note = f"[REOPENED — confirmed still held] {evidence}"
        conn.execute("""
            UPDATE live_trades
            SET outcome = 'OPEN', exit_price = NULL, pnl = NULL,
                exit_reason = ?, exited_at = NULL
            WHERE id = ?
        """, (note, trade_id))
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
    from datetime import datetime, timedelta, timezone
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
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM sector_snapshots WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def insert_ticker_history(rows: list[dict]) -> int:
    """
    Upsert per-ticker daily signal scores into ticker_history.
    Each row: {ticker, sector, day, signal_score}.
    Returns number of rows inserted (ignores duplicates).
    """
    if not rows:
        return 0
    conn = get_db()
    try:
        cur = conn.executemany("""
            INSERT OR REPLACE INTO ticker_history (ticker, sector, day, signal_score)
            VALUES (:ticker, :sector, :day, :signal_score)
        """, rows)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_yesterday_sector_avg_scores() -> dict[str, float]:
    """
    Returns the most recent completed day's average signal score per sector.
    Used for day-over-day sector trend arrows.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT sector, AVG(signal_score) AS avg_score
            FROM ticker_history
            WHERE day = (
                SELECT MAX(day) FROM ticker_history WHERE day < DATE('now')
            )
            GROUP BY sector
        """).fetchall()
        return {r["sector"]: round(r["avg_score"], 4) for r in rows}
    finally:
        conn.close()


def get_yesterday_ticker_scores() -> dict[str, float]:
    """
    Returns the most recent completed day's signal score per ticker from ticker_history.
    Used for day-over-day trend arrows — more meaningful than poll-over-poll comparison.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, signal_score
            FROM ticker_history
            WHERE day = (
                SELECT MAX(day) FROM ticker_history WHERE day < DATE('now')
            )
        """).fetchall()
        return {r["ticker"]: r["signal_score"] for r in rows}
    finally:
        conn.close()


def get_prev_ticker_prices() -> dict[str, float]:
    """
    Returns the price from the second-most-recent signal poll per ticker.
    Used to compute intraday price-change % (chg) in the sector summary endpoint.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, price
            FROM signals s1
            WHERE price IS NOT NULL
              AND timestamp = (
                  SELECT timestamp FROM signals s2
                  WHERE s2.ticker = s1.ticker AND s2.price IS NOT NULL
                  ORDER BY timestamp DESC
                  LIMIT 1 OFFSET 1
              )
        """).fetchall()
        return {r["ticker"]: r["price"] for r in rows}
    finally:
        conn.close()


def get_existing_ticker_history_dates(start_date: str, end_date: str) -> set[str]:
    """Return set of days that already have ticker_history rows in the date range."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT day FROM ticker_history
            WHERE day >= ? AND day <= ?
        """, (start_date, end_date)).fetchall()
        return {r["day"] for r in rows}
    finally:
        conn.close()


def get_ticker_thresholds() -> dict[str, float]:
    """
    Return per-sector calibrated thresholds from ticker_history.
    Falls back to empty dict if not enough data.
    Thresholds are stored in a simple key-value table.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sector, threshold FROM ticker_thresholds"
        ).fetchall()
        return {r["sector"]: r["threshold"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def save_ticker_thresholds(thresholds: dict[str, float]) -> None:
    """Persist calibrated per-sector thresholds."""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ticker_thresholds (
                sector    TEXT PRIMARY KEY,
                threshold REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany("""
            INSERT INTO ticker_thresholds (sector, threshold, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sector) DO UPDATE SET threshold=excluded.threshold, updated_at=excluded.updated_at
        """, [(s, t, now) for s, t in thresholds.items()])
        conn.commit()
    finally:
        conn.close()


def get_watchlist() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ticker, sector, added_at, source FROM watchlist ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_watchlist(ticker: str, sector: str, source: str = "auto") -> None:
    """Add ticker to watchlist. If already present, only upgrades source to 'manual'."""
    from datetime import datetime, timezone
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO watchlist (ticker, sector, added_at, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                source = CASE WHEN excluded.source = 'manual' THEN 'manual' ELSE source END
        """, (ticker, sector, datetime.now(timezone.utc).isoformat(), source))
        conn.commit()
    finally:
        conn.close()


def remove_watchlist(ticker: str) -> bool:
    """Remove ticker from watchlist. Returns True if it existed."""
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def prune_watchlist_auto(keep_tickers: set[str]) -> int:
    """
    Remove auto-added tickers that are no longer RECOVERING.
    Manual entries are never auto-removed.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE source = 'auto'"
        ).fetchall()
        to_remove = [r["ticker"] for r in rows if r["ticker"] not in keep_tickers]
        if to_remove:
            conn.executemany("DELETE FROM watchlist WHERE ticker = ?", [(t,) for t in to_remove])
            conn.commit()
        return len(to_remove)
    finally:
        conn.close()


def prune_signals(keep_per_ticker: int = 10) -> int:
    """
    Delete old signal rows, keeping only the most recent `keep_per_ticker`
    rows per ticker. Rows with a gate_decision are always preserved so that
    gate history is not lost. Returns the number of rows deleted.
    """
    conn = get_db()
    try:
        cur = conn.execute("""
            DELETE FROM signals
            WHERE gate_decision IS NULL
              AND id NOT IN (
                SELECT id FROM signals s2
                WHERE s2.ticker = signals.ticker
                  AND s2.gate_decision IS NULL
                ORDER BY s2.timestamp DESC
                LIMIT ?
            )
        """, (keep_per_ticker,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── Drift monitor ─────────────────────────────────────────────────────────────

def has_drift_baselines() -> bool:
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM drift_baselines").fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


def upsert_drift_baseline(parameter: str, sector: str, metric: str,
                          value: float, n_trades: int) -> None:
    from datetime import datetime, timezone
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO drift_baselines (parameter, sector, metric, baseline_value, n_trades, computed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(parameter, sector, metric)
            DO UPDATE SET baseline_value = excluded.baseline_value,
                          n_trades       = excluded.n_trades,
                          computed_at    = excluded.computed_at
        """, (parameter, sector, metric, value, n_trades,
              datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def get_drift_baselines() -> dict:
    """Returns {(parameter, sector, metric): {baseline_value, n_trades}}."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM drift_baselines").fetchall()
        return {(r["parameter"], r["sector"], r["metric"]): dict(r) for r in rows}
    finally:
        conn.close()


def upsert_drift_alert(row: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO drift_alerts
              (created_at, updated_at, parameter, sector, metric,
               baseline_value, current_value, deviation_pct,
               confidence, severity, n_trades, acknowledged)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(parameter, sector, metric)
            DO UPDATE SET
                updated_at     = excluded.updated_at,
                baseline_value = excluded.baseline_value,
                current_value  = excluded.current_value,
                deviation_pct  = excluded.deviation_pct,
                confidence     = excluded.confidence,
                severity       = excluded.severity,
                n_trades       = excluded.n_trades,
                acknowledged   = 0
        """, (now, now,
              row["parameter"], row["sector"], row["metric"],
              row["baseline_value"], row["current_value"], row["deviation_pct"],
              row["confidence"], row["severity"], row["n_trades"]))
        conn.commit()
    finally:
        conn.close()


def get_drift_alerts(severity: str | None = None,
                     acknowledged: bool = False,
                     limit: int = 20) -> list[dict]:
    conn = get_db()
    try:
        filters = ["acknowledged = ?"]
        params: list = [int(acknowledged)]
        if severity:
            filters.append("severity = ?")
            params.append(severity.upper())
        where = "WHERE " + " AND ".join(filters)
        rows = conn.execute(f"""
            SELECT * FROM drift_alerts
            {where}
            ORDER BY
                CASE severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                ABS(deviation_pct) DESC
            LIMIT ?
        """, (*params, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Lock 5 token usage ────────────────────────────────────────────────────────

def insert_l5_token_usage(ticker: str | None, input_tokens: int,
                           output_tokens: int, cost_usd: float) -> None:
    from datetime import datetime, timezone
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO l5_token_usage (timestamp, ticker, input_tokens, output_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), ticker,
             input_tokens, output_tokens, round(cost_usd, 6)),
        )
        conn.commit()
    finally:
        conn.close()


def get_l5_spend_summary() -> dict:
    """
    Returns L5 token spend aggregated over 7-day and 30-day windows.
    Each window includes: total_cost_usd, call_count, daily_rate_usd.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                SUM(CASE WHEN timestamp >= DATE('now', '-7 days')  THEN cost_usd ELSE 0 END) AS cost_7d,
                SUM(CASE WHEN timestamp >= DATE('now', '-30 days') THEN cost_usd ELSE 0 END) AS cost_30d,
                SUM(CASE WHEN timestamp >= DATE('now', '-7 days')  THEN 1 ELSE 0 END)        AS calls_7d,
                SUM(CASE WHEN timestamp >= DATE('now', '-30 days') THEN 1 ELSE 0 END)        AS calls_30d
            FROM l5_token_usage
        """).fetchone()
        cost_7d  = rows["cost_7d"]  or 0.0
        cost_30d = rows["cost_30d"] or 0.0
        calls_7d  = rows["calls_7d"]  or 0
        calls_30d = rows["calls_30d"] or 0
        return {
            "cost_7d":        round(cost_7d, 4),
            "cost_30d":       round(cost_30d, 4),
            "calls_7d":       calls_7d,
            "calls_30d":      calls_30d,
            "daily_rate_7d":  round(cost_7d  / 7,  4),
            "daily_rate_30d": round(cost_30d / 30, 4),
        }
    finally:
        conn.close()


def acknowledge_drift_alert(alert_id: int) -> None:
    conn = get_db()
    try:
        conn.execute("UPDATE drift_alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        conn.commit()
    finally:
        conn.close()


def get_closed_trades_for_drift(limit: int = 200) -> list[dict]:
    """Returns closed demo trades for drift metric computation."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT ticker, sector, signal_score, sentiment_score, claude_confidence,
                   outcome, pnl, amount, timestamp, exited_at
            FROM trades
            WHERE outcome IN ('WIN', 'LOSS')
              AND signal_score IS NOT NULL
            ORDER BY exited_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Regime Bayesian posteriors ────────────────────────────────────────────────

def get_sector_posteriors() -> dict[str, float]:
    """Load persisted Bayesian posteriors for all sectors."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sector, posterior FROM sector_posteriors"
        ).fetchall()
        return {r["sector"]: r["posterior"] for r in rows}
    finally:
        conn.close()


def upsert_sector_posteriors(posteriors: dict[str, float]) -> None:
    """Persist Bayesian posteriors. Called after every daily regime update."""
    from datetime import datetime, timezone
    now  = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.executemany(
            """
            INSERT INTO sector_posteriors (sector, posterior, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sector) DO UPDATE SET
                posterior  = excluded.posterior,
                updated_at = excluded.updated_at
            """,
            [(sector, posterior, now) for sector, posterior in posteriors.items()],
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"upsert_sector_posteriors: {e}")
    finally:
        conn.close()


def get_sector_posteriors_asof(date_str: str, days_back: int) -> dict[str, float]:
    """Load each sector's posterior from the most recent date at or before (date_str - days_back calendar days)."""
    conn = get_db()
    try:
        threshold = conn.execute(
            "SELECT date(?, ?)", (date_str, f"-{days_back} days"),
        ).fetchone()[0]
        prev_date = conn.execute(
            "SELECT MAX(date) FROM sector_posterior_history WHERE date <= ?",
            (threshold,),
        ).fetchone()[0]
        if prev_date is None:
            return {}
        rows = conn.execute(
            "SELECT sector, posterior FROM sector_posterior_history WHERE date = ?",
            (prev_date,),
        ).fetchall()
        return {r["sector"]: r["posterior"] for r in rows}
    finally:
        conn.close()


def get_previous_sector_posteriors(date_str: str) -> dict[str, float]:
    """Load each sector's posterior from the most recent date strictly before date_str."""
    return get_sector_posteriors_asof(date_str, days_back=1)


def insert_sector_posterior_history(date_str: str, posteriors: dict[str, float]) -> None:
    """Append daily posterior snapshot. Idempotent on (date, sector) key."""
    conn = get_db()
    try:
        conn.executemany(
            """
            INSERT OR IGNORE INTO sector_posterior_history (date, sector, posterior)
            VALUES (?, ?, ?)
            """,
            [(date_str, sector, posterior) for sector, posterior in posteriors.items()],
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"insert_sector_posterior_history: {e}")
    finally:
        conn.close()
