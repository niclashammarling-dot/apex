"""
Is a backtest result about the book that is actually trading? (2026-09-29)

The weekly sweep and optimizer both call engine_fast.run without live's shape:
no profit-lock ratchet (neither passes profit_lock_*), max_positions searched in
ranges that exclude live's value, and the optimizer's search space includes the
legacy trailing stop live removed on 2026-06-03. On 2026-09-28 every stop-width
conclusion flipped or dissolved once one more live dimension was modelled
(close→intraday 5%→7%; 2→8 positions sign flip; + ratchet within noise). Their
mails and the Friday report presented those results as if they described the
system.

A result is on-shape only if it carries `live_shape_hash` equal to the hash of
the live-config fields it was run under — the same staleness rule as the
optimizer's NOISE_FLOOR objective_hash. Nothing writes that key yet (the live-
shape harness is the fix, filed in apex-moc); until it does, every result is
labelled, and the label clears itself when the harness lands and returns if
live_config.json later changes under an old result.
"""
from __future__ import annotations

import hashlib
import json
from itertools import groupby

# The live-config fields a result's conclusions depend on. A change to any of
# them makes an earlier result describe a different book.
LIVE_SHAPE_KEYS = (
    "take_profit_pct",
    "stop_loss_pct",
    "profit_lock_trigger_pct",
    "profit_lock_trail_pct",
    "max_positions",
)


def live_shape() -> dict:
    from backend.live_config import get_live_config
    cfg = get_live_config()
    return {k: cfg.get(k) for k in LIVE_SHAPE_KEYS}


def live_shape_hash(shape: dict | None = None) -> str:
    shape = live_shape() if shape is None else shape
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:8]


def shape_label(result: dict, kind: str) -> str | None:
    """None when `result` was run at the current live shape; otherwise one line
    saying why it does not describe the live book. `kind` is "sweep" or
    "optimizer" (only the reasons differ)."""
    shape = live_shape()
    h = result.get("live_shape_hash")
    if h == live_shape_hash(shape):
        return None
    if h:
        return (f"STALE — run at live shape {h}; live config is now {live_shape_hash(shape)} "
                f"({_fmt(shape)}). Not a basis for live changes until re-run.")
    reasons = [
        f"no profit-lock ratchet modelled (live: {shape['profit_lock_trigger_pct']}/{shape['profit_lock_trail_pct']})",
        f"max_positions searched, live runs {shape['max_positions']}",
    ]
    if kind == "optimizer":
        reasons.append("search includes the legacy trailing stop live removed 2026-06-03")
    if kind == "sweep":
        reasons.append("compared against the demo config, not live")
    return ("OFF-SHAPE — these results do not describe the live book: "
            + "; ".join(reasons) + ". Not a basis for live changes.")


def _fmt(shape: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in shape.items())


def outcome_signature(trade_log: list[dict]) -> str:
    """Identical trades → identical signature. Two combos with the same signature
    are the same backtest, whatever their parameters say."""
    key = [(t.get("ticker"), t.get("entry_date"), t.get("exit_date"),
            t.get("exit_reason"), round(float(t.get("pnl") or 0.0), 2)) for t in trade_log]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]


def inert_axes(rows: list[dict], axes: tuple[str, ...], sig_key: str = "outcome") -> list[str]:
    """Axes whose value never changed the outcome: within every group of rows that
    agree on all other axes, every row has the same signature. An axis with a
    single grid value is not reported (it was never varied)."""
    inert = []
    for axis in axes:
        if len({repr(r[axis]) for r in rows}) < 2:
            continue
        others = [a for a in axes if a != axis]
        keyf = lambda r, others=others: tuple(repr(r[a]) for a in others)
        # Only groups where the axis actually varied are evidence; combos dropped
        # below MIN_TRADES can leave singletons, which compare nothing.
        groups = [g for g in (list(g) for _, g in groupby(sorted(rows, key=keyf), key=keyf))
                  if len({repr(r[axis]) for r in g}) > 1]
        if groups and all(len({r[sig_key] for r in g}) == 1 for g in groups):
            inert.append(axis)
    return inert


def collapse_by_outcome(rows: list[dict], axes: tuple[str, ...], limit: int = 3,
                        sig_key: str = "outcome") -> list[dict]:
    """Ranked rows → one entry per distinct outcome, in rank order, up to `limit`.

    Each entry is {"row": best-ranked row of that outcome, "combos": how many
    rows produced it, "varied": {axis: values} for the axes that differed among
    them}. The one renderer for the sweep mail and the Friday report (2026-10-07):
    the 09-29 fix printed every axis in the mail only, and the 10-05 report still
    showed one result three times (VIX off/30/35, outcome 9acf142fc075). Rows
    without a signature (files before 09-29) are each their own outcome.
    """
    groups: dict[str, list[dict]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(r.get(sig_key) or f"_row{i}", []).append(r)
    out = []
    for g in groups.values():  # insertion order = rank of each outcome's best row
        varied = {}
        for a in axes:
            vals = {repr(r.get(a)): r.get(a) for r in g}
            if len(vals) > 1:
                varied[a] = sorted(vals.values(), key=lambda v: (v is not None, v))
        out.append({"row": g[0], "combos": len(g), "varied": varied})
        if len(out) == limit:
            break
    return out


def describe_varied(entry: dict) -> str:
    """'' for a single combo; else e.g. 'same trades for 6 combos: vix_threshold off/30/35, use_leading_rs off/on'."""
    if entry["combos"] < 2:
        return ""
    def fmt(v):
        return "off" if v is None or v is False else "on" if v is True else str(v)
    axes = ", ".join(f"{a} {'/'.join(fmt(v) for v in vals)}" for a, vals in entry["varied"].items())
    return f"same trades for {entry['combos']} combos: {axes}"


# Daily loss cap as a fraction of the starting balance: live $500 / $10,000 and demo
# $100 / $2,000 are both 5 % (Niclas 2026-10-17). Equity-based is next Saturday's question.
DAILY_LOSS_CAP_PCT = 0.05


def live_cooloff_days() -> tuple[int, int]:
    """(sl_cooldown_days, tp_cooldown_days) for both engines, from live config
    (2026-10-17, PRODUCTION_GAP row 23). Live blocks re-entry by hours
    (`exit_cooloff_hours` after SL/loss/expiry, `tp_cooloff_hours` after a TP/TSL
    win); the engines block by calendar day, so hours round UP to whole days
    (24 → 1, 168 → 7). Until this, both engines defaulted to 5 / 0 — every
    optimizer and walk-forward run since June used those."""
    import math
    from backend.live_config import get_live_config
    cfg = get_live_config()
    return (math.ceil(cfg["exit_cooloff_hours"] / 24), math.ceil(cfg["tp_cooloff_hours"] / 24))


def live_macro_event_blackout_days() -> int:
    """The CPI/NFP pre-event window in days for both engines, from live config, as live and demo read it
    (l1_eligibility: cfg `macro_event_blackout_days`). Until 2026-10-28 the engines used
    macro_status's own default, equal to live's value by coincidence, not by reference (Niclas
    2026-10-28, P3: one calendar for every book: demo, live and the shadow book)."""
    from backend.live_config import get_live_config
    return int(get_live_config()["macro_event_blackout_days"])


def reentry_cooloff_days(exit_reason: str, outcome: str, sl_days: int, tp_days: int) -> int:
    """Days a ticker is blocked from re-entry after this exit, as live picks the window
    (`db.get_recently_exited_live_tickers`, 2026-10-18, PRODUCTION_GAP row 23): only a
    TP or TSL *win* gets `tp_cooloff_hours`; every other exit (SL, a TSL loss, TIME /
    EXPIRED, REGIME) gets `exit_cooloff_hours`. Until this, both engines set no cooloff
    after a TIME exit, and gave a TSL win the SL window when tp_days was 0.
    END_OF_BACKTEST is not an exit live has: 0."""
    if exit_reason == "END_OF_BACKTEST":
        return 0
    return tp_days if (exit_reason in ("TP", "TSL") and outcome == "WIN") else sl_days


def reentry_cooloff_days_before_row23(exit_reason: str, outcome: str, sl_days: int, tp_days: int) -> int:
    """The window both engines set before 2026-10-18 (6f3921d), kept only so the battery can
    revert row 23 for flip attribution: a TP or TSL win gets tp_days when that is > 0; any
    other SL or TSL exit, and a TSL win when tp_days is 0, gets sl_days; TIME gets none."""
    if (exit_reason == "TP" or (exit_reason == "TSL" and outcome == "WIN")) and tp_days > 0:
        return tp_days
    return sl_days if exit_reason in ("SL", "TSL") else 0


def etf_return_as_live(closes, today) -> float | None:
    """The sector ETF return live's Lock 4 reads (2026-10-18, PRODUCTION_GAP row 11):
    `l5_positioning` downloads `period="8d"` (8 daily rows, measured 10-18) and takes
    last / first - 1, so its "5d" return spans 7 trading-day intervals; fewer than 5
    rows is an error there (no penalty). `closes` is the ETF's Close series; the
    engine evaluates at today's close where live reads today's bar in progress.
    engine.py used 5 intervals until this."""
    import pandas as pd
    s = closes.dropna()
    s = s[s.index <= pd.Timestamp(today)].tail(8)
    if len(s) < 5:
        return None
    return float(s.iloc[-1] / s.iloc[0]) - 1


def etf_penalty_fails(score: float, etf_ret: float | None, penalty: float | None,
                      floor_pct: float, threshold: float) -> bool:
    """Live's ETF negative penalty (`chain.py:156-187`): when the sector ETF return,
    in percent rounded to 2 decimals as Lock 4 reports it, is below `floor_pct`
    (live -1.0), the score times (1 - penalty) must still clear the sector threshold
    or the ticker is filtered. It gates only: live's final_score is Lock 4's or
    Lock 5's, so the penalised score is not carried on. None or 0 is off (live:
    `if etf_negative_penalty > 0`)."""
    if not penalty or etf_ret is None or round(etf_ret * 100, 2) >= floor_pct:
        return False
    return score * (1.0 - penalty) < threshold


def overflow_admits(score: float, open_count: int, max_positions: int,
                    base_threshold: float, increment: float) -> bool:
    """Live's portfolio roof (2026-10-18, PRODUCTION_GAP row 14; `gate_runner_live.py`
    566-627, `config.py` 115-118 "No hard cap"): below `max_positions` every candidate
    is admitted; at or above it, slot n over the roof needs the raw signal score to
    clear base_threshold x (1 + n x increment), rounded to 4 places as live does. A
    rejected candidate does not stop the cycle; the next one is tried. increment
    math.inf is the engines' hard stop at max_positions (before 2026-10-18)."""
    if open_count < max_positions:
        return True
    level = open_count - max_positions + 1
    return score >= round(base_threshold * (1 + level * increment), 4)


def live_overflow_increment() -> float:
    """`overflow_quant_increment` from live config (live 0.05)."""
    from backend.live_config import get_live_config
    return float(get_live_config()["overflow_quant_increment"])
