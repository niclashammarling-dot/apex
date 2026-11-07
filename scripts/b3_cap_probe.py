"""Read-only probe: does the live position cap bind? Inputs for the b3 cap-rule shape (Saturday 2026-11-07).

Vault scope: raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 6 (reads 1 and 2 before the ruling).
Since live_account_since:
  1. cycles and session time at the cap, and candidates turned away there (L2 RISK overflow refusals);
  2. n and outcome per slot (the position's ordinal in the gate's open_count at execution).

Live has no hard cap: at open_count >= max_positions a candidate needs score >= base x (1 + level x
overflow_quant_increment), level = open_count - max_positions + 1.

How the gate's open_count is rebuilt (each step was forced by a control miss on 2026-11-06):
  - Broker positions come from the FILL activities (net qty per symbol), the population the gate counts
    (`gate_runner_live.py:610`, broker.get_positions()). live_trades lags: its rows and the fills land
    20-40 s after the gate decides (08-13: ISRG, COP, TMO decided 13:49:20-30 UTC, filled 13:50:05-20;
    OXY was refused at 13:49:40 on 5 held + 3 just executed = 8).
  - Inside a cycle the serial phase runs in score order (`gate_runner_live.py:613`), not row-timestamp
    order (09-29 14:53 UTC: KLAC executed before LRCX, whose row is 4 s older). A row's open_count is the
    broker snapshot at the cycle's first row plus the executions in that cycle with a higher score.
    Pre-rotation candidates execute after every regular one, whatever their score (`ad1b134`: "Regular
    (confirmed) candidates always execute before pre-rotation candidates"). They are not modelled:
    live_gate_history does not store the flag, and the logs add them often (430 lines since 2026-10-07), so
    a slot in a cycle where one executed can be off by one. The cap controls below still hold.
  - FILL history starts at the 2026-07-07 paper reset (first fill 17:56 UTC); rows before it are
    unevaluable, reported, never counted as zero.
max_positions: 8, then 9 from 916613c (2026-09-30 04:54 UTC). The file is written at runtime, so git alone
does not prove when; L5's quoted counts agree: "7 of 8 max positions used" 2026-09-29 14:54 UTC, "open_positions
(9) already exceeds max_positions (8)" 18:13 UTC, "8/9" from 2026-09-30 13:53 UTC.

Known-present controls (the run exits 2 if any misses):
  - the FILL rebuild's held set now equals broker.get_positions() now;
  - both rows with overflow_slot = 1 (LRCX 09-29, MSFT 09-30) rebuild to open_count >= max_positions;
  - every evaluable FILTERED_L2_RISK_OVERFLOW row rebuilds to open_count >= max_positions.

Opens data/apex.db with mode=ro; reads Alpaca FILL activities and positions (GET only); writes only OUT.json.
Usage: .venv/bin/python -u scripts/b3_cap_probe.py OUT.json
"""
import bisect
import json
import math
import random
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))           # backend.* imports when run as scripts/b3_cap_probe.py
DB = ROOT / "data" / "apex.db"
SINCE = "2026-07-07"                      # live_config live_account_since (asserted below)
MAX_POS_BEFORE = 8
MAX_POS_CHANGES = [(datetime(2026, 9, 30, 4, 54, 57, tzinfo=timezone.utc), 9)]   # 916613c
ET = ZoneInfo("America/New_York")
WHY = "raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md"   # vault scope this probe answers (producer block, vault DATA-PROVENANCE)
LIVE_SAMPLE = ("live_sample: closed live_trades with timestamp >= live_account_since, "
               "exit_confidence != 'unverified', not annotated")   # backend/db.py LIVE_SAMPLE, copied


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def max_positions(t: datetime) -> int:
    m = MAX_POS_BEFORE
    for when, v in MAX_POS_CHANGES:
        if t >= when:
            m = v
    return m


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2x2 [[a, b], [c, d]] two-sided Fisher exact p (sum of tables no more likely than the observed)."""
    r1, c1, n = a + b, a + c, a + b + c + d
    def p(x):
        return math.comb(c1, x) * math.comb(n - c1, r1 - x) / math.comb(n, r1)
    obs = p(a)
    lo, hi = max(0, r1 - (n - c1)), min(r1, c1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def perm_mean_p(x: list[float], y: list[float], n_perm: int = 20000, seed: int = 7) -> float:
    """Two-sided permutation p for the difference in means (fixed seed, so the run reproduces)."""
    obs = abs(sum(x) / len(x) - sum(y) / len(y))
    pool, k, rng, hits = x + y, len(x), random.Random(seed), 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        if abs(sum(pool[:k]) / k - sum(pool[k:]) / (len(pool) - k)) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def main(out: str) -> int:
    from backend.brokers.alpaca import get_activities, get_positions

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cfg = json.loads((ROOT / "data" / "live_config.json").read_text())
    assert cfg.get("live_account_since") == SINCE, cfg.get("live_account_since")
    assert cfg.get("max_positions") == 9, cfg.get("max_positions")

    # --- broker positions over time, from FILL ------------------------------------------------------
    fills = sorted((ts(f["filled_at"]), f["ticker"], f["qty"] if f["side"] == "buy" else -f["qty"])
                   for f in get_activities("FILL", after="2026-03-01T00:00:00Z"))
    pos, times, counts = defaultdict(float), [], []
    for when, sym, q in fills:
        pos[sym] += q
        times.append(when)
        counts.append(sum(1 for v in pos.values() if v > 1e-6))
    held_now = sorted(k for k, v in pos.items() if v > 1e-6)
    first_fill = times[0] if times else None

    def held_at(t: datetime) -> int:
        i = bisect.bisect_left(times, t)              # fills strictly before t
        return counts[i - 1] if i else 0

    # --- gate rows grouped into cycles ----------------------------------------------------------------
    gh = [dict(r) for r in con.execute(
        "SELECT id, timestamp, ticker, gate_decision, stop_code, signal_score, cycle_started_at, "
        "alpaca_order_id, overflow_slot, l5_positioning_pass FROM live_gate_history WHERE timestamp >= ? "
        "ORDER BY timestamp", (SINCE,))]
    key, prev, cyc_first, by_cycle = None, None, {}, defaultdict(list)
    for r in gh:
        t = ts(r["timestamp"])
        if r["cycle_started_at"]:
            key = "c:" + r["cycle_started_at"]
        elif prev is None or (t - prev).total_seconds() > 120:
            key = "t:" + r["timestamp"]
        r["cycle"], prev = key, t
        cyc_first.setdefault(key, t)
        by_cycle[key].append(r)

    def gate_open(r: dict) -> int | None:
        start = cyc_first[r["cycle"]]
        if first_fill is None or start < first_fill:
            return None
        ahead = sum(1 for o in by_cycle[r["cycle"]] if o["id"] != r["id"] and o["gate_decision"] == "TRADE_EXECUTED"
                    and (o["signal_score"] or 0) > (r["signal_score"] or 0))
        return held_at(start) + ahead

    # --- controls ---------------------------------------------------------------------------------
    held_broker = sorted(p["ticker"] for p in get_positions())
    ctrl = [{"kind": "fills_rebuild_equals_broker_positions_now", "by_fills": held_now, "broker": held_broker,
             "ok": held_now == held_broker}]
    for r in (r for r in gh if r["overflow_slot"] == 1):
        o, m = gate_open(r), max_positions(ts(r["timestamp"]))
        ctrl.append({"kind": "overflow_executed", "ticker": r["ticker"], "at": r["timestamp"], "open": o,
                     "max": m, "ok": o is not None and o >= m})
    refused = [r for r in gh if r["stop_code"] == "FILTERED_L2_RISK_OVERFLOW"]
    for r in refused:
        r["open"], r["max"] = gate_open(r), max_positions(ts(r["timestamp"]))
    evaluable = [r for r in refused if r["open"] is not None]
    ctrl.append({"kind": "overflow_refused_at_cap", "n": len(refused), "evaluable": len(evaluable),
                 "unevaluable_before_first_fill": [(r["ticker"], r["timestamp"]) for r in refused if r["open"] is None],
                 "misses": [(r["ticker"], r["timestamp"], r["open"], r["max"]) for r in evaluable if r["open"] < r["max"]],
                 "ok": bool(evaluable) and all(r["open"] >= r["max"] for r in evaluable)})
    n_exec_ctrl = sum(c["kind"] == "overflow_executed" for c in ctrl)
    ctrl_ok = n_exec_ctrl == 2 and all(c["ok"] for c in ctrl)

    # --- read 2: n and outcome per slot -----------------------------------------------------------
    annotated = {r[0] for r in con.execute(
        "SELECT trade_id FROM trade_annotations WHERE trade_table = 'live_trades' AND tag = 'superseded_posterior'")}
    sample = [dict(t) for t in con.execute("SELECT * FROM live_trades WHERE timestamp >= ?", (SINCE,))
              if t["outcome"] in ("WIN", "LOSS", "EXPIRED") and (t["exit_confidence"] or "confirmed") != "unverified"
              and t["id"] not in annotated]
    gh_by_order = {r["alpaca_order_id"]: r for r in gh if r["alpaca_order_id"]}
    rows, unslotted, per_slot = [], [], defaultdict(list)
    for t in sample:
        g = gh_by_order.get(t["alpaca_order_id"])
        o = gate_open(g) if g else None
        if o is None:
            unslotted.append({"id": t["id"], "ticker": t["ticker"], "entered": t["timestamp"],
                              "why": "no gate row" if not g else "before first fill"})
            continue
        ret = (t["pnl"] or 0.0) / t["notional"] if t["notional"] else 0.0
        row = {"id": t["id"], "ticker": t["ticker"], "entered": t["timestamp"], "slot": o + 1,
               "max": max_positions(ts(g["timestamp"])), "outcome": t["outcome"], "ret": ret, "pnl": t["pnl"]}
        rows.append(row)
        per_slot[row["slot"]].append(row)

    def group(rs):
        return {"n": len(rs), "win": sum(r["outcome"] == "WIN" for r in rs),
                "loss": sum(r["outcome"] == "LOSS" for r in rs),
                "mean_ret_pct": round(100 * sum(r["ret"] for r in rs) / len(rs), 3) if rs else None,
                "pnl": round(sum(r["pnl"] or 0 for r in rs), 2)}

    def compare(a, b):
        if not a or not b:
            return {"win_p_fisher": None, "mean_p_perm": None}
        wa, wb = sum(r["outcome"] == "WIN" for r in a), sum(r["outcome"] == "WIN" for r in b)
        return {"win_p_fisher": round(fisher_two_sided(wa, len(a) - wa, wb, len(b) - wb), 4),
                "mean_p_perm": round(perm_mean_p([r["ret"] for r in a], [r["ret"] for r in b]), 4)}

    lo7, hi8 = [r for r in rows if r["slot"] <= 7], [r for r in rows if r["slot"] >= 8]
    below, top = [r for r in rows if r["slot"] < r["max"]], [r for r in rows if r["slot"] >= r["max"]]
    slot_groups = {"slots_1_7": group(lo7), "slots_8_plus": group(hi8), "test_8plus_vs_1_7": compare(hi8, lo7),
                   "below_last_slot": group(below), "last_slot_or_overflow": group(top),
                   "test_last_or_overflow_vs_below": compare(top, below)}

    # --- read 1: cycles and time at the cap -------------------------------------------------------
    cycles = [ts(r[0]) for r in con.execute(
        "SELECT started_at FROM gate_cycles WHERE job = 'run_live_gate' ORDER BY started_at")]
    at_cap = [c for c in cycles if held_at(c) >= max_positions(c)]
    cyc = {"source": "gate_cycles job=run_live_gate (table exists from 2026-10-05); open = broker at cycle start",
           "n": len(cycles), "first": cycles[0].isoformat() if cycles else None, "at_cap": len(at_cap),
           "at_cap_days": sorted({c.date().isoformat() for c in at_cap}),
           "held_at_cycle_start": dict(sorted(Counter(held_at(c) for c in cycles).items()))}

    start = max(ts(SINCE + "T00:00:00+00:00"), first_fill) if first_fill else ts(SINCE + "T00:00:00+00:00")
    end, step, mins_total, mins_cap, cap_days = datetime.now(timezone.utc), timedelta(minutes=5), 0, 0, set()
    t = start.replace(second=0, microsecond=0)
    while t < end:
        e = t.astimezone(ET)
        if e.weekday() < 5 and (9, 30) <= (e.hour, e.minute) < (16, 0):
            mins_total += 5
            if held_at(t) >= max_positions(t):
                mins_cap += 5
                cap_days.add(e.date().isoformat())
        t += step
    session = {"grid": "5 min, weekdays 09:30-16:00 ET (holidays not removed), broker positions only",
               "from": start.isoformat(), "share_at_cap": round(mins_cap / mins_total, 4) if mins_total else None,
               "days_at_cap_any_time": len(cap_days), "last_day_at_cap": max(cap_days) if cap_days else None,
               "cap_days": sorted(cap_days)}

    refused_out = {"n_rows": len(refused), "ticker_days": len({(r["ticker"], r["timestamp"][:10]) for r in refused}),
                   "cycles": len({r["cycle"] for r in refused}), "days": sorted({r["timestamp"][:10] for r in refused}),
                   "by_month": dict(sorted(Counter(r["timestamp"][:7] for r in refused).items())),
                   "first": refused[0]["timestamp"] if refused else None,
                   "last": refused[-1]["timestamp"] if refused else None,
                   "l5_positioning_pass": dict(Counter(str(r["l5_positioning_pass"]) for r in refused))}

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    run = " ".join([".venv/bin/python", "-u", "scripts/b3_cap_probe.py", *sys.argv[1:]])
    result = {"producer": {"repo": "apex", "commit": commit, "script": "scripts/b3_cap_probe.py", "run": run,
                           "why": WHY,
                           "run_at": datetime.now(timezone.utc).isoformat(), "db": "data/apex.db (mode=ro)",
                           "broker": "Alpaca FILL activities + positions (GET)", "sample": LIVE_SAMPLE,
                           "since": SINCE, "max_positions": "8, then 9 from 2026-09-30T04:54:57Z (916613c)"},
              "controls": {"ok": ctrl_ok, "rows": ctrl},
              "broker": {"fills": len(fills), "first_fill": first_fill.isoformat() if first_fill else None},
              "cycles": cyc, "session_time": session, "refused_at_cap": refused_out,
              "slots": {s: group(v) for s, v in sorted(per_slot.items())}, "unslotted": unslotted,
              "slot_groups": slot_groups, "rows": rows}
    Path(out).write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({k: result[k] for k in ("controls", "broker", "cycles", "session_time", "refused_at_cap",
                                               "slots", "unslotted", "slot_groups")}, indent=1, default=str))
    print("CONTROL", "OK" if ctrl_ok else "FAIL")
    return 0 if ctrl_ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
