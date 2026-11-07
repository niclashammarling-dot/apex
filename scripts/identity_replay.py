"""Identity replay for lock labels phase 3 (2026-11-07): same candidates, same inputs, parent against branch.

Vault: raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md § Verification ("the same candidates give the
same rows and the same entries before and after"). The plan's form, three sessions replayed from the stored rows'
inputs, is not possible: the rows do not store what the chain read (regime leaderboard, VIX, Grok's answer, the
option chains, Claude's answer). That is the gap INDEX "APEX live gate stores the inputs it decided on" closes.
So the replay runs the demo runner's per-candidate path on a fixed grid of synthetic candidates, built so every
chain rule both passes and fails at least once, with every external read replaced by the same deterministic stub
in both versions:
  L1 regime leaderboard and VIX; L2 earnings date; L4 Grok, RSS and market signals; L5 relative strength, volume accumulation,
  put/call and unusual calls (the option fetch is never called); L6 Claude.
The path is the runner's own: gate_runner._evaluate (context, chain with the deferred review) → the deferred L6
call → _write_demo (finalize: stored codes and flags), with the row captured where insert_demo_gate_result is called.
Each version runs on its own temp database built by its own init_db().

Positive control: `--mutate` raises the VIX maximum to 40 for that run only, a behaviour change; compared with
an unmutated run it must give differences (AAD passes L1), or the comparison is blind.

Usage (run once per checkout, then compare):
  .venv/bin/python -u scripts/identity_replay.py --root CHECKOUT [--mutate] OUT.json
  .venv/bin/python -u scripts/identity_replay.py --compare PARENT.json BRANCH.json
Vault: the phase 3 merge note of 2026-11-07 (producer block in each OUT.json).
"""
import importlib
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

COLUMN_MAP = {   # parent column → branch column (db.GATE_COLUMN_RENAMES on the branch)
    "lock1_pass": "l3_score_pass", "lock2_pass": "l4_sentiment_pass", "lock_leading_pass": "l5_positioning_pass",
    "lock3_pass": "l6_review_pass", "lock_leading_checks": "l5_positioning_checks",
    "l2_summary": "l4_sentiment_summary", "lock3_sentiment_score": "l4_sentiment_score",
    "lock3_conviction": "l4_sentiment_conviction", "macro_reason": "l1_eligibility_reason",
    "lock3_reasoning": "decision_reason",
}
VOLATILE = {"timestamp", "cycle_started_at"}   # wall-clock stamps, not decisions
TODAY = date.today()

SECTORS = {  # sector → (adjusted_score, allocation, rank); "Utilities" is off the leaderboard
    "Technology": (0.80, 0.30, 1), "Energy": (0.70, 0.25, 2), "Healthcare": (0.60, 0.20, 3),
    "Industrials": (0.40, 0.0, 4),
}


def _cand(i, ticker, sector, score, *, vix=18.0, earn=None, grok=(0.6, "high"), rs=(True, 0.5), va=True,
          pcr=True, calls=False, opt_err=False, claude=(True, 0.8)):
    return dict(i=i, ticker=ticker, sector=sector, score=score, vix=vix, earn=earn, grok=grok, rs=rs, va=va,
                pcr=pcr, calls=calls, opt_err=opt_err, claude=claude)


GRID = [
    _cand(1, "AAA", "Technology", 0.90),                                    # passes every lock
    _cand(2, "AAB", "Utilities", 0.90),                                     # L1 off the leaderboard
    _cand(3, "AAC", "Industrials", 0.90),                                   # L1 no allocation
    _cand(4, "AAD", "Energy", 0.90, vix=35.0),                              # L1 VIX
    _cand(5, "AAE", "Healthcare", 0.90, earn=1),                            # L2 earnings blackout
    _cand(6, "AAF", "Healthcare", 0.90, earn=5),                            # L2 earnings near penalty
    _cand(7, "AAG", "Technology", 0.10),                                    # L3 score below threshold
    _cand(8, "AAH", "Technology", 0.90, grok=(-0.4, "high")),               # L4 sentiment
    _cand(9, "AAI", "Energy", 0.90, rs=(False, 0.2), va=False),             # L5 price group
    _cand(10, "AAJ", "Energy", 0.90, pcr=False, calls=False),               # L5 options group
    _cand(11, "AAK", "Energy", 0.90, opt_err=True),                         # L5 unavailable (fetch error)
    _cand(12, "AAL", "Technology", 0.60, rs=(True, -3.0)),                  # L2 ETF re-test
    _cand(13, "AAM", "Technology", 0.90, claude=(False, 0.8)),              # L6 Claude rejects
    _cand(14, "AAN", "Technology", 0.90, claude=(True, 0.2)),               # L6 confidence below minimum
    _cand(15, "AAO", "Technology", 0.90, claude=None),                      # L6 malformed answer
    _cand(16, "AAP", "Healthcare", 0.70),                                   # near threshold, passes
    _cand(17, "AAQ", "Healthcare", 0.56, earn=5),                           # earnings-near penalty → L3 fails
    _cand(18, "AAR", "Energy", 0.90, pcr=False, calls=True),                # options group passes on calls alone
    _cand(19, "AAS", "Energy", 0.90, rs=(False, 0.3), va=True),             # price group passes on volume alone
]


def _first(*names):
    for n in names:
        if n is None:
            continue
        try:
            return importlib.import_module(n)
        except ModuleNotFoundError:
            continue
    return None


def run(root: Path, out: Path, mutate: bool = False) -> None:
    sys.path.insert(0, str(root))
    import backend.db as db
    db.DB_PATH = Path(tempfile.mkdtemp()) / "replay.db"
    db.init_db()
    gr = importlib.import_module("backend.gate.gate_runner")
    l1 = _first("backend.gate.l1_eligibility", "backend.gate.lock1_eligibility")
    l2 = _first("backend.gate.l2_risk")
    l4 = _first("backend.gate.l4_sentiment", "backend.gate.lock3_sentiment")
    l5 = _first("backend.gate.l5_positioning", "backend.gate.lock4_leading")
    l6 = _first("backend.gate.l6_review", "backend.gate.lock5_claude")
    by_ticker = {c["ticker"]: c for c in GRID}
    cur = {}

    leaderboard = [SimpleNamespace(sector=s, adjusted_score=a, allocation=al, rank=r)
                   for s, (a, al, r) in SECTORS.items()]
    l1._get_regime_result = lambda: SimpleNamespace(leaderboard=leaderboard, leader="Technology", date=str(TODAY))
    l1._get_vix = lambda: cur["c"]["vix"]
    earn_mod = l2 or l1
    earn_mod._get_earnings_date = lambda t: (TODAY + timedelta(days=by_ticker[t]["earn"])
                                             if by_ticker[t]["earn"] is not None else None)
    l4._fetch_rss = lambda *a, **k: []
    l4.fetch_market_signals = lambda t: {"replay": True, "ticker": t}   # Yahoo news/quote fetch (L4 input)
    l4._call_grok = lambda ticker, *a, **k: {
        "score": by_ticker[ticker]["grok"][0], "conviction": by_ticker[ticker]["grok"][1],
        "sentiment": "bullish" if by_ticker[ticker]["grok"][0] > 0 else "bearish",
        "key_themes": ["replay"], "summary": f"replay summary {ticker}"}
    l5._fetch_options_chains = lambda t: (_ for _ in ()).throw(RuntimeError("fetch error: replay")) \
        if by_ticker[t]["opt_err"] else [("2026-12-18", None, None)]
    l5._check_relative_strength = lambda t, s: {
        "pass": by_ticker[t]["rs"][0], "error": False, "etf_return": by_ticker[t]["rs"][1],
        "reason": f"rs replay {t}"}
    l5._check_volume_accumulation = lambda t: {"pass": by_ticker[t]["va"], "error": False, "reason": "va replay"}

    def _pcr(chains, fetch_err=None, *a, **k):
        t = cur["c"]["ticker"]
        if fetch_err:
            return {"pass": False, "error": True, "reason": fetch_err}
        return {"pass": by_ticker[t]["pcr"], "error": False, "reason": "pcr replay"}

    def _calls(chains, fetch_err=None, *a, **k):
        t = cur["c"]["ticker"]
        if fetch_err:
            return {"pass": False, "error": True, "reason": fetch_err}
        return {"pass": by_ticker[t]["calls"], "error": False, "reason": "calls replay"}

    l5._check_put_call_ratio = _pcr
    l5._check_unusual_call_volume = _calls

    def _claude(payload, model, *a, **k):
        c = cur["c"]["claude"]
        if c is None:
            return "not json at all", {"input_tokens": 1, "output_tokens": 1}
        return json.dumps({"decision": "BUY" if c[0] else "HOLD", "confidence": c[1], "position_size_pct": 0.08,
                           "reasoning": f"replay {cur['c']['ticker']}"}), {"input_tokens": 1, "output_tokens": 1}

    l6._call_anthropic = _claude

    wallet_mod = importlib.import_module("backend.wallet")
    captured = []
    gr.insert_demo_gate_result = lambda row: captured.append(dict(row))
    gr.update_signal_gate = lambda *a, **k: None
    run_l6 = getattr(gr, "_run_l6_review_for_result", None) or getattr(gr, "_run_l5_for_result")
    cfg = gr.get_demo_config() if hasattr(gr, "get_demo_config") else importlib.import_module(
        "backend.demo_config").get_demo_config()
    # Set under both names: the parent reads the old key, the branch the derived one (falling back to the old).
    for old_key, new_key, v in (("etf_negative_penalty", "l2_risk_etf_penalty", 0.10),
                                ("etf_negative_floor", "l2_risk_etf_floor", -1.0),
                                ("macro_earnings_blackout_days", "l2_risk_earnings_days", 3),
                                ("macro_earnings_near_days", "l2_risk_earnings_near_days", 7),
                                ("macro_earnings_near_penalty", "l2_risk_earnings_near_penalty", 0.05),
                                ("vix_threshold", "l1_eligibility_vix_max", 30.0),
                                ("lock3_confidence_min", "l6_review_claude_min", 0.6),
                                ("lock2_sentiment_min", "l4_sentiment_grok_min", 0.0)):
        cfg[old_key] = cfg[new_key] = v
    if mutate:
        cfg["vix_threshold"] = cfg["l1_eligibility_vix_max"] = 40.0
    wallet = {"balance": 10000.0, "open_positions": 3, "sector_exposure": {"Technology": 0.1},
              "starting_balance": 10000.0}
    thresholds = {s: 0.55 for s in SECTORS}
    rows = []
    for c in GRID:
        cur["c"] = c
        if hasattr(l4, "_cache"):
            l4._cache.clear()
        signal = {"id": c["i"], "ticker": c["ticker"], "sector": c["sector"], "signal_score": c["score"],
                  "price": 100.0, "timestamp": f"{TODAY}T15:00:00", "bar_date": str(TODAY)}
        result = gr._evaluate(signal, wallet, cfg, sector_thresholds=thresholds)
        pending = result.get("_l5_lock_results") is not None
        trade = None
        # The runner's execution phase with a free slot and no open position (gate_runner.run).
        if pending:
            gr.lock_codes.put(result.setdefault("rule_states", {}), "position_open", gr.lock_codes.PASS)
            gr._overflow_state(result, False)
            run_l6(signal, result, cfg)
        if result["outcome"] == "TRADE_QUEUED":
            gr._overflow_state(result, False)
            trade = wallet_mod.execute_trade(result, signal["price"], dynamic_caps=None)
            result["outcome"] = "TRADE_EXECUTED" if trade else gr.STOPPED
        before = len(captured)
        gr._write_demo(signal, result, f"{TODAY}T15:00:00", [])
        row = captured[before] if len(captured) > before else {}
        rows.append({"ticker": c["ticker"], "pending_l6": pending,
                     "row": {k: v for k, v in row.items() if k not in VOLATILE},
                     "entry": {k: v for k, v in (trade or {}).items() if k not in VOLATILE and k != "id"}})
    def head(path):
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    me = Path(__file__).resolve()
    # commit: where this script lives (the archive's producer); replayed: the checkout whose code ran.
    producer = {"repo": "apex", "commit": head(me.parent), "replayed": head(root), "script": "scripts/identity_replay.py",
                "run": f".venv/bin/python -u {me} --root {root}{' --mutate' if mutate else ''} {out}",
                "why": "raw/notes/2026-10/2026-10-31-apex-lock-labels-phase3-plan.md § Verification",
                "db": "temp schema from the checkout's own init_db()", "mutate": mutate}
    out.write_text(json.dumps({"producer": producer, "root": str(root), "rows": rows}, indent=1, default=str))
    print(f"{len(rows)} candidates replayed from {root}")


def compare(parent: Path, branch: Path) -> int:
    p, b = json.loads(parent.read_text())["rows"], json.loads(branch.read_text())["rows"]
    diffs = []
    for pr, br in zip(p, b):
        assert pr["ticker"] == br["ticker"]
        prow = {COLUMN_MAP.get(k, k): v for k, v in pr["row"].items()}
        brow = br["row"]
        for k in sorted(set(prow) | set(brow)):
            if prow.get(k, "<absent>") != brow.get(k, "<absent>"):
                diffs.append((pr["ticker"], k, prow.get(k, "<absent>"), brow.get(k, "<absent>")))
        if pr.get("entry") != br.get("entry"):
            diffs.append((pr["ticker"], "entry", pr.get("entry"), br.get("entry")))
        if pr["pending_l6"] != br["pending_l6"]:
            diffs.append((pr["ticker"], "pending_l6", pr["pending_l6"], br["pending_l6"]))
    codes = sorted({(r["ticker"], r["row"].get("stop_code") or r["row"].get("gate_decision")) for r in b})
    print("stop codes (branch):", codes)
    print("entries:", sum(1 for r in b if r.get("entry")), "of", len(b))
    for d in diffs:
        print("DIFF", d)
    print(f"{len(p)} candidates, {len(diffs)} differences")
    return 1 if diffs else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "--compare":
        sys.exit(compare(Path(a[1]), Path(a[2])))
    run(Path(a[a.index("--root") + 1]).resolve(), Path(a[-1]), mutate="--mutate" in a)
