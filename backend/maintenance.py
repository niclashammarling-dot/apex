"""
Structural integrity checks — run Saturday morning before the weekend sweep.

Each check returns a list of issue strings (empty = OK). This lets them be
used both by the scheduler (run_structural_checks) and as pytest assertions
in tests/test_structural.py without any test framework dependency.
"""
import re

from loguru import logger

# ── Individual checks ─────────────────────────────────────────────────────────

def check_config_parity() -> list[str]:
    """
    demo_config and live_config must expose the same set of keys.
    A key missing from live_config means a setting is only configurable
    in demo — the live gate silently falls back to a hardcoded default.
    """
    from backend import demo_config, live_config
    demo_keys = set(demo_config._KEYS)
    live_keys = set(live_config._KEYS)
    issues = []
    only_demo = demo_keys - live_keys
    only_live = live_keys - demo_keys
    if only_demo:
        issues.append(f"Keys in demo_config but missing from live_config: {sorted(only_demo)}")
    if only_live:
        issues.append(f"Keys in live_config but missing from demo_config: {sorted(only_live)}")
    return issues


def check_sector_names() -> list[str]:
    """
    lock4_leading.SECTOR_ETF must map every sector defined in config.SECTORS.
    A missing sector causes Lock 4 to fail all checks for that sector's
    tickers with no error — just a silent 'no ETF mapped' log line.
    """
    from backend.config import EXCLUDED_SECTORS, SECTORS
    from backend.gate.lock4_leading import SECTOR_ETF
    config_sectors = set(SECTORS.keys()) - set(EXCLUDED_SECTORS.keys())
    etf_sectors    = set(SECTOR_ETF.keys())
    issues = []
    missing = config_sectors - etf_sectors
    extra   = etf_sectors    - config_sectors
    if missing:
        issues.append(f"Sectors in config.SECTORS with no ETF in lock4_leading: {sorted(missing)}")
    if extra:
        issues.append(f"Sectors in lock4_leading.SECTOR_ETF not in config.SECTORS: {sorted(extra)}")
    return issues


def check_promote_exclusions() -> list[str]:
    """
    Account-size-specific config keys must not appear in the promotable set.

    starting_balance and daily_loss_cap are calibrated to account size, not
    strategy. If either ends up in demo_thresholds(), Promote will overwrite
    the live values — corrupting the sector exposure denominator and the daily
    loss cap with demo-scale numbers.
    """
    from backend.live_config import _PROMOTE_EXCLUDE, demo_thresholds
    required = {"starting_balance", "daily_loss_cap"}
    issues = []
    promotable = set(demo_thresholds().keys())
    leaked = required - _PROMOTE_EXCLUDE
    if leaked:
        issues.append(
            f"Keys missing from _PROMOTE_EXCLUDE: {sorted(leaked)} — "
            "these will be overwritten on next Promote"
        )
    in_promotable = required & promotable
    if in_promotable:
        issues.append(
            f"Account-specific keys present in demo_thresholds(): {sorted(in_promotable)}"
        )
    return issues


def check_context_parity() -> list[str]:
    """
    Demo and live gate runners must build identical risk_limits keys in the
    Lock 3 context. A missing key means Claude gets null for that constraint
    and has to guess.
    """
    from backend.demo_config import get_demo_config
    from backend.gate import gate_runner, gate_runner_live
    from backend.live_config import get_live_config

    signal    = {"ticker": "_CHECK", "sector": "Technology", "signal_score": 0.5}

    demo_cfg  = get_demo_config()
    live_cfg  = get_live_config()
    demo_wallet = {"balance": 2000, "open_positions": 0, "sector_exposure": {}}
    live_wallet = {**demo_wallet, "starting_balance": live_cfg["starting_balance"]}

    demo_ctx = gate_runner._build_base_context(signal, demo_wallet, demo_cfg)
    live_ctx = gate_runner_live._build_base_context(signal, live_wallet, live_cfg)

    demo_keys = set(demo_ctx.get("risk_limits", {}).keys())
    live_keys = set(live_ctx.get("risk_limits", {}).keys())

    issues = []
    only_demo = demo_keys - live_keys
    only_live = live_keys - demo_keys
    if only_demo:
        issues.append(f"risk_limits keys only in demo context: {sorted(only_demo)}")
    if only_live:
        issues.append(f"risk_limits keys only in live context: {sorted(only_live)}")
    return issues


def check_gate_insert_fields() -> list[str]:
    """
    Every column in demo_gate_history and live_gate_history must appear in
    the corresponding INSERT SQL in db.py.

    Failure mode: a column added via ADD COLUMN + schema migration is silently
    never written because the INSERT statement wasn't updated. Call-site drift
    (correct INSERT SQL but incomplete dict at the call site) is caught at
    runtime by _assert_insert_fields(), which fires every gate cycle.
    """
    import inspect

    from backend import db

    issues = []

    for table, insert_fn in [
        ("demo_gate_history", db.insert_demo_gate_result),
        ("live_gate_history", db.insert_live_gate_result),
    ]:
        fn_name = insert_fn.__name__

        # Extract INSERT SQL from function source
        src = inspect.getsource(insert_fn)
        sql_match = re.search(r'_SQL\s*=\s*"""(.*?)"""', src, re.DOTALL)
        if not sql_match:
            issues.append(f"{fn_name}: could not locate _SQL in source — check is unmaintainable")
            continue
        sql_params = set(re.findall(r":(\w+)", sql_match.group(1)))

        # Schema columns (excluding auto-increment id)
        conn = db.get_db()
        try:
            pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            conn.close()
        schema_cols = {row[1] for row in pragma} - {"id"}

        # Schema columns absent from INSERT SQL → column will never be written
        missing_from_sql = schema_cols - sql_params
        if missing_from_sql:
            issues.append(
                f"{fn_name}: schema columns not in INSERT SQL "
                f"(will never be written): {sorted(missing_from_sql)}"
            )

        # INSERT SQL names a phantom column not in schema → INSERT will error
        phantom = sql_params - schema_cols
        if phantom:
            issues.append(
                f"{fn_name}: INSERT SQL names columns not in schema: {sorted(phantom)}"
            )

    return issues


# ── Scheduler entry point ─────────────────────────────────────────────────────

_CHECKS = [
    ("config_parity",        check_config_parity),
    ("sector_names",         check_sector_names),
    ("context_parity",       check_context_parity),
    ("promote_exclusions",   check_promote_exclusions),
    ("gate_insert_fields",   check_gate_insert_fields),
]


def run_structural_checks() -> bool:
    """
    Run all structural checks. Logs each result and sends an alert if any fail.
    Returns True if all checks pass, False otherwise.
    Called by the scheduler every Saturday at 06:00 ET.
    """
    logger.info("Structural checks: starting")
    failures: list[str] = []

    for name, fn in _CHECKS:
        try:
            issues = fn()
            if issues:
                for issue in issues:
                    logger.error(f"Structural check [{name}] FAIL: {issue}")
                    failures.append(f"[{name}] {issue}")
            else:
                logger.info(f"Structural check [{name}]: OK")
        except Exception as e:
            msg = f"[{name}] raised: {e}"
            logger.error(f"Structural check error: {msg}")
            failures.append(msg)

    if failures:
        logger.error(f"Structural checks: {len(failures)} issue(s) found")
        _alert_failures(failures)
        return False

    logger.info("Structural checks: all passed")
    return True


def _alert_failures(failures: list[str]) -> None:
    try:
        from backend.alerts import _cfg, _send_email, _send_slack
        cfg   = _cfg()
        title = "[APEX] Structural Check Failed"
        body  = (
            f"{len(failures)} issue(s) detected during Saturday maintenance check.\n\n"
            + "\n".join(f"• {f}" for f in failures)
            + "\n\nReview and fix before Monday's market open."
        )
        if cfg["slack_url"]:
            _send_slack(cfg["slack_url"], title, body)
        if cfg["email_to"] and cfg["smtp_user"] and cfg["smtp_pass"]:
            _send_email(cfg, title, body)
    except Exception as e:
        logger.warning(f"Structural check alert failed to send: {e}")
