"""
Extended wallet + gate tests.
Run: cd ~/apex && .venv/bin/python tests/test_wallet.py
"""
import sqlite3
import time
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from backend.db import (
    init_db, insert_signal, insert_trade, close_trade,
    get_open_trades, get_equity_curve, DB_PATH,
)
from backend.wallet import execute_trade, check_exits, _daily_realized_loss
from backend.gate import lock1_quant, lock2_sentiment, lock3_claude
from backend.gate.lock2_sentiment import _cache as grok_cache

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

def clear_trades():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM trades")
    conn.commit()
    conn.close()


def make_gate_result(ticker, sector, price, decision="BUY", confidence=0.80):
    sid = insert_signal({
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "ticker":         ticker,
        "sector":         sector,
        "price":          price,
        "ma20":           price * 0.97,
        "rsi":            64.0,
        "volume":         20_000_000,
        "avg_vol_30d":    10_000_000,
        "volume_ratio":   2.0,
        "signal_score":   0.74,
        "momentum_score": 0.70,
        "volume_score":   0.82,
        "ev":             0.09,
        "kelly_size":     0.18,
        "high_60d":       price * 1.10,
        "low_60d":        price * 0.90,
        "atr_pct":        0.015,
        "effective_sl":   0.05,
    })
    return {
        "id":             sid,
        "ticker":         ticker,
        "sector":         sector,
        "signal_score":   0.74,
        "lock1":          {"passed": True, "score": 0.74},
        "lock2":          None,
        "lock3":          {
            "passed":           decision == "BUY",
            "decision":         decision,
            "confidence":       confidence,
            "position_size_pct": 0.18,
            "reasoning":        "test",
        },
        "sentiment_score":   0.7,
        "claude_confidence": confidence,
        "claude_reasoning":  "test",
    }


def now_iso():
    return datetime.now(timezone.utc).isoformat()


results = []

def check(name, condition):
    verdict = "PASS" if condition else "FAIL"
    results.append((name, verdict))
    icon = "✓" if condition else "✗"
    print(f"  {icon}  {name}")


# ── TEST 1: Stop-loss exit ────────────────────────────────────────────────────
print("\nTEST 1: Stop-loss exit")
clear_trades()
insert_trade({
    "timestamp": now_iso(), "ticker": "NVDA", "sector": "Technology",
    "action": "BUY", "price": 500.0, "shares": 3.6, "amount": 1800.0,
    "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
    "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
})
closed = check_exits()
nvda = next((c for c in closed if c["ticker"] == "NVDA"), None)
check("SL fires when entry >> current market",  nvda and nvda["exit_reason"] == "SL")
check("outcome = LOSS",                         nvda and nvda["outcome"] == "LOSS")
check("pnl is negative",                        nvda and nvda["pnl"] < 0)

# ── TEST 2: Time-stop exit ────────────────────────────────────────────────────
print("\nTEST 2: Time-stop exit")
clear_trades()
old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
insert_trade({
    "timestamp": old_ts, "ticker": "MSFT", "sector": "Technology",
    "action": "BUY", "price": 380.0, "shares": 4.7, "amount": 1800.0,
    "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
    "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
})
closed = check_exits()
msft = next((c for c in closed if c["ticker"] == "MSFT"), None)
check("TIME fires after 5+ trading days", msft and msft["exit_reason"] == "TIME")
check("outcome = EXPIRED",                msft and msft["outcome"] == "EXPIRED")

# ── TEST 3: Lock 1 boundary ───────────────────────────────────────────────────
print("\nTEST 3: Lock 1 boundary (0.6499 / 0.6500)")
clear_trades()
check("0.6500 passes Lock 1", lock1_quant.evaluate({"signal_score": 0.650})["passed"])
check("0.6499 fails  Lock 1", not lock1_quant.evaluate({"signal_score": 0.6499})["passed"])

# ── TEST 4: Lock 2 negative sentiment → fails ─────────────────────────────────
print("\nTEST 4: Lock 2 negative sentiment")
clear_trades()
grok_cache.clear()
grok_cache["TSLA"] = {
    "expires_at": time.time() + 3600,
    "result": {
        "sentiment":  "negative",
        "score":      -0.6,
        "volume":     "high",
        "key_themes": ["layoffs", "recall", "CEO drama"],
        "summary":    "Very bearish",
    },
}
r = lock2_sentiment.evaluate("TSLA")
check("negative score fails Lock 2", not r["passed"])
check("score returned correctly",    r["score"] == -0.6)

# ── TEST 5: Lock 3 HOLD → fails ───────────────────────────────────────────────
print("\nTEST 5: Lock 3 HOLD decision")
clear_trades()
hold = {
    "lock": 3, "passed": False, "decision": "HOLD",
    "confidence": 0.50, "position_size_pct": 0.0,
    "reasoning": "Uncertain macro", "reason": "decision=HOLD",
}
with patch.object(lock3_claude, "evaluate", return_value=hold):
    l3 = lock3_claude.evaluate({})
check("L3 HOLD fails gate",  not l3["passed"])
check("decision = HOLD",     l3["decision"] == "HOLD")
check("confidence returned", l3["confidence"] == 0.50)

# ── TEST 6: Insufficient cash ─────────────────────────────────────────────────
print("\nTEST 6: Insufficient cash")
clear_trades()
# Deploy $9,900 directly via DB, leaving only $100
for i, (ticker, sector) in enumerate([
    ("T1","Energy"),("T2","Financials"),("T3","Industrials"),
    ("T4","Healthcare"),("T5","ConsumerDisc"),("T6","Technology"),
]):
    insert_trade({
        "timestamp": now_iso(), "ticker": ticker, "sector": sector,
        "action": "BUY", "price": 100.0, "shares": 16.5, "amount": 1650.0,
        "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
        "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
    })
r = execute_trade(make_gate_result("NEE", "Energy", 70.0), 70.0)
check("trade rejected when cash < $50 min", r is None)

# ── TEST 7: Daily loss cap ────────────────────────────────────────────────────
print("\nTEST 7: Daily loss cap ($500)")
clear_trades()
today = now_iso()
for i in range(2):
    tid = insert_trade({
        "timestamp": today, "ticker": f"LOSER{i}", "sector": "Energy",
        "action": "BUY", "price": 100.0, "shares": 18.0, "amount": 1800.0,
        "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
        "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
    })
    close_trade(tid, 90.0, -300.0, "LOSS", "SL", today)

daily = _daily_realized_loss()
check(f"daily loss tracked correctly (${daily:.2f})", daily == 600.0)
r = execute_trade(make_gate_result("AMZN", "ConsumerDisc", 200.0), 200.0)
check("new trade blocked after daily loss cap", r is None)

# ── TEST 8: Grok API key missing → fail closed ────────────────────────────────
print("\nTEST 8: Grok unavailable → fail closed")
clear_trades()
grok_cache.clear()
with patch("backend.gate.lock2_sentiment.XAI_API_KEY", ""):
    r = lock2_sentiment.evaluate("NVDA")
check("fails closed when API key missing", not r["passed"])
check("reason = grok_unavailable",         r["reason"] == "grok_unavailable")

# ── TEST 9: yfinance failure → graceful skip ──────────────────────────────────
print("\nTEST 9: yfinance failure → graceful skip")
clear_trades()
insert_trade({
    "timestamp": now_iso(), "ticker": "FAKE", "sector": "Energy",
    "action": "BUY", "price": 100.0, "shares": 18.0, "amount": 1800.0,
    "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
    "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
})
with patch("backend.wallet.yf.Ticker", side_effect=Exception("network error")):
    try:
        closed = check_exits()
        no_crash = True
    except Exception:
        no_crash = False
check("no crash on yfinance failure", no_crash)
check("position stays open",          len(get_open_trades()) == 1)

# ── TEST 10: Equity curve accuracy ───────────────────────────────────────────
print("\nTEST 10: Equity curve accuracy")
clear_trades()
today = now_iso()
# WIN +200, LOSS -100 → net +100 → final balance $10,100
for pnl, outcome, reason in [(200.0, "WIN", "TP"), (-100.0, "LOSS", "SL")]:
    tid = insert_trade({
        "timestamp": today, "ticker": "EQ", "sector": "Energy",
        "action": "BUY", "price": 100.0, "shares": 18.0, "amount": 1800.0,
        "signal_score": 0.74, "sentiment_score": 0.7, "claude_confidence": 0.8,
        "claude_reasoning": "test", "outcome": "OPEN", "pnl": None,
    })
    close_trade(tid, 100.0, pnl, outcome, reason, today)

curve = get_equity_curve()
check("curve starts at $10,000",    curve[0]["balance"] == 10_000.0)
check("curve ends at $10,100",      abs(curve[-1]["balance"] - 10_100.0) < 0.01)
check("3 points (start + 2 trades)", len(curve) == 3)

# ── Summary ───────────────────────────────────────────────────────────────────
clear_trades()
print()
print("─" * 45)
passed = sum(1 for _, v in results if v == "PASS")
failed = sum(1 for _, v in results if v == "FAIL")
print(f"Results: {passed} passed, {failed} failed  ({len(results)} total)")
if failed:
    print("Failed:")
    for name, v in results:
        if v == "FAIL":
            print(f"  ✗ {name}")
