#!/usr/bin/env python3
"""
APEX Mechanical Checks — CHECK 3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16
Pure grep/parse, no LLM. Writes findings to audit/nightly-report-YYYY-MM-DD.md.
"""

import json
import re
import sqlite3
import subprocess
from datetime import date, datetime, timedelta, timezone
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
    # Match the production DB path only when it's used as a value — assigned,
    # opened, or connected to. A string that merely mentions the path (e.g. in a
    # comment explaining what we're redirecting away from) is not a risk.
    _PATTERN = re.compile(
        r'(open|connect|Path|DB_PATH\s*=|sqlite3\.connect)\s*[(\s]*["\'].*apex\.db'
    )
    for fpath in (REPO / "tests").rglob("*.py"):
        for i, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
            if _PATTERN.search(line):
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


# ── CHECK 12 — Lock 3 context key parity (demo vs live) ──────────────────────

def check12():
    """
    Parse the ctx keys set in _build_claude_context() (demo) and _build_context() (live)
    using AST and flag any key present in one but absent in the other.
    Known intentional differences are whitelisted.
    """
    import ast

    demo_file = REPO / "backend/gate/gate_runner.py"
    live_file = REPO / "backend/gate/gate_runner_live.py"
    if not demo_file.exists() or not live_file.exists():
        return

    # Keys that are intentionally present only in one runner
    LIVE_ONLY = {"mode"}    # "LIVE — real money" label
    DEMO_ONLY: set = set()  # add any demo-only keys here if needed

    def extract_ctx_keys(source: str, fn_name: str) -> set:
        """
        Walk the AST of fn_name and collect all string literals used as:
          ctx["key"] = ...        (Subscript assignment)
          ctx.update({"key": ...}) (keyword in Call)
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()

        # Find the function def
        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                fn_node = node
                break
        if fn_node is None:
            return set()

        keys: set = set()
        for node in ast.walk(fn_node):
            # ctx["key"] = value
            if (isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Subscript)
                    and isinstance(node.targets[0].value, ast.Name)
                    and node.targets[0].value.id == "ctx"):
                slice_node = node.targets[0].slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    keys.add(slice_node.value)

            # ctx.update({...})
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "ctx"):
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                keys.add(k.value)
                for kw in node.keywords:
                    if isinstance(kw.value, ast.Dict):
                        for k in kw.value.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                keys.add(k.value)

        return keys

    demo_keys = extract_ctx_keys(demo_file.read_text(), "_build_claude_context") - LIVE_ONLY
    live_keys = extract_ctx_keys(live_file.read_text(), "_build_context") - DEMO_ONLY

    for k in sorted(demo_keys - live_keys):
        flag(12, "Lock3 context parity", "CRITICAL",
             "backend/gate/gate_runner_live.py:_build_context",
             f"key '{k}' in demo _build_claude_context but missing from live _build_context")

    for k in sorted(live_keys - demo_keys):
        flag(12, "Lock3 context parity", "WARNING",
             "backend/gate/gate_runner.py:_build_claude_context",
             f"key '{k}' in live _build_context but missing from demo _build_claude_context")


# ── CHECK 13 — Undisclosed config changes ────────────────────────────────────

def check13():
    """
    For each config file, show what changed in its most recent commit.
    Fires every audit until a follow-up commit acknowledges the change.
    A follow-up commit is one whose message contains the key name or 'config:'.
    This catches config changes buried in feature commits and forces review.
    """
    for config_file in ["data/demo_config.json", "data/live_config.json"]:
        path = REPO / config_file
        if not path.exists():
            continue

        # Get the most recent commit that touched this file
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

        # Only flag if the change is recent (within 3 days)
        try:
            age_days = (date.today() - date.fromisoformat(commit_date)).days
            if age_days > 3:
                continue
        except Exception:
            continue

        # Get the commit before this one for the same file
        prev_result = subprocess.run(
            ["git", "log", "-1", "--format=%H", f"{last_sha}^", "--", config_file],
            cwd=REPO, capture_output=True, text=True
        )
        prev_sha = prev_result.stdout.strip()
        if not prev_sha:
            # Fall back to parent commit regardless of whether it touched the file
            prev_sha = f"{last_sha}^"

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

        # Check if a subsequent commit acknowledged these keys
        ack_result = subprocess.run(
            ["git", "log", "--format=%s", f"{last_sha}..HEAD"],
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


# ── CHECK 14 — EOD regime freshness ──────────────────────────────────────────

def check14():
    """
    sector_posteriors.updated_at must be within 3 calendar days.
    3 days covers the widest normal gap: Sunday 1 AM audit, last EOD on Friday.
    Anything older means the catch-up also failed.
    """
    db = REPO / "data/apex.db"
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(db)
        row  = conn.execute("SELECT MAX(updated_at) FROM sector_posteriors").fetchone()
        conn.close()
    except Exception as e:
        flag(14, "EOD regime freshness", "WARNING", "data/apex.db:sector_posteriors",
             f"could not query sector_posteriors: {e}")
        return

    if not row or not row[0]:
        flag(14, "EOD regime freshness", "CRITICAL", "data/apex.db:sector_posteriors",
             "sector_posteriors empty — EOD regime has never run")
        return

    last_dt = datetime.fromisoformat(row[0])
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400

    if age_days > 3:
        flag(14, "EOD regime freshness", "WARNING", "data/apex.db:sector_posteriors",
             f"EOD regime last updated {age_days:.1f} days ago ({row[0][:10]}) — catch-up may have failed")


# ── CHECK 15 — Calibration freshness ─────────────────────────────────────────

def check15():
    """
    data/calibration_done.txt must contain the current ISO week label.
    Missing or stale marker means Sunday 3 AM cron was missed AND catch-up failed.
    """
    marker = REPO / "data/calibration_done.txt"
    current_week = datetime.now(timezone.utc).strftime("%G-W%V")

    if not marker.exists():
        flag(15, "Calibration freshness", "WARNING", "data/calibration_done.txt:—",
             f"calibration marker missing — thresholds not calibrated this week ({current_week})")
        return

    stored = marker.read_text().strip()
    if stored != current_week:
        flag(15, "Calibration freshness", "WARNING", "data/calibration_done.txt:—",
             f"calibration last ran {stored}, current week {current_week} — catch-up may have failed")


# ── CHECK 16 — yfinance scalar extraction ─────────────────────────────────────

def check16():
    """
    Flag .iloc[-1] applied directly to a yfinance column slice without .flatten().
    Newer yfinance returns multi-column DataFrames even for single tickers — .iloc[-1]
    yields a Series instead of a scalar, silently breaking float() conversion.
    Safe pattern: .values.flatten()[-1]
    """
    # Matches ["Close"].iloc[-1] or ['close'].iloc[-1] — the dangerous extraction pattern
    _PATTERN = re.compile(r'\[["\']\w*[Cc]lose["\']\]\.iloc\[-1\]')

    for fpath in REPO.rglob("*.py"):
        if any(skip in str(fpath) for skip in ["node_modules", "venv", ".git", "__pycache__"]):
            continue
        text = fpath.read_text(errors="ignore")
        if "yf.download" not in text:
            continue  # only scan files that call yf.download — Ticker.history() is safe
        if "_slice_history" in text:
            continue  # file uses a wrapper that drops the MultiIndex ticker level — safe
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # skip comment lines
            if _PATTERN.search(line) and ".flatten()" not in line and ".values" not in line:
                # Skip if a yf.Ticker call appears in the 5 lines before — that path is safe
                context = "\n".join(lines[max(0, i - 6):i - 1])
                if "yf.Ticker" in context:
                    continue
                rel = str(fpath.relative_to(REPO))
                flag(16, "yfinance scalar extraction", "WARNING", f"{rel}:{i}",
                     f"`.iloc[-1]` on column slice without `.flatten()`: {stripped[:70]}")


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

    mechanical_checks = {3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16}
    rows = []
    check_names = {
        3: "Fractional qty", 4: "Config parity", 5: "Sector name strings",
        6: "Test DB isolation", 9: "Config value drift",
        10: "Ticker data coverage", 11: "NaN/null pipeline",
        12: "Lock3 context parity", 13: "Undisclosed config change",
        14: "EOD regime freshness", 15: "Calibration freshness",
        16: "yfinance scalar extraction",
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
    check12()
    check13()
    check14()
    check15()
    check16()
    retirement_candidates = update_registry()
    write_report(retirement_candidates or [])
