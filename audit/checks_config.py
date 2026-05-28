"""
Config-domain mechanical checks — CHECKs 4, 9, 11, 13, 21, 37, 40.

Covers: demo/live key parity, threshold value bounds, NaN/null pipeline,
undisclosed config changes, overflow increment range, promote exclusion
integrity, and config key wiring coverage.
"""
import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

from audit._audit_core import REPO, flag


# ── CHECK 4 — Config parity ───────────────────────────────────────────────────

def check4():
    demo = REPO / "data/demo_config.json"
    live = REPO / "data/live_config.json"
    if not all(p.exists() for p in [demo, live]):
        return

    demo_keys = set(json.loads(demo.read_text()).keys())
    live_keys = set(json.loads(live.read_text()).keys())

    for k in demo_keys - live_keys:
        flag(4, "Config parity", "WARNING", "data/live_config.json:—",
             f"key '{k}' in demo but missing from live")
    for k in live_keys - demo_keys:
        flag(4, "Config parity", "WARNING", "data/demo_config.json:—",
             f"key '{k}' in live but missing from demo")


# ── CHECK 9 — Config value drift ─────────────────────────────────────────────

def check9():
    demo_path = REPO / "data/demo_config.json"
    live_path = REPO / "data/live_config.json"
    if not demo_path.exists() or not live_path.exists():
        return

    demo = json.loads(demo_path.read_text())
    live = json.loads(live_path.read_text())

    checks = [
        ("lock1_threshold",  demo, "data/demo_config.json", lambda v: v < 0.60, "CRITICAL", "lock1_threshold < 0.60 — in dead zone (demo floor is 0.60, intentional)"),
        ("lock1_threshold",  live, "data/live_config.json", lambda v: v < 0.65, "WARNING",  "lock1_threshold < 0.65 on live"),
        ("vix_threshold",    demo, "data/demo_config.json", lambda v: v < 30,  "WARNING", "vix_threshold < 30 — blocks recovery rallies"),
        ("vix_threshold",    live, "data/live_config.json", lambda v: v < 30,  "WARNING", "vix_threshold < 30 on live"),
        ("take_profit_pct",  demo, "data/demo_config.json", lambda v: v > 0.08, "WARNING", "take_profit_pct > 0.08 — most trades won't hit target"),
        ("max_positions",    demo, "data/demo_config.json", lambda v: v > 15,  "WARNING", "max_positions > 15 — beyond portfolio capacity"),
    ]
    for key, cfg, label, cond, sev, msg in checks:
        val = cfg.get(key)
        if val is not None and cond(val):
            flag(9, "Config value drift", sev, f"{label}:—", f"{msg} (current: {val})")

    cutoff = (date.today() - timedelta(days=7)).isoformat()
    recent = subprocess.run(
        ["git", "log", f"--since={cutoff}", "--oneline", "--",
         "data/demo_config.json", "data/live_config.json"],
        cwd=REPO, capture_output=True, text=True
    )
    for line in recent.stdout.strip().splitlines():
        if not any(kw in line.lower() for kw in ["→", "->", "config:", "threshold", "pct", "value"]):
            flag(9, "Config value drift", "INFO", "data/*_config.json:—",
                 f"config commit without per-value note: {line[:60]}")


# ── CHECK 11 — NaN/null config pipeline ──────────────────────────────────────

def check11():
    app = REPO / "frontend/src/App.jsx"
    if app.exists():
        for i, line in enumerate(app.read_text().splitlines(), 1):
            if "parseFloat(" in line and 'val === ""' not in line and "handleChange" not in line:
                if "e.target.value" in line or ", val)" in line or "(val)" in line:
                    flag(11, "NaN/null pipeline", "WARNING", f"frontend/src/App.jsx:{i}",
                         "parseFloat(val) without empty-string guard")

    eligibility = REPO / "backend/gate/lock1_eligibility.py"
    if eligibility.exists():
        text = eligibility.read_text()
        keys = ["macro_event_blackout_days", "macro_earnings_blackout_days",
                "vix_threshold", "gate_cooloff_hours"]
        for i, line in enumerate(text.splitlines(), 1):
            for k in keys:
                if f'cfg.get("{k}",' in line or f"cfg.get('{k}'," in line:
                    flag(11, "NaN/null pipeline", "WARNING", f"backend/gate/lock1_eligibility.py:{i}",
                         f"cfg.get({k!r}, default) — default won't fire for stored None")


# ── CHECK 13 — Undisclosed config changes ────────────────────────────────────

def check13():
    """
    For each config file, show what changed in its most recent commit.
    Fires every audit until a follow-up commit acknowledges the change.
    A follow-up commit is one whose message contains the key name or 'config:'.
    """
    for config_file in ["data/demo_config.json", "data/live_config.json"]:
        path = REPO / config_file
        if not path.exists():
            continue

        result = subprocess.run(
            ["git", "log", "-1", "--format=%H|%as|%s", "--", config_file],
            cwd=REPO, capture_output=True, text=True
        )
        if not result.stdout.strip():
            continue

        parts = result.stdout.strip().split("|", 2)
        if len(parts) < 3:
            continue
        last_sha, commit_date, last_msg = parts

        try:
            age_days = (date.today() - date.fromisoformat(commit_date)).days
            if age_days > 3:
                continue
        except Exception:
            continue

        prev_result = subprocess.run(
            ["git", "log", "-1", "--format=%H", f"{last_sha}^", "--", config_file],
            cwd=REPO, capture_output=True, text=True
        )
        prev_sha = prev_result.stdout.strip() or f"{last_sha}^"

        try:
            curr_text = subprocess.run(
                ["git", "show", f"{last_sha}:{config_file}"],
                cwd=REPO, capture_output=True, text=True
            ).stdout
            prev_text = subprocess.run(
                ["git", "show", f"{prev_sha}:{config_file}"],
                cwd=REPO, capture_output=True, text=True
            ).stdout
            curr = json.loads(curr_text)
            prev = json.loads(prev_text)
        except (json.JSONDecodeError, Exception):
            continue

        changed_keys = [
            k for k in set(prev) | set(curr)
            if prev.get(k, "<absent>") != curr.get(k, "<absent>")
        ]
        if not changed_keys:
            continue

        ack_result = subprocess.run(
            ["git", "log", "--format=%B", f"{last_sha}..HEAD"],
            cwd=REPO, capture_output=True, text=True
        )
        ack_messages = ack_result.stdout.lower()
        acknowledged = "config:" in ack_messages or all(
            k.lower() in ack_messages for k in changed_keys
        )
        if acknowledged:
            continue

        for k in sorted(changed_keys):
            old_val = prev.get(k, "<absent>")
            new_val = curr.get(k, "<absent>")
            flag(13, "Undisclosed config change", "WARNING",
                 f"{config_file}:—",
                 f"{k}: {old_val} → {new_val} (in {last_sha[:8]}: \"{last_msg[:50]}\")")


# ── CHECK 21 — Overflow quant increment range ─────────────────────────────────

def check21():
    """
    overflow_quant_increment must be present in both demo and live config JSON
    files and within a sensible range (0.01–0.25). Outside this band the
    escalating threshold either becomes meaningless (too small) or immediately
    unreachable (too large).
    """
    demo = REPO / "data/demo_config.json"
    live = REPO / "data/live_config.json"
    for label, path in [("demo_config", demo), ("live_config", live)]:
        if not path.exists():
            continue
        try:
            val = json.loads(path.read_text()).get("overflow_quant_increment")
        except Exception as e:
            flag(21, "Overflow increment range", "WARNING", f"data/{path.name}:overflow_quant_increment",
                 f"could not parse config: {e}")
            continue
        if val is None:
            flag(21, "Overflow increment range", "CRITICAL", f"data/{path.name}:overflow_quant_increment",
                 "overflow_quant_increment missing — overflow logic falls back to hardcoded 0.05")
        elif not (0.01 <= float(val) <= 0.25):
            flag(21, "Overflow increment range", "WARNING", f"data/{path.name}:overflow_quant_increment",
                 f"overflow_quant_increment={val} outside expected range 0.01–0.25")


# ── CHECK 37 — Promote exclusion integrity ────────────────────────────────────

def check37():
    """
    Promote exclusion integrity — account-size-specific config keys must not
    appear in the promotable set.

    Two structural assertions:
      1. _PROMOTE_EXCLUDE in backend/live_config.py contains both "starting_balance"
         and "daily_loss_cap" (text check — catches accidental removal).
      2. demo_thresholds() returns neither key at runtime (import check — catches
         the filter being bypassed in code even if the constant looks correct).

    Prevented by: 2026-05-15. Promote wrote starting_balance=2000 into live_config,
    corrupting sector exposure denominator (notional/2000 = 735% projected, sector
    cap 20% → all live trades unconditionally rejected). daily_loss_cap=$100 would
    have imposed near-zero daily limit on a $100k account.
    """
    REQUIRED = {"starting_balance", "daily_loss_cap"}
    path = REPO / "backend/live_config.py"
    if not path.exists():
        flag(37, "Promote exclusion integrity", "CRITICAL",
             "backend/live_config.py", "live_config.py not found")
        return

    text = path.read_text()

    excl_match = re.search(r'_PROMOTE_EXCLUDE\s*=\s*\{([^}]+)\}', text)
    if not excl_match:
        flag(37, "Promote exclusion integrity", "CRITICAL",
             "backend/live_config.py",
             "_PROMOTE_EXCLUDE not defined — all config keys are promotable")
        return

    excl_body = excl_match.group(1)
    for key in REQUIRED:
        if f'"{key}"' not in excl_body and f"'{key}'" not in excl_body:
            flag(37, "Promote exclusion integrity", "CRITICAL",
                 "backend/live_config.py",
                 f'"{key}" missing from _PROMOTE_EXCLUDE — Promote will overwrite live value '
                 f'with demo-scale equivalent on next run')

    # Structural check: demo_thresholds() must reference _PROMOTE_EXCLUDE in its body.
    # This catches the bypass case (filter code changed) without importing the module.
    fn_match = re.search(r'def demo_thresholds\(\)[^:]*:(.*?)(?=\ndef |\Z)', text, re.DOTALL)
    if not fn_match:
        flag(37, "Promote exclusion integrity", "CRITICAL",
             "backend/live_config.py",
             "demo_thresholds() not found — Promote function is missing")
    elif "_PROMOTE_EXCLUDE" not in fn_match.group(1):
        flag(37, "Promote exclusion integrity", "CRITICAL",
             "backend/live_config.py:demo_thresholds",
             "demo_thresholds() body does not reference _PROMOTE_EXCLUDE — "
             "exclusion filter bypassed; required keys will be promoted")


# ── CHECK 40 — Config coverage audit ─────────────────────────────────────────

def check40():
    """
    Config coverage audit — two sub-checks:

    A) Wiring coverage: every key in _KEYS (demo_config.py / live_config.py) must
       have at least one consumption hit anywhere in the repo. A recognized key with
       zero hits is the exact failure class that produced the trailing_stop_pct gap:
       key registered, default set, consuming code never read it.

    B) None-default detection: keys whose _defaults() value is None are "recognized
       but silently disabled". Allowlisted None defaults get WARNING (acknowledged
       disabled feature). Unlisted None defaults get CRITICAL.

    Prevented by: trailing_stop_pct post-mortem (2026-05-17). Third instance of the
    valid-looking silence failure class.
    """
    # Explicit operator acknowledgment that these keys are intentionally disabled by default.
    _NONE_DEFAULTS_ALLOWED = {"trailing_stop_pct"}

    _SKIP_SEGMENTS = {".venv", "venv", "__pycache__", ".git", "node_modules", "tests"}
    _SKIP_FILES = {
        REPO / "backend/demo_config.py",
        REPO / "backend/live_config.py",
    }

    def _collect_keys(path: Path) -> list:
        text = path.read_text()
        m = re.search(r'_KEYS\s*=\s*\[([^\]]+)\]', text, re.DOTALL)
        if not m:
            return []
        return re.findall(r'"([^"]+)"', m.group(1))

    def _collect_defaults(cfg_path: Path) -> dict | None:
        """
        Parse None-valued entries from _defaults() source text.

        _defaults() returns a dict whose values are either named constants
        (imported from backend.config) or literal None. We only care about
        the None literals — those are "feature disabled" entries. Named
        constants are non-None by construction; importing the full module
        chain is not needed.

        Returns {key: None} for every "key": None entry, or None if the
        function body cannot be found.
        """
        text = cfg_path.read_text()
        fn_match = re.search(r'def _defaults\(\)[^:]*:(.*?)(?=\ndef |\Z)', text, re.DOTALL)
        if not fn_match:
            return None
        body = fn_match.group(1)
        none_keys = re.findall(r'"([^"]+)":\s*None', body)
        return {k: None for k in none_keys}

    def _repo_py_files():
        for fpath in REPO.rglob("*.py"):
            if any(seg in fpath.parts for seg in _SKIP_SEGMENTS):
                continue
            if fpath in _SKIP_FILES:
                continue
            yield fpath

    demo_cfg = REPO / "backend/demo_config.py"
    live_cfg  = REPO / "backend/live_config.py"

    if not demo_cfg.exists() or not live_cfg.exists():
        flag(40, "Config coverage audit", "WARNING",
             "backend/demo_config.py,backend/live_config.py",
             "one or both config modules missing — cannot run coverage check")
        return

    demo_keys = _collect_keys(demo_cfg)
    live_keys = _collect_keys(live_cfg)

    if not demo_keys and not live_keys:
        flag(40, "Config coverage audit", "WARNING",
             "backend/demo_config.py",
             "_KEYS list not found or empty in both config modules — pattern may have changed")
        return

    all_keys = sorted(set(demo_keys) | set(live_keys))
    repo_texts = [
        (fp, fp.read_text(errors="ignore"))
        for fp in _repo_py_files()
    ]

    for key in all_keys:
        pattern = re.compile(
            rf'''cfg\[["']{re.escape(key)}["']\]|cfg\.get\(["']{re.escape(key)}["']'''
        )
        hits = [fp for fp, text in repo_texts if pattern.search(text)]
        if not hits:
            flag(40, "Config coverage audit", "CRITICAL",
                 "backend/demo_config.py,backend/live_config.py",
                 f'"{key}" in _KEYS but no cfg["{key}"] or cfg.get("{key}"...) found in repo '
                 f'— recognized key with no consumer (valid-looking silence risk)')

    for label, cfg_path in [("demo", demo_cfg), ("live", live_cfg)]:
        defaults = _collect_defaults(cfg_path)
        if defaults is None:
            flag(40, "Config coverage audit", "WARNING",
                 f"backend/{label}_config.py",
                 f"_defaults() function not found in {label}_config.py — skipping None-default check")
            continue
        for key, val in defaults.items():
            if val is None:
                if key in _NONE_DEFAULTS_ALLOWED:
                    flag(40, "Config coverage audit", "WARNING",
                         f"backend/{label}_config.py:_defaults",
                         f'"{key}" defaults to None (feature disabled) — acknowledged in '
                         f'_NONE_DEFAULTS_ALLOWED; set a non-None value to enable')
                else:
                    flag(40, "Config coverage audit", "CRITICAL",
                         f"backend/{label}_config.py:_defaults",
                         f'"{key}" defaults to None but is not in _NONE_DEFAULTS_ALLOWED '
                         f'— unlisted disabled default; add to allowlist with intent comment '
                         f'or set a real value')


def run() -> None:
    check4()
    check9()
    check11()
    check13()
    check21()
    check37()
    check40()
