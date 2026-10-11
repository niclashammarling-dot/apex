"""Macro pre-event penalty re-test on the intraday-stop engine (2026-10-11).

Live parameters from data/live_config.json (cooloff hours -> days: 24h -> 1, 168h -> 7;
max_hold_days 25 -> time_stop_days). Each variant's result is saved as soon as it finishes,
so a killed run loses one unit. Run from apex/: python3 -u <this> <outdir>
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from backend.backtest.engine import run  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
END = "2026-10-09"
WINDOWS = {"full": "2021-01-01", "post2024": "2024-01-01"}
BASE = dict(
    end_date=END, stop_loss_pct=0.06, take_profit_pct=0.06,
    profit_lock_trigger_pct=0.04, profit_lock_trail_pct=0.01,
    sl_cooldown_days=1, tp_cooldown_days=7, max_positions=9,
    lock1_threshold=0.65, time_stop_days=25, vix_threshold=30,
    etf_negative_penalty=0.15, etf_negative_floor=-1.0, intraday_stops=True,
)
VARIANTS = [("no_filter", dict(macro_hard_block=False, macro_pre_event_penalty=None)),
            ("hard_block", dict(macro_hard_block=True, macro_pre_event_penalty=None))]
VARIANTS += [(f"penalty_{p:.2f}", dict(macro_hard_block=False, macro_pre_event_penalty=p))
             for p in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)]
VARIANTS += [("penalty_0.05_repeat", dict(macro_hard_block=False, macro_pre_event_penalty=0.05))]

for wname, start in WINDOWS.items():
    for vname, kw in VARIANTS:
        if vname.endswith("_repeat") and wname != "full":
            continue
        f = OUT / f"{wname}__{vname}.json"
        if f.exists():
            print(f"skip {f.name} (done)")
            continue
        t0 = time.time()
        r = run(start_date=start, **BASE, **kw)
        r = {k: v for k, v in r.items() if k != "equity_curve"}
        f.write_text(json.dumps(r, default=str))
        pf = r["profit_factor"]
        print(f"{wname:9} {vname:20} PF={pf if pf is None else round(pf,3)} WR={r['win_rate']} "
              f"N={r['total_trades']} CAGR={r['cagr']:.4f} DD={r['max_drawdown']:.4f} "
              f"Sharpe={r['sharpe']:.3f} ({time.time()-t0:.0f}s)", flush=True)
