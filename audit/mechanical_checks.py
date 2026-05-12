#!/usr/bin/env python3
"""
APEX Mechanical Checks — CHECK 3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 24, 25, 26, 27, 28, 29, 32
Pure grep/parse, no LLM. Writes findings to audit/nightly-report-YYYY-MM-DD.md.
"""
# CHECK 22 added 2026-04-15: Yahoo data pipeline health
# CHECK 24 added 2026-04-18: Chain-runner wiring integrity
# CHECK 25 added 2026-04-20: gate_decision string parity (backend _OUTCOMES vs frontend + db consumers)
# CHECK 26 added 2026-05-03: L1/L2 threshold-source parity (both must use get_ticker_thresholds())
# CHECK 27 added 2026-05-06: GICS sector classification parity (tickers.json vs authoritative GICS map)
# CHECK 29 added 2026-05-07: Live sector exposure cap wiring (sector_exposure_live computed and enforced in both execution branches)
# CHECK 33 added 2026-05-09: Bayesian multiplier health (silent all-1.0 detection via data/bayesian_multiplier_stats.json)

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
    # Look for qty assignment (start-of-statement) without int() conversion
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"^\s*qty\s*=", line) and "int(" not in line and "integer" not in line.lower():
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
        ("lock1_threshold",  demo, "data/demo_config.json", lambda v: v < 0.60, "CRITICAL", "lock1_threshold < 0.60 — in dead zone (demo floor is 0.60, intentional)"),
        ("lock1_threshold",  live, "data/live_config.json", lambda v: v < 0.65, "WARNING",  "lock1_threshold < 0.65 on live"),
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

    demo_keys = extract_ctx_keys(demo_file.read_text(), "_build_base_context") - LIVE_ONLY
    live_keys = extract_ctx_keys(live_file.read_text(), "_build_base_context") - DEMO_ONLY

    for k in sorted(demo_keys - live_keys):
        flag(12, "Lock3 context parity", "CRITICAL",
             "backend/gate/gate_runner_live.py:_build_base_context",
             f"key '{k}' in demo _build_base_context but missing from live _build_base_context")

    for k in sorted(live_keys - demo_keys):
        flag(12, "Lock3 context parity", "WARNING",
             "backend/gate/gate_runner.py:_build_base_context",
             f"key '{k}' in live _build_base_context but missing from demo _build_base_context")


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


# ── CHECK 17 — Sentiment cache freshness ─────────────────────────────────────

def check17():
    """
    sentiment_cache must have been populated today (Mon–Fri).
    A stale or missing cache means the 9:35 AM pre-fetch failed or was skipped,
    and Lock 2 is running without Reddit/RSS content for watchlist tickers.
    Only fires on weekdays — cache is intentionally not refreshed on weekends.
    """
    today = date.today()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        return

    db = REPO / "data/apex.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(db)
        row  = conn.execute("SELECT MAX(fetched_at) FROM sentiment_cache").fetchone()
        conn.close()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — pre-fetch has never run
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             "sentiment_cache table missing — pre-fetch has never run")
        return
    except Exception as e:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             f"could not query sentiment_cache: {e}")
        return

    if not row or not row[0]:
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             "sentiment_cache is empty — pre-fetch has never successfully run")
        return

    last_dt  = datetime.fromisoformat(row[0])
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600

    if age_hours > 26:  # more than a trading day old
        flag(17, "Sentiment cache freshness", "WARNING", "data/apex.db:sentiment_cache",
             f"sentiment_cache last populated {age_hours:.1f}h ago — pre-fetch may have failed")


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


# ── CHECK 22 — Yahoo data pipeline health ────────────────────────────────────

def check22():
    """
    On trading days, verify that sector_snapshots received data today and that
    regime_result_cache.json exists and is fresh (written today or yesterday).

    Canary for Yahoo Finance rate-limiting or API outage: the first symptom is
    partial download failures before a full 429 block. Stale snapshots on a
    trading day means the polling pipeline silently failed.
    """
    today = date.today()
    weekday = today.weekday()
    is_trading_day = weekday < 5   # Mon–Fri only

    # ── Sub-check A: sector_snapshots written today ───────────────────────────
    if is_trading_day:
        db_path = REPO / "data/apex.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                cutoff = today.isoformat()
                row = conn.execute(
                    "SELECT COUNT(*) FROM sector_snapshots WHERE timestamp >= ?",
                    (cutoff,),
                ).fetchone()
                conn.close()
                count = row[0] if row else 0
                if count == 0:
                    flag(22, "Yahoo data pipeline health", "WARNING",
                         "data/apex.db:sector_snapshots",
                         f"No sector snapshots written today ({today}) — "
                         "polling may have failed due to Yahoo rate limit or API outage")
                elif count < 10:
                    flag(22, "Yahoo data pipeline health", "WARNING",
                         "data/apex.db:sector_snapshots",
                         f"Only {count} sector snapshot rows today ({today}); "
                         "expected multiple poll cycles — check for partial Yahoo failures")
            except Exception as e:
                flag(22, "Yahoo data pipeline health", "WARNING",
                     "data/apex.db:sector_snapshots",
                     f"Could not query sector_snapshots: {e}")

    # ── Sub-check B: regime_result_cache.json exists and is recent ────────────
    cache = REPO / "data/regime_result_cache.json"
    if not cache.exists():
        flag(22, "Yahoo data pipeline health", "WARNING",
             "data/regime_result_cache.json",
             "regime_result_cache.json missing — regime-bayes will show unavailable after any restart "
             "until next EOD run")
    else:
        try:
            import json as _json
            cached = _json.loads(cache.read_text())
            cached_date = cached.get("date", "")
            age_days = (today - date.fromisoformat(cached_date)).days if cached_date else 999
            if age_days > 5:
                flag(22, "Yahoo data pipeline health", "WARNING",
                     "data/regime_result_cache.json",
                     f"regime_result_cache.json is {age_days} days old (last: {cached_date}) — "
                     "EOD regime may not have run successfully this week")
        except Exception as e:
            flag(22, "Yahoo data pipeline health", "WARNING",
                 "data/regime_result_cache.json",
                 f"Could not parse regime_result_cache.json: {e}")


# ── CHECK 24 — Chain-runner wiring integrity ──────────────────────────────────

def check24():
    """
    Verify that:
    1. chain.py defines evaluate_chain
    2. gate_runner.py imports evaluate_chain from backend.gate.chain
    3. gate_runner_live.py imports evaluate_chain (directly or via gate_runner)
    4. chain.py calls all five lock evaluate functions

    Prevents recurrence of the 2026-04-18 incident: chain.py existed for a week
    with zero callers — the old lock modules were silently still live.
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

    # chain.py must define evaluate_chain
    if "def evaluate_chain(" not in chain_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/chain.py", "evaluate_chain not defined in chain.py")

    # gate_runner.py must import evaluate_chain from chain
    if "evaluate_chain" not in runner_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/gate_runner.py",
             "evaluate_chain not imported/called — runner is not wired to chain.py")

    # gate_runner_live.py must reference evaluate_chain
    if "evaluate_chain" not in runner_l_text:
        flag(24, "Chain-runner wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "evaluate_chain not imported/called — live runner is not wired to chain.py")

    # chain.py must call all 5 lock evaluate functions
    for lock_fn in ["lock1_evaluate", "lock2_evaluate", "lock3_evaluate",
                    "lock4_evaluate", "lock5_evaluate"]:
        if lock_fn not in chain_text:
            flag(24, "Chain-runner wiring", "CRITICAL",
                 "backend/gate/chain.py",
                 f"{lock_fn} not called in chain.py — lock dropped from chain")

    # Neither runner should directly import old retired lock modules
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
    still checked for old FILTERED_MACRO string — tickers showed all-green despite
    L1 failure; funnel macro_fail count silently zeroed out.
    """
    runner = REPO / "backend/gate/gate_runner.py"
    gate_feed     = REPO / "frontend/src/components/GateFeed.jsx"
    live_gate_feed = REPO / "frontend/src/components/LiveGateFeed.jsx"
    db_file = REPO / "backend/db.py"

    if not runner.exists():
        return

    runner_text = runner.read_text()

    # Extract string values from _OUTCOMES dict: 1: "FILTERED_ELIGIBILITY", etc.
    outcomes = re.findall(r'\d+:\s*"(FILTERED_[A-Z_]+)"', runner_text)
    if not outcomes:
        flag(25, "gate_decision string parity", "WARNING",
             "backend/gate/gate_runner.py:_OUTCOMES",
             "_OUTCOMES dict not found or no FILTERED_* strings — pattern changed")
        return

    for outcome in outcomes:
        for label, path in [("GateFeed.jsx", gate_feed), ("LiveGateFeed.jsx", live_gate_feed)]:
            if not path.exists():
                continue
            text = path.read_text()
            if outcome not in text:
                flag(25, "gate_decision string parity", "CRITICAL",
                     str(path.relative_to(REPO)),
                     f'"{outcome}" emitted by _OUTCOMES but not handled in {label} '
                     f'(getLockStates or badge label) — unrecognized decision shows all-green locks')

        if db_file.exists():
            db_text = db_file.read_text()
            # Only check FILTERED_* strings that appear in funnel SQL/dicts
            # (SKIPPED_* and TRADE_* are handled separately — only check FILTERED_*)
            if outcome not in db_text:
                flag(25, "gate_decision string parity", "WARNING",
                     "backend/db.py",
                     f'"{outcome}" emitted by _OUTCOMES but not referenced in db.py '
                     f'— funnel counts for this outcome will silently be 0')

    # ── Sub-check B: ticker signal strings ────────────────────────────────────
    # Extract signal = "..." values from sector_regime.py (exclude vel_signal= lines)
    regime_py = REPO / "backend/sector_regime.py"
    if not regime_py.exists():
        return
    regime_text = regime_py.read_text()
    # Match `signal = "value"` but not `vel_signal = "value"`
    ticker_signals = set(re.findall(r'(?<!vel_)signal\s*=\s*"([a-z]+)"', regime_text))

    # Frontend components that display per-ticker signal labels
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
            # Only check components that have a signal map (contain at least one known key)
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
    runner       = REPO / "backend/gate/gate_runner.py"
    runner_live  = REPO / "backend/gate/gate_runner_live.py"
    lock2        = REPO / "backend/gate/lock2_quant.py"

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

    if lock2.exists():
        lock2_text = lock2.read_text()
        if "get_ticker_thresholds" not in lock2_text:
            flag(26, "L1/L2 threshold-source parity", "CRITICAL",
                 "backend/gate/lock2_quant.py:_sector_threshold",
                 "_sector_threshold() does not call get_ticker_thresholds() — L2 threshold "
                 "source has diverged from L1; likely reverted to static config value")


def check27():
    """
    Verify each ticker in tickers.json is placed in its correct GICS sector.

    Uses a hardcoded authoritative map — the source of truth is GICS/S&P, not
    yfinance (which can lag reclassifications). Map must be updated manually when
    GICS restructurings occur (last major restructuring: September 2018).

    Prevented by: May 2026 incident where META, V, and MA were left in pre-2018
    sector assignments (Technology and Financials respectively) after the 2018 GICS
    restructuring created Communication Services and moved payment networks to IT.
    The misclassification meant Lock 4 benchmarked V and MA against XLF (wrong ETF)
    and META added advertising-cycle noise to the Technology sector score.
    """
    # Authoritative GICS sector for each ticker in the APEX universe.
    # Update this map whenever a ticker is added or GICS publishes a reclassification.
    GICS_MAP = {
        # Technology (XLK)
        "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
        "AMD": "Technology", "V": "Technology",
        # Healthcare (XLV)
        "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
        "MRNA": "Healthcare", "LLY": "Healthcare",
        # Energy (XLE)
        "XOM": "Energy", "CVX": "Energy", "HAL": "Energy",
        "COP": "Energy", "OXY": "Energy",
        # Industrials (XLI)
        "CAT": "Industrials", "BA": "Industrials", "GE": "Industrials",
        "HON": "Industrials", "DE": "Industrials",
        # Financials (XLF) — excludes V and MA (moved to IT in 2018)
        "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
        "BLK": "Financials", "MS": "Financials",
        # ConsumerDisc (XLY)
        "AMZN": "ConsumerDisc", "TSLA": "ConsumerDisc", "NKE": "ConsumerDisc",
        "MCD": "ConsumerDisc", "HD": "ConsumerDisc",
        # ConsumerStaples (XLP)
        "PG": "ConsumerStaples", "KO": "ConsumerStaples", "PEP": "ConsumerStaples",
        "WMT": "ConsumerStaples", "COST": "ConsumerStaples",
        # Communication (XLC) — includes META and GOOGL post-2018 restructuring
        "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
        "DIS": "Communication", "SPOT": "Communication",
        # Utilities (XLU)
        "DUK": "Utilities", "SO": "Utilities", "AEP": "Utilities",
        "EXC": "Utilities", "NEE": "Utilities",
        # Materials (XLB)
        "LIN": "Materials", "APD": "Materials", "NEM": "Materials",
        "FCX": "Materials", "SHW": "Materials",
        # RealEstate (XLRE)
        "PLD": "RealEstate", "AMT": "RealEstate", "EQIX": "RealEstate",
        "SPG": "RealEstate", "WELL": "RealEstate",
    }

    tickers_path = REPO / "data/tickers.json"
    if not tickers_path.exists():
        flag(27, "GICS sector classification parity", "CRITICAL",
             "data/tickers.json", "tickers.json not found")
        return

    import json as _json
    universe = _json.loads(tickers_path.read_text())

    for apex_sector, meta in universe.items():
        for ticker in meta.get("tickers", []):
            expected = GICS_MAP.get(ticker)
            if expected is None:
                flag(27, "GICS sector classification parity", "WARN",
                     "data/tickers.json",
                     f"{ticker} has no GICS entry in the audit map — add it when onboarding new tickers")
            elif expected != apex_sector:
                flag(27, "GICS sector classification parity", "CRITICAL",
                     "data/tickers.json",
                     f"{ticker} is in APEX sector '{apex_sector}' but GICS assigns it to '{expected}'; "
                     f"Lock 4 ETF benchmark and sector score will be wrong")


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
    destroy capital (PF 0.46–0.89 across 2021-2026); a silent unwiring would
    resume entering them without any visible signal.
    """
    REQUIRED_EXCLUDED = {"Financials", "Utilities", "ConsumerStaples"}

    config_path = REPO / "backend/config.py"
    l1_path     = REPO / "backend/gate/lock1_eligibility.py"
    l2_path     = REPO / "backend/gate/lock2_quant.py"

    # 1. config.py defines EXCLUDED_SECTORS with all required sectors
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

    # 2. lock1_eligibility.py: imports EXCLUDED_SECTORS and checks it before regime
    l1_text = l1_path.read_text()
    if "EXCLUDED_SECTORS" not in l1_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock1_eligibility.py",
             "EXCLUDED_SECTORS not imported — excluded sectors bypass L1 entirely")
        return
    excl_pos  = l1_text.find("EXCLUDED_SECTORS")
    regime_pos = l1_text.find("_get_regime_result()")
    if excl_pos > regime_pos:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock1_eligibility.py",
             "EXCLUDED_SECTORS check appears after _get_regime_result() — must fire first")

    # 3. lock2_quant.py: imports SECTOR_THRESHOLD_FLOORS and applies it in _sector_threshold
    l2_text = l2_path.read_text()
    if "SECTOR_THRESHOLD_FLOORS" not in l2_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock2_quant.py",
             "SECTOR_THRESHOLD_FLOORS not imported — ConsumerDisc floor at 0.75 not enforced")
    elif "max(base, floor)" not in l2_text:
        flag(28, "EXCLUDED_SECTORS gate wiring", "CRITICAL",
             "backend/gate/lock2_quant.py",
             "SECTOR_THRESHOLD_FLOORS imported but floor not applied in _sector_threshold")


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
    check while demo enforced dynamic_caps at every execute_trade call. Two same-sector
    tickers qualifying simultaneously would both execute on live with no exposure guard.
    """
    path = REPO / "backend/gate/gate_runner_live.py"
    text = path.read_text()

    # 1. SECTORS imported
    if "from backend.config import" not in text or "SECTORS" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "SECTORS not imported — ticker→sector map cannot be built")
        return

    # 2. sector_exposure_live computed (not the old {} stub)
    if "sector_exposure_live" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "sector_exposure_live not present — sector cap check is missing entirely")
        return
    if '"sector_exposure":  {}' in text or '"sector_exposure": {}' in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "sector_exposure still set to {} stub — real computation not wired")

    # 3. dynamic_caps_live computed
    if "dynamic_caps_live" not in text:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             "dynamic_caps_live not computed — cap values will fall back to flat config only")

    # 4. sector_exposure_live[sector] = projected in BOTH branches (must appear at least twice)
    update_count = text.count("sector_exposure_live[sector] = projected")
    if update_count < 2:
        flag(29, "Live sector exposure cap wiring", "CRITICAL",
             "backend/gate/gate_runner_live.py",
             f"sector_exposure_live update appears {update_count}x — must be in both normal and overflow branches")


def check30():
    """
    Verify startup live regime exit reconciliation is wired in scheduler.py.

    Two structural assertions:
      1. _check_missed_live_exits is defined in scheduler.py.
      2. It is called in start_scheduler() AFTER _check_missed_eod_regime() — ordering
         is load-bearing: regime must be fresh before the exit check fires.

    Prevented by: 2026-05-07 gap analysis. Live regime exit check is an interval job;
    APScheduler does not fire it at scheduler.start(). Open positions could go unprotected
    for EXIT_CHECK_INTERVAL minutes after restart. The startup reconciliation function closes
    this window by calling check_live_regime_exits() immediately after the EOD catch-up.
    """
    path = REPO / "backend/scheduler.py"
    text = path.read_text()

    if "_check_missed_live_exits" not in text:
        flag(30, "Startup live regime exit reconciliation", "CRITICAL",
             "backend/scheduler.py",
             "_check_missed_live_exits not defined — live positions unprotected at startup")
        return

    if "_check_missed_live_exits()" not in text:
        flag(30, "Startup live regime exit reconciliation", "CRITICAL",
             "backend/scheduler.py",
             "_check_missed_live_exits never called in start_scheduler")
        return

    eod_pos = text.find("_check_missed_eod_regime()")
    live_pos = text.find("_check_missed_live_exits()")
    if eod_pos == -1 or live_pos <= eod_pos:
        flag(30, "Startup live regime exit reconciliation", "CRITICAL",
             "backend/scheduler.py",
             "_check_missed_live_exits must be called after _check_missed_eod_regime — "
             "regime result must be fresh before exit check fires")


def check31():
    """
    Verify live bracket orders use GTC TIF and check_live_exits has position-level
    reconciliation fallback.

    Two structural assertions:
      1. place_bracket_order in brokers/alpaca.py uses TimeInForce.GTC, not TimeInForce.DAY.
         DAY TIF expires bracket legs at market close — position sits unprotected from the
         next morning with no exit ever firing.
      2. check_live_exits in live_trades_tracker.py contains the position-reconciliation
         fallback (_find_exit_from_orders). Covers manual closes, expired bracket legs,
         OCA triggers, corporate actions — any close that doesn't appear as a filled leg.

    Prevented by: 2026-05-08. CAT entered April 10, bracket legs expired same day (DAY TIF),
    position sat at +14% for 4 weeks with no exit. All 5 live positions opened April 10
    were affected.
    """
    broker_path  = REPO / "backend/brokers/alpaca.py"
    tracker_path = REPO / "backend/live_trades_tracker.py"

    broker_text  = broker_path.read_text()
    tracker_text = tracker_path.read_text()

    if "TimeInForce.GTC" not in broker_text:
        flag(31, "Live bracket TIF and exit reconciliation", "CRITICAL",
             "backend/brokers/alpaca.py",
             "TimeInForce.GTC not found in place_bracket_order — bracket legs will expire at market close")
        return

    if "TimeInForce.DAY" in broker_text and "place_bracket_order" in broker_text:
        # DAY still present in the function — check it's not in the bracket call
        fn_start = broker_text.find("def place_bracket_order")
        fn_end   = broker_text.find("\ndef ", fn_start + 1)
        fn_body  = broker_text[fn_start:fn_end]
        if "TimeInForce.DAY" in fn_body:
            flag(31, "Live bracket TIF and exit reconciliation", "CRITICAL",
                 "backend/brokers/alpaca.py",
                 "TimeInForce.DAY used in place_bracket_order — must be GTC for bracket legs to persist")
            return

    if "_find_exit_from_orders" not in tracker_text:
        flag(31, "Live bracket TIF and exit reconciliation", "CRITICAL",
             "backend/live_trades_tracker.py",
             "_find_exit_from_orders missing — no fallback for positions closed outside bracket mechanism")
        return

    if "alpaca_positions" not in tracker_text:
        flag(31, "Live bracket TIF and exit reconciliation", "CRITICAL",
             "backend/live_trades_tracker.py",
             "position-reconciliation snapshot (alpaca_positions) missing from check_live_exits")


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

    mechanical_checks = {3, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 24, 25, 26, 27, 28}
    rows = []
    check_names = {
        3: "Fractional qty", 4: "Config parity", 5: "Sector name strings",
        6: "Test DB isolation", 9: "Config value drift",
        10: "Ticker data coverage", 11: "NaN/null pipeline",
        12: "Lock3 context parity", 13: "Undisclosed config change",
        14: "EOD regime freshness", 15: "Calibration freshness",
        16: "yfinance scalar extraction", 17: "Sentiment cache freshness",
        21: "Overflow increment range", 22: "Yahoo data pipeline health",
        24: "Chain-runner wiring", 25: "gate_decision string parity",
        26: "L1/L2 threshold-source parity", 27: "GICS classification parity",
        28: "EXCLUDED_SECTORS gate wiring",
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


# ── CHECK 32 — Git sync divergence ───────────────────────────────────────────

def check32():
    """
    Verify local master is not behind origin/master and working tree is clean.

    Two assertions:
      1. Local master not behind origin/master — nightly agent commits to origin;
         unpushed dev commits cause stale audit/ and wrong frontend last-test date.
         CRITICAL if >3 behind, WARNING if 1–3.
      2. No uncommitted files in working tree — nightly agent may write files without
         committing; those changes silently persist across sessions and are invisible
         to the audit history.

    Prevented by: 2026-05-09. 26 nightly commits accumulated on origin over 25 days
    while 19 dev commits sat unpushed. Extended 2026-05-12: nightly agent wrote 14
    files (frontend + backend) on May 9 without committing; changes sat untracked
    for 3 days.
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/master"],
            cwd=REPO, capture_output=True, text=True, timeout=15,
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        behind = None

    if behind is None:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             "Could not determine commits behind origin/master — fetch may have failed")
        return

    if behind > 3:
        flag(32, "Git sync divergence", "CRITICAL", ".git/",
             f"Local master is {behind} commits behind origin/master — dev commits were not pushed; "
             f"audit/ is stale, frontend will show outdated last-test date")
    elif behind > 0:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             f"Local master is {behind} commit(s) behind origin/master — push dev commits after session close")

    try:
        dirty = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO, capture_output=True, text=True, timeout=15,
        )
        uncommitted = [l for l in dirty.stdout.splitlines() if l.strip()]
        count = len(uncommitted)
    except Exception:
        count = None

    if count is None:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             "Could not count uncommitted changes — git diff HEAD failed")
    elif count > 0:
        flag(32, "Git sync divergence", "WARNING", ".git/",
             f"{count} uncommitted file(s) in working tree — changes will be lost or skipped on next nightly run")


def check33():
    """
    Bayesian multiplier health — silent all-1.0 detection.

    _compute_bayesian_multipliers() can silently return all 1.0 in two
    distinct failure modes:
      A) regime_bayes_result was None (legitimate: no regime data)
      B) ticker_allocations() returned empty/zero (broken: upstream failure)

    Mode A produces no multiplier entries at all (multiplier_count=0).
    Mode B produces entries but every value is 1.0 (all_unity=True).

    The suspicious_cycles counter in the stats file tracks mode B specifically:
    regime was present, ≥3 tickers were queued, but every multiplier was 1.0.

    CRITICAL: any suspicious cycle detected today.
    WARNING: stats file missing or stale on a weekday (runner may have stopped).
    """
    path = REPO / "data" / "bayesian_multiplier_stats.json"
    today = date.today()
    is_trading_day = today.weekday() < 5

    if not path.exists():
        if is_trading_day:
            flag(33, "Bayesian multiplier health", "WARNING",
                 "data/bayesian_multiplier_stats.json",
                 "Stats file missing — _persist_multiplier_stats() not called; "
                 "gate runner may be down or the call was removed from gate_runner.run()")
        return

    try:
        stats = json.loads(path.read_text())
    except Exception as e:
        flag(33, "Bayesian multiplier health", "WARNING",
             "data/bayesian_multiplier_stats.json",
             f"Stats file unreadable: {e}")
        return

    file_date = stats.get("date", "")
    if is_trading_day and file_date != today.isoformat():
        flag(33, "Bayesian multiplier health", "WARNING",
             "data/bayesian_multiplier_stats.json",
             f"Stats file is from {file_date or '(missing)'}, not today ({today.isoformat()}) — "
             "gate runner did not complete a full cycle or _persist_multiplier_stats() was removed")
        return

    suspicious = stats.get("suspicious_cycles", 0)
    if suspicious > 0:
        total = len(stats.get("cycles", []))
        flag(33, "Bayesian multiplier health", "CRITICAL",
             "data/bayesian_multiplier_stats.json",
             f"{suspicious}/{total} gate cycle(s): regime_result present, ≥3 tickers queued, "
             f"but all multipliers=1.0 — ticker_allocations() likely returned zeros or "
             f"sector allocation lookup failed silently; Bayesian sizing had no effect")


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
    check17()
    check21()
    check22()
    check24()
    check25()
    check26()
    check27()
    check28()
    check29()
    check30()
    check31()
    check32()
    check33()
    retirement_candidates = update_registry()
    write_report(retirement_candidates or [])
