"""
Structural integrity checks — run Saturday morning before the weekend sweep.

Each check returns a list of issue strings (empty = OK). This lets them be
used both by the scheduler (run_structural_checks) and as pytest assertions
in tests/test_structural.py without any test framework dependency.
"""
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
    lock_leading.SECTOR_ETF must map every sector defined in config.SECTORS.
    A missing sector causes Lock Leading to fail all checks for that sector's
    tickers with no error — just a silent 'no ETF mapped' log line.
    """
    from backend.config import SECTORS
    from backend.gate.lock_leading import SECTOR_ETF
    config_sectors = set(SECTORS.keys())
    etf_sectors    = set(SECTOR_ETF.keys())
    issues = []
    missing = config_sectors - etf_sectors
    extra   = etf_sectors    - config_sectors
    if missing:
        issues.append(f"Sectors in config.SECTORS with no ETF in lock_leading: {sorted(missing)}")
    if extra:
        issues.append(f"Sectors in lock_leading.SECTOR_ETF not in config.SECTORS: {sorted(extra)}")
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

    signal    = {"ticker": "_CHECK", "sector": "Technology"}
    l2        = {"score": 0.5, "conviction": "low", "key_themes": [], "summary": "check"}
    l_leading = {"pass_count": 2, "checks": {}}

    demo_cfg  = get_demo_config()
    live_cfg  = get_live_config()
    demo_wallet = {"balance": 2000, "open_positions": 0, "sector_exposure": {}}
    live_wallet = {**demo_wallet, "starting_balance": live_cfg["starting_balance"]}

    demo_ctx = gate_runner._build_claude_context(signal, l2, l_leading, demo_wallet, demo_cfg)
    live_ctx = gate_runner_live._build_context(signal, l2, l_leading, live_wallet, live_cfg)

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


# ── Scheduler entry point ─────────────────────────────────────────────────────

_CHECKS = [
    ("config_parity",  check_config_parity),
    ("sector_names",   check_sector_names),
    ("context_parity", check_context_parity),
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
