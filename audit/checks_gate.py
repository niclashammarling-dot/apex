"""
Gate-domain mechanical checks — CHECKs 24, 25, 26, 28, 29, 38.

Covers: chain-runner wiring integrity, gate_decision string parity,
L1/L2 threshold-source parity, EXCLUDED_SECTORS wiring, live sector
exposure cap wiring, and live entry absence-of-activity.
"""
import re
import sqlite3
from datetime import date

from audit._audit_core import REPO, flag


# ── CHECK 24 — Chain-runner wiring integrity ──────────────────────────────────

def check24():
    """
    Verify that:
    1. chain.py defines evaluate_chain
    2. gate_runner.py imports evaluate_chain from backend.gate.chain
    3. gate_runner_live.py imports evaluate_chain (directly or via gate_runner)
    4. chain.py calls all five lock evaluate functions

    Prevented by: 2026-04-18 incident: chain.py existed for a week with zero
    callers — the old lock modules were silently still live.
    """
    chain    = REPO / "backend/gate/chain.py"
    runner   = REPO / "backend/gate/gate_runner.py"
    runner_l = REPO / "backend/gate/gate_runner_live.py"

    for path in [chain, runner, runner_l]:
        if not path.exists():
            flag(24, "Chain-runner wiring", "CRITICAL",
                 str(path.relative_to(REPO)), "file missing")
            return

    chain_text    = chain.read_text()
    runner_text   = runner.read_text()
    runner_l_text = runner_l.read_text()

    if "def evaluate_chain(" not in chain_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/chain.py", "evaluate_chain not defined in chain.py")

    if "evaluate_chain" not in runner_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/gate_runner.py",
             "evaluate_chain not imported/called — runner is not wired to chain.py")

    if "evaluate_chain" not in runner_l_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "evaluate_chain not imported/called — live runner is not wired to chain.py")

    for lock_fn in ["lock1_evaluate", "lock2_evaluate", "lock3_evaluate",
                    "lock4_evaluate", "lock5_evaluate"]:
        if lock_fn not in chain_text:
            flag(24, "Chain-runner wiring", "CRITICAL",
                 "backend/gate/chain.py",
                 f"{lock_fn} not called in chain.py — lock dropped from chain")

    old_modules = ["lock1_quant", "lock_macro", "lock2_sentiment",
                   "lock_leading", "lock3_claude"]
    for mod in old_modules:
        for label, text in [("gate_runner.py", runner_text),
                             ("gate_runner_live.py", runner_l_text)]:
            if f"import {mod}" in text or f"from backend.gate.{mod}" in text:
                flag(24, "Chain-runner wiring", "CRITICAL",
                     f"backend/gate/{label}",
                     f"retired module '{mod}' still imported — runner bypasses chain.py")


# ── CHECK 25 — gate_decision string parity ───────────────────────────────────

def check25():
    """
    Verify that every gate_decision string emitted by _OUTCOMES in gate_runner.py
    is handled by all consumers:
      - getLockStates in GateFeed.jsx and LiveGateFeed.jsx
      - OUTCOME_LABELS/decisionBadge in GateFeed.jsx and LiveGateFeed.jsx
      - funnel SQL and dict lookups in db.py

    Prevented by: FILTERED_ELIGIBILITY introduced in _OUTCOMES but all consumers
    still checked for old FILTERED_MACRO string.
    """
    runner         = REPO / "backend/gate/gate_runner.py"
    gate_feed      = REPO / "frontend/src/components/GateFeed.jsx"
    live_gate_feed = REPO / "frontend/src/components/LiveGateFeed.jsx"
    db_file        = REPO / "backend/db.py"

    if not runner.exists():
        return

    runner_text = runner.read_text()
    outcomes    = re.findall(r'\d+:\s*"(FILTERED_[A-Z_]+)"', runner_text)
    if not outcomes:
        flag(25, "gate_decision string parity", "WARNING",
             "backend/gate/gate_runner.py:_OUTCOMES",
             "_OUTCOMES dict not found or no FILTERED_* strings — pattern changed")
        return

    for outcome in outcomes:
        for label, path in [("GateFeed.jsx", gate_feed), ("LiveGateFeed.jsx", live_gate_feed)]:
            if not path.exists():
                continue
            if outcome not in path.read_text():
                flag(25, "gate_decision string parity", "CRITICAL",
                     str(path.relative_to(REPO)),
                     f'"{outcome}" emitted by _OUTCOMES but not handled in {label} '
                     f'(getLockStates or badge label) — unrecognized decision shows all-green locks')

        if db_file.exists() and outcome not in db_file.read_text():
            flag(25, "gate_decision string parity", "WARNING",
                 "backend/db.py",
                 f'"{outcome}" emitted by _OUTCOMES but not referenced in db.py '
                 f'— funnel counts for this outcome will silently be 0')

    # Sub-check B: ticker signal strings
    regime_py = REPO / "backend/sector_regime.py"
    if not regime_py.exists():
        return
    regime_text    = regime_py.read_text()
    ticker_signals = set(re.findall(r'(?<!vel_)signal\s*=\s*"([a-z]+)"', regime_text))

    signal_consumers = [
        REPO / "frontend/src/components/SectorGrid.jsx",
        REPO / "frontend/src/components/SectorRegime.jsx",
        REPO / "frontend/src/components/Watchlist.jsx",
        REPO / "frontend/src/components/RotationForecast.jsx",
    ]
    for sig in sorted(ticker_signals):
        for comp_path in signal_consumers:
            if not comp_path.exists():
                continue
            comp_text = comp_path.read_text()
            if "breakout:" not in comp_text and "rising:" not in comp_text:
                continue
            if f'{sig}:' not in comp_text:
                flag(25, "gate_decision string parity", "WARNING",
                     str(comp_path.relative_to(REPO)),
                     f'ticker signal "{sig}" emitted by sector_regime.py but missing from '
                     f'{comp_path.name} signal map — renders as fallback/blank')


# ── CHECK 26 — L1/L2 threshold-source parity ─────────────────────────────────

def check26():
    """
    Verify that both L1 (gate runners) and L2 (lock2_quant) read sector thresholds
    from the ticker_thresholds table via get_ticker_thresholds(), not from a static
    config value.

    Prevented by: April 2026 incident where L2 used config.SECTORS['lock1_threshold']
    (static 0.70) while L1 already used calibrated per-sector values (0.54–0.62),
    causing 100% FILTERED_L1 rate across all sectors for an entire week.
    """
    runner      = REPO / "backend/gate/gate_runner.py"
    runner_live = REPO / "backend/gate/gate_runner_live.py"
    lock2       = REPO / "backend/gate/lock2_quant.py"

    for label, path in [("gate_runner.py", runner), ("gate_runner_live.py", runner_live)]:
        if not path.exists():
            continue
        text = path.read_text()
        if "get_ticker_thresholds" not in text:
            flag(26, "L1/L2 threshold-source parity", "CRITICAL",
                 f"backend/gate/{label}",
                 f"{label} does not call get_ticker_thresholds() — L1 candidates will be "
                 f"filtered against the static config fallback, not calibrated per-sector values")
        if "sector_thresholds" not in text:
            flag(26, "L1/L2 threshold-source parity", "CRITICAL",
                 f"backend/gate/{label}",
                 f"{label} does not pass sector_thresholds to get_lock1_candidates() — "
                 f"per-sector calibration is fetched but silently unused")

    if lock2.exists() and "get_ticker_thresholds" not in lock2.read_text():
        flag(26, "L1/L2 threshold-source parity", "CRITICAL",
             "backend/gate/lock2_quant.py:_sector_threshold",
             "_sector_threshold() does not call get_ticker_thresholds() — L2 threshold "
             "source has diverged from L1; likely reverted to static config value")


# ── CHECK 28 — EXCLUDED_SECTORS gate wiring ──────────────────────────────────

def check28():
    """
    Verify EXCLUDED_SECTORS is defined in config.py and correctly wired at L1.

    Three structural assertions:
      1. config.py defines EXCLUDED_SECTORS with the three sweep-validated sectors
         (Financials, Utilities, ConsumerStaples).
      2. lock1_eligibility.py imports EXCLUDED_SECTORS and checks it before the
         regime call — excluded sectors must fail L1 before any market-state logic.
      3. lock2_quant.py imports SECTOR_THRESHOLD_FLOORS and applies it in
         _sector_threshold — sweep-validated floors survive recalibration.

    Prevented by: 2026-05-07 sector exclusion implementation. Excluded sectors
    destroy capital (PF 0.46–0.89 across 2021-2026).
    """
    REQUIRED_EXCLUDED = {"Financials", "Utilities", "ConsumerStaples"}

    config_path = REPO / "backend/config.py"
    l1_path     = REPO / "backend/gate/lock1_eligibility.py"
    l2_path     = REPO / "backend/gate/lock2_quant.py"

    config_text = config_path.read_text()
    if "EXCLUDED_SECTORS" not in config_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/config.py", "EXCLUDED_SECTORS not defined in config.py")
        return
    for sector in REQUIRED_EXCLUDED:
        if f'"{sector}"' not in config_text:
            flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
                 "backend/config.py",
                 f'"{sector}" missing from EXCLUDED_SECTORS — sweep-validated exclusion dropped')

    l1_text = l1_path.read_text()
    if "EXCLUDED_SECTORS" not in l1_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock1_eligibility.py",
             "EXCLUDED_SECTORS not imported — excluded sectors bypass L1 entirely")
        return
    excl_pos   = l1_text.find("EXCLUDED_SECTORS")
    regime_pos = l1_text.find("_get_regime_result()")
    if excl_pos > regime_pos:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock1_eligibility.py",
             "EXCLUDED_SECTORS check appears after _get_regime_result() — must fire first")

    l2_text = l2_path.read_text()
    if "SECTOR_THRESHOLD_FLOORS" not in l2_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock2_quant.py",
             "SECTOR_THRESHOLD_FLOORS not imported — ConsumerDisc floor at 0.75 not enforced")
    elif "max(base, floor)" not in l2_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock2_quant.py",
             "SECTOR_THRESHOLD_FLOORS imported but floor not applied in _sector_threshold")


# ── CHECK 29 — Live sector exposure cap wiring ───────────────────────────────

def check29():
    """
    Verify live sector exposure cap is computed and enforced in gate_runner_live.py.

    Four structural assertions:
      1. SECTORS is imported from backend.config.
      2. sector_exposure_live is computed from open Alpaca positions (not the old {} stub).
      3. dynamic_caps_live is computed via compute_dynamic_caps.
      4. sector_exposure_live[sector] = projected appears in BOTH the normal and overflow
         execution branches — cap enforcement must be symmetric across all entry paths.

    Prevented by: 2026-05-07 incident analysis. Live runner had no sector concentration
    check while demo enforced dynamic_caps at every execute_trade call.
    """
    path = REPO / "backend/gate/gate_runner_live.py"
    text = path.read_text()

    if "from backend.config import" not in text or "SECTORS" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "SECTORS not imported — ticker→sector map cannot be built")
        return

    if "sector_exposure_live" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "sector_exposure_live not present — sector cap check is missing entirely")
        return
    if '"sector_exposure":  {}' in text or '"sector_exposure": {}' in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "sector_exposure still set to {} stub — real computation not wired")

    if "dynamic_caps_live" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "dynamic_caps_live not computed — cap values will fall back to flat config only")

    update_count = text.count("sector_exposure_live[sector] = projected")
    if update_count < 2:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             f"sector_exposure_live update appears {update_count}x — must be in both normal and overflow branches")


# ── CHECK 38 — Live entry absence-of-activity ─────────────────────────────────

def check38():
    """
    Live entry absence-of-activity — WARNING diagnostic for extended gate silence.

    When the live gate evaluates candidates (≥ M gate assessments/day) but no
    TRADE_EXECUTED events fire for N consecutive active trading days, surface a
    diagnostic with filter breakdown.

    Three confirmed instances of this failure class: daily_loss_cap silent halt,
    insider_cluster dead sub-check, promote-exclusion near-miss.

    N=5: one trading week of silence before surfacing.
    M=5: minimum FILTERED_* + TRADE_EXECUTED assessments per day to confirm gate ran.
    """
    N = 5
    M = 5

    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)

        last_entry_row = conn.execute(
            "SELECT DATE(MAX(timestamp)) FROM live_gate_history "
            "WHERE gate_decision = 'TRADE_EXECUTED'"
        ).fetchone()
        last_entry_date = last_entry_row[0] if last_entry_row and last_entry_row[0] else None

        since_clause = f"AND DATE(timestamp) > '{last_entry_date}'" if last_entry_date else ""

        daily_rows = conn.execute(f"""
            SELECT DATE(timestamp) as day,
                   SUM(CASE WHEN gate_decision = 'TRADE_EXECUTED' THEN 1 ELSE 0 END) as entries,
                   COUNT(*) as assessments
            FROM live_gate_history
            WHERE gate_decision NOT LIKE 'SKIPPED_%'
            {since_clause}
            GROUP BY day
            ORDER BY day
        """).fetchall()

        conn.close()
    except Exception as e:
        flag(38, "Live entry absence-of-activity", "WARNING",
             "data/apex.db:live_gate_history",
             f"could not query live_gate_history: {e}")
        return

    if not daily_rows:
        return

    active_silent = [row for row in daily_rows if row[2] >= M and row[1] == 0]
    consecutive   = len(active_silent)

    if consecutive < N:
        return

    try:
        conn = sqlite3.connect(db)
        filter_rows = conn.execute(f"""
            SELECT gate_decision, COUNT(*) as cnt
            FROM live_gate_history
            WHERE gate_decision LIKE 'FILTERED_%'
            {since_clause}
            GROUP BY gate_decision
            ORDER BY cnt DESC
            LIMIT 3
        """).fetchall()
        conn.close()
    except Exception:
        filter_rows = []

    total_filtered  = sum(r[1] for r in filter_rows)
    breakdown_parts = []
    for decision, cnt in filter_rows[:2]:
        pct = (cnt / total_filtered * 100) if total_filtered else 0
        breakdown_parts.append(f"{decision} {pct:.0f}%")
    breakdown = ", ".join(breakdown_parts) if breakdown_parts else "no filter data"

    avg_daily  = sum(r[2] for r in active_silent) / consecutive
    entry_desc = f"last entry {last_entry_date}" if last_entry_date else "no entries ever recorded"

    flag(38, "Live entry absence-of-activity", "WARNING",
         "data/apex.db:live_gate_history",
         f"{consecutive} active trading days with no TRADE_EXECUTED ({entry_desc}); "
         f"avg {avg_daily:.0f} assessments/day; dominant filters: {breakdown}")


# ── CHECK 49 — Gate funnel endpoint scope integrity ───────────────────────────

def check49():
    """
    Gate funnel endpoint scope integrity.

    The /gate/history and /live/gate/history endpoints return a funnel dict and
    a rows list. They have different intended scopes:
      - funnel: all-time (no since filter)
      - rows: today-only (since=market_open)

    This check verifies that the funnel is NOT receiving a since= argument in
    the router calls — i.e., that the all-time scope has not been accidentally
    narrowed again. Reads the router source directly.

    Motivated by 2026-06-03 regression where commit 5f5925d accidentally added
    since=market_open to both funnel calls when only the rows call was intended
    to change.
    """
    import re

    signals_router = REPO / "backend/routers/signals_router.py"
    live_router    = REPO / "backend/routers/live_router.py"

    for path, fn_name in [
        (signals_router, "get_demo_gate_funnel_counts"),
        (live_router,    "get_live_gate_funnel_counts"),
    ]:
        if not path.exists():
            flag(49, "Gate funnel endpoint scope integrity", "WARNING",
                 str(path), f"{path.name}: file not found")
            continue

        src = path.read_text()
        # Find the funnel call line(s)
        for line in src.splitlines():
            if fn_name in line and "def " not in line:
                if "since=" in line:
                    flag(49, "Gate funnel endpoint scope integrity", "CRITICAL",
                         f"{path.name}:—",
                         f"{fn_name}() called with since= argument — funnel scope "
                         f"accidentally narrowed to a time window; must be all-time. "
                         f"Line: {line.strip()}")
                    return

    # No flag = pass (convention: absence of a flag is the pass signal;
    # `ok()` does not exist in _audit_core — calling it here crashed this
    # check on every passing run since it was added 2026-06-03)


# ── CHECK 51 — Lock 5 Bayesian field exclusion parity ────────────────────────

def check51():
    """
    Lock 5 Bayesian field exclusion parity.

    `build_base_context` in chain.py injects `regime_bayes_*` fields into the
    Lock 5 context dict. The SYSTEM_PROMPT in lock5_claude.py explicitly scopes
    these out of the model's sizing decision ("Do NOT factor regime_bayes_allocation,
    regime_bayes_posterior, or any Bayesian field into your size").

    The instruction is correct but the exclusion is named-field, not structural:
    any new `regime_bayes_*` field added to the context dict without a matching
    update to the prompt text creates silent permission — the model sees the
    field and, absent explicit scope-out, may use it (prompt-silence-as-permission).

    This check extracts every `ctx["regime_bayes_*"]` assignment in chain.py and
    verifies each field name appears verbatim in SYSTEM_PROMPT. Flags WARNING for
    any field present in context but absent from the exclusion text.

    Filed 2026-06-05 as a byproduct of token-efficiency qualification gate work
    (raw/notes/2026-06/2026-06-05-lock5-bayesian-exclusion-list-maintenance.md).
    """
    chain_path = REPO / "backend/gate/chain.py"
    prompt_path = REPO / "backend/gate/lock5_claude.py"

    if not chain_path.exists() or not prompt_path.exists():
        flag(51, "Lock 5 Bayesian field exclusion parity", "WARNING",
             "backend/gate/chain.py,backend/gate/lock5_claude.py",
             "source file(s) not found — cannot verify exclusion parity")
        return

    chain_src = chain_path.read_text()
    prompt_src = prompt_path.read_text()

    fields = sorted(set(re.findall(r'ctx\["(regime_bayes_\w+)"\]', chain_src)))
    if not fields:
        flag(51, "Lock 5 Bayesian field exclusion parity", "WARNING",
             "backend/gate/chain.py",
             "no regime_bayes_* fields found in build_base_context — "
             "check may be stale relative to context structure")
        return

    missing = [f for f in fields if f not in prompt_src]
    if missing:
        flag(51, "Lock 5 Bayesian field exclusion parity", "WARNING",
             "backend/gate/lock5_claude.py:SYSTEM_PROMPT",
             f"regime_bayes_* field(s) {missing} present in Lock 5 context "
             f"(build_base_context) but not named in SYSTEM_PROMPT exclusion "
             f"text — silent permission risk; update the prompt's Do NOT list")
    # No flag = pass (convention: absence of a flag is the pass signal)


# ── CHECK 52 — Anthropic credit runway ───────────────────────────────────────

def check52():
    """
    Anthropic credit runway check.

    Lock 5 uses claude-sonnet-4-6 as its sole LLM with no fallback (post-MSFT
    postmortem). Credit exhaustion causes L5 to fail-closed silently — the gate
    returns HOLD with no notification, indistinguishable from a normal L5 skip.

    This check queries l5_token_usage for the 7-day measured burn rate, computes
    projected runway against L5_ANTHROPIC_CREDIT_USD, and flags WARNING if runway
    drops below 14 days. WARNING (not hard-fail): a blocked audit would be a worse
    failure mode than the silent exhaustion it is monitoring for.

    Prerequisite: l5_token_usage rows accumulate as L5 calls are made. The check
    is a no-op (skips without flagging) if the table has fewer than 7 days of data,
    since the burn rate estimate would be unreliable. Update L5_ANTHROPIC_CREDIT_USD
    in config.py after each top-up.
    """
    from backend.config import L5_ANTHROPIC_CREDIT_USD
    from backend.db import get_l5_spend_summary

    RUNWAY_WARNING_DAYS = 14

    if L5_ANTHROPIC_CREDIT_USD <= 0:
        flag(52, "Anthropic credit runway", "WARNING",
             "backend/config.py:L5_ANTHROPIC_CREDIT_USD",
             "L5_ANTHROPIC_CREDIT_USD not set — update config.py with current "
             "Anthropic prepaid balance to enable runway monitoring")
        return

    summary = get_l5_spend_summary()

    if summary["calls_7d"] == 0:
        return  # no data yet — skip silently

    daily_rate = summary["daily_rate_7d"]
    if daily_rate <= 0:
        return

    runway_days = L5_ANTHROPIC_CREDIT_USD / daily_rate
    if runway_days < RUNWAY_WARNING_DAYS:
        flag(52, "Anthropic credit runway", "WARNING",
             "backend/config.py:L5_ANTHROPIC_CREDIT_USD",
             f"L5 credit runway {runway_days:.1f} days at ${daily_rate:.4f}/day "
             f"(7d measured rate, balance ${L5_ANTHROPIC_CREDIT_USD:.2f}) — "
             f"below {RUNWAY_WARNING_DAYS}-day threshold; top up Anthropic credits")


def check53() -> None:
    """CHECK 53 — Earnings near-term tier constants present in config and JSON configs."""
    import json
    from pathlib import Path
    import backend.config as cfg_module

    errors = []

    # config.py
    if not hasattr(cfg_module, "MACRO_EARNINGS_NEAR_DAYS"):
        errors.append("MACRO_EARNINGS_NEAR_DAYS missing from config.py")
    if not hasattr(cfg_module, "MACRO_EARNINGS_NEAR_PENALTY"):
        errors.append("MACRO_EARNINGS_NEAR_PENALTY missing from config.py")

    # JSON configs
    root = Path(__file__).parent.parent / "data"
    for fname in ("demo_config.json", "live_config.json"):
        path = root / fname
        if not path.exists():
            errors.append(f"{fname} not found")
            continue
        data = json.loads(path.read_text())
        for key in ("macro_earnings_near_days", "macro_earnings_near_penalty"):
            if key not in data:
                errors.append(f"{key} missing from {fname}")

    if errors:
        raise AssertionError("CHECK 53 FAIL — earnings near-term config missing:\n" + "\n".join(f"  - {e}" for e in errors))


# ── CHECK 54 — Gate funnel label/lock semantic parity ─────────────────────────

# Canonical exit_lock semantics, from _chain_to_gate_result's docstring in
# gate_runner.py (DB column mapping comment: "semantics shifted").
_CANONICAL_LOCK_KEYWORDS = {
    1: "Eligibility",
    2: "Quant",
    3: "Sentiment",
    4: "Leading",
    5: "Claude",
}


def check54() -> None:
    """
    CHECK 54 — gate funnel label/lock semantic parity.

    _OUTCOMES in gate_runner.py maps exit_lock N to a FILTERED_* string under a
    legacy numbering ("semantics shifted" per _chain_to_gate_result's docstring:
    lock1_pass=Quant, lock2_pass=Sentiment, lock3_pass=Claude). adaptFunnel in
    App.jsx then maps those same FILTERED_* strings to display labels. If a
    label's keyword doesn't match the lock that actually produced the string,
    a rejection from one lock is displayed under another lock's label — e.g.
    Quant rejections shown as "L1 Score" and Sentiment rejections shown as
    "L2 Quant", with no bucket for the lock whose keyword was displaced.

    Prevented by: 2026-06-11 finding — funnel showed L4 (Leading fails) > L2
    (labelled Quant fails) with no L3 row, traced to this label/lock drift.
    """
    runner   = REPO / "backend/gate/gate_runner.py"
    app_jsx  = REPO / "frontend/src/App.jsx"
    if not runner.exists() or not app_jsx.exists():
        return

    runner_text = runner.read_text()
    outcomes = dict(re.findall(r'(\d+):\s*"(FILTERED_[A-Z0-9_]+)"', runner_text))
    if not outcomes:
        flag(54, "gate funnel label/lock semantic parity", "WARNING",
             "backend/gate/gate_runner.py:_OUTCOMES",
             "_OUTCOMES dict not found or no FILTERED_* strings — pattern changed")
        return

    app_text = app_jsx.read_text()
    rows = re.findall(r'stage:\s*"(FILTERED_[A-Z0-9_]+)",\s*label:\s*"([^"]+)"', app_text)
    label_by_stage = dict(rows)
    if not label_by_stage:
        flag(54, "gate funnel label/lock semantic parity", "WARNING",
             "frontend/src/App.jsx:adaptFunnel",
             "no FILTERED_* stage/label rows found — pattern changed")
        return

    for lock_num_str, outcome in outcomes.items():
        lock_num = int(lock_num_str)
        keyword  = _CANONICAL_LOCK_KEYWORDS.get(lock_num)
        if keyword is None:
            continue
        label = label_by_stage.get(outcome)
        if label is None:
            continue  # CHECK 25 already flags missing labels
        if keyword.lower() not in label.lower():
            flag(54, "gate funnel label/lock semantic parity", "WARNING",
                 "frontend/src/App.jsx:adaptFunnel",
                 f'exit_lock {lock_num} ({keyword}) emits "{outcome}", which '
                 f'adaptFunnel labels "{label}" — funnel shows {keyword} '
                 f'rejections under a different lock\'s label')


def run() -> None:
    check24()
    check25()
    check26()
    check28()
    check29()
    check38()
    check49()
    check51()
    check52()
    check53()
    check54()
