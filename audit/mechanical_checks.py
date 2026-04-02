#!/usr/bin/env python3
"""
APEX Mechanical Checks — CHECK 3, 4, 5, 6, 9, 10, 11
Pure grep/parse, no LLM. Writes findings to audit/nightly-report-YYYY-MM-DD.md.
"""

import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).parent.parent
TODAY = date.today().isoformat()
REPORT = REPO / "audit" / f"nightly-report-{TODAY}.md"

findings = []  # list of (check_num, check_name, sev, file_line, finding)
triggered = set()


def flag(check_num, check_name, sev, file_line, finding):
    findings.append((check_num, check_name, sev, file_line, finding))
    triggered.add(check_num)


# ── CHECK 3 — Fractional qty ──────────────────────────────────────────────────

def check3():
    path = REPO / "backend/brokers/alpaca.py"
    if not path.exists():
        return
    text = path.read_text()
    # Look for qty assignment without int() conversion
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"qty\s*=", line) and "int(" not in line and "integer" not in line.lower():
            if "notional" in line or "price" in line:
                flag(3, "Fractional qty", "WARNING", f"backend/brokers/alpaca.py:{i}",
                     f"qty assigned without int(): {line.strip()[:60]}")


# ── CHECK 4 — Config parity ───────────────────────────────────────────────────

def check4():
    demo = REPO / "data/demo_config.json"
    live = REPO / "data/live_config.json"
    cfg  = REPO / "backend/config.py"
    if not all(p.exists() for p in [demo, live, cfg]):
        return

    demo_keys = set(json.loads(demo.read_text()).keys())
    live_keys = set(json.loads(live.read_text()).keys())

    for k in demo_keys - live_keys:
        flag(4, "Config parity", "WARNING", "data/live_config.json:—",
             f"key '{k}' in demo but missing from live")
    for k in live_keys - demo_keys:
        flag(4, "Config parity", "WARNING", "data/demo_config.json:—",
             f"key '{k}' in live but missing from demo")


# ── CHECK 5 — Sector name strings ─────────────────────────────────────────────

def check5():
    cfg = REPO / "backend/config.py"
    if not cfg.exists():
        return
    m = re.search(r'SECTORS\s*=\s*\[([^\]]+)\]', cfg.read_text())
    if not m:
        return
    canonical = set(re.findall(r'"([^"]+)"', m.group(1)))

    for ext in ["*.py", "*.ts", "*.tsx"]:
        for fpath in REPO.rglob(ext):
            if "node_modules" in str(fpath) or "venv" in str(fpath):
                continue
            for i, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
                for word in re.findall(r'"([A-Z][a-zA-Z]{3,})"', line):
                    if word in {
                        "Technology","Healthcare","Energy","Industrials","Financials",
                        "ConsumerDisc","ConsumerStaples","Communication","Utilities",
                        "Materials","RealEstate"
                    } - canonical:
                        rel = str(fpath.relative_to(REPO))
                        flag(5, "Sector name strings", "WARNING", f"{rel}:{i}",
                             f"'{word}' not in canonical SECTORS list")


# ── CHECK 6 — Test DB isolation ───────────────────────────────────────────────

def check6():
    for fpath in (REPO / "tests").rglob("*.py"):
        for i, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
            if re.search(r'(data/apex\.db|backend/apex\.db)', line):
                rel = str(fpath.relative_to(REPO))
                flag(6, "Test DB isolation", "CRITICAL", f"{rel}:{i}",
                     "hardcoded reference to production DB path")


# ── CHECK 9 — Config value drift ─────────────────────────────────────────────

def check9():
    demo_path = REPO / "data/demo_config.json"
    live_path = REPO / "data/live_config.json"
    if not demo_path.exists() or not live_path.exists():
        return

    demo = json.loads(demo_path.read_text())
    live = json.loads(live_path.read_text())

    checks = [
        ("lock1_threshold",  demo, "data/demo_config.json", lambda v: v < 0.60, "CRITICAL", "lock1_threshold < 0.60 — in dead zone"),
        ("lock1_threshold",  demo, "data/demo_config.json", lambda v: 0.60 <= v < 0.65, "WARNING", "lock1_threshold < 0.65 — below effective floor"),
        ("lock1_threshold",  live, "data/live_config.json", lambda v: v < 0.65, "WARNING", "lock1_threshold < 0.65 on live"),
        ("vix_threshold",    demo, "data/demo_config.json", lambda v: v < 30,  "WARNING", "vix_threshold < 30 — blocks recovery rallies"),
        ("vix_threshold",    live, "data/live_config.json", lambda v: v < 30,  "WARNING", "vix_threshold < 30 on live"),
        ("take_profit_pct",  demo, "data/demo_config.json", lambda v: v > 0.08, "WARNING", "take_profit_pct > 0.08 — most trades won't hit target"),
        ("max_positions",    demo, "data/demo_config.json", lambda v: v > 6,   "WARNING", "max_positions > 6 — dilutes signal quality"),
    ]
    for key, cfg, label, cond, sev, msg in checks:
        val = cfg.get(key)
        if val is not None and cond(val):
            flag(9, "Config value drift", sev, f"{label}:—", f"{msg} (current: {val})")

    # Recent config commits without per-value justification
    result = subprocess.run(
        ["git", "log", "--oneline", "-10", "--", "data/demo_config.json", "data/live_config.json"],
        cwd=REPO, capture_output=True, text=True
    )
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


# ── CHECK 10 — Ticker signal data coverage ────────────────────────────────────

def check10():
    db_py = REPO / "backend/db.py"
    regime_py = REPO / "backend/sector_regime.py"

    if db_py.exists():
        text = db_py.read_text()
        m = re.search(r'def get_ticker_daily_scores.*?days.*?=\s*(\d+)', text, re.DOTALL)
        if m and int(m.group(1)) < 180:
            flag(10, "Ticker data coverage", "WARNING", "backend/db.py:—",
                 f"get_ticker_daily_scores default days={m.group(1)}, should be 180")

    if regime_py.exists():
        for i, line in enumerate(regime_py.read_text().splitlines(), 1):
            if "get_ticker_daily_scores" in line and "days=" in line:
                m = re.search(r'days=(\d+)', line)
                if m and int(m.group(1)) < 180:
                    flag(10, "Ticker data coverage", "WARNING", f"backend/sector_regime.py:{i}",
                         f"compute_ticker_signals calls get_ticker_daily_scores(days={m.group(1)}), need 180")


# ── CHECK 11 — NaN/null config pipeline ──────────────────────────────────────

def check11():
    app = REPO / "frontend/src/App.jsx"
    if app.exists():
        for i, line in enumerate(app.read_text().splitlines(), 1):
            if "parseFloat(" in line and 'val === ""' not in line and "handleChange" not in line:
                if "e.target.value" in line or ", val)" in line or "(val)" in line:
                    flag(11, "NaN/null pipeline", "WARNING", f"frontend/src/App.jsx:{i}",
                         "parseFloat(val) without empty-string guard")

    macro = REPO / "backend/gate/lock_macro.py"
    if macro.exists():
        text = macro.read_text()
        keys = ["macro_event_blackout_days", "macro_earnings_blackout_days",
                "vix_threshold", "gate_cooloff_hours"]
        for i, line in enumerate(text.splitlines(), 1):
            for k in keys:
                if f'cfg.get("{k}",' in line or f"cfg.get('{k}'," in line:
                    flag(11, "NaN/null pipeline", "WARNING", f"backend/gate/lock_macro.py:{i}",
                         f"cfg.get({k!r}, default) — default won't fire for stored None")


# ── Update check registry ─────────────────────────────────────────────────────

def update_registry():
    checks_path = REPO / "audit/CHECKS.md"
    if not checks_path.exists():
        return

    today = date.today().isoformat()
    retirement_days = 90
    lines = checks_path.read_text().splitlines()
    new_lines = []
    retirement_candidates = []

    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 9 and parts[1].isdigit():
            num = int(parts[1])
            name, added, prompted, files = parts[2], parts[3], parts[4], parts[5]
            lt = today if num in triggered else parts[6]
            lc = parts[7] if num in triggered else today
            try:
                days_since = (date.today() - date.fromisoformat(lt)).days
                if days_since >= retirement_days:
                    since = (date.today() - timedelta(days=retirement_days)).isoformat()
                    if any(
                        subprocess.run(
                            ["git", "log", "--oneline", f"--since={since}", "--", f],
                            cwd=REPO, capture_output=True, text=True
                        ).stdout.strip()
                        for f in files.split(",")
                    ):
                        retirement_candidates.append((num, name, days_since))
            except Exception:
                pass
            new_lines.append(f"| {num} | {name} | {added} | {prompted} | {files} | {lt} | {lc} |")
        else:
            new_lines.append(line)

    checks_path.write_text("\n".join(new_lines) + "\n")
    return retirement_candidates


# ── Write partial report ──────────────────────────────────────────────────────

def write_report(retirement_candidates):
    REPORT.parent.mkdir(exist_ok=True)

    mechanical_checks = {3, 4, 5, 6, 9, 10, 11}
    rows = []
    check_names = {
        3: "Fractional qty", 4: "Config parity", 5: "Sector name strings",
        6: "Test DB isolation", 9: "Config value drift",
        10: "Ticker data coverage", 11: "NaN/null pipeline",
    }

    # One clean row per check that had no findings
    for num in sorted(mechanical_checks):
        if num not in triggered:
            rows.append(f"| {num} {check_names[num]} | \u2713 | \u2014 | \u2014 | \u2014 |")

    # One row per finding
    for num, name, sev, file_line, finding in sorted(findings, key=lambda x: x[0]):
        rows.append(f"| {num} {name} | \u26a0 | {sev} | {file_line} | {finding} |")

    retire_section = "None"
    if retirement_candidates:
        retire_section = "\n".join(
            f"CHECK {n} ({name}) \u2014 {days} days since last triggered"
            for n, name, days in retirement_candidates
        )

    n_crit    = sum(1 for _, _, s, _, _ in findings if s == "CRITICAL")
    n_warn    = sum(1 for _, _, s, _, _ in findings if s == "WARNING")
    n_info    = sum(1 for _, _, s, _, _ in findings if s == "INFO")

    content = f"""# APEX Nightly Audit \u2014 {TODAY}
{len(findings)} issues: {n_crit} critical, {n_warn} warnings, {n_info} info
*(LLM checks 1, 2, 7, 8 appended below by llm_checks.py)*

| Check | Status | Sev | File:line | Finding |
|-------|--------|-----|-----------|---------|
""" + "\n".join(rows) + f"""

## Retirement Candidates
{retire_section}
"""
    REPORT.write_text(content)
    print(f"Mechanical checks done. {len(findings)} finding(s). Report: {REPORT.name}")


if __name__ == "__main__":
    check3()
    check4()
    check5()
    check6()
    check9()
    check10()
    check11()
    retirement_candidates = update_registry()
    write_report(retirement_candidates or [])
