"""
Code-health mechanical checks — CHECKs 3, 6, 10, 12, 16, 30, 31, 45, 48, 49,
50, 58, 59, 60, 61, 62, 74, 76.

Covers: fractional qty in broker, test DB isolation, ticker signal data
coverage, Lock3 context key parity, yfinance scalar extraction pattern,
startup live regime exit reconciliation, live bracket TIF/exit fallback,
static code analysis (ruff + connection-leak patterns), L4 yfinance retry
coverage, and — CHECK 74 — a static scan of tests/ for hardcoded dates
exposed to a decaying (>=, <=, <, >, timedelta) wall-clock comparison.
"""
import re

from audit._audit_core import REPO, flag, require_data_file


# ── CHECK 3 — Fractional qty ──────────────────────────────────────────────────

def check3():
    path = REPO / "backend/brokers/alpaca.py"
    if not path.exists():
        return
    text = path.read_text()
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"^\s*qty\s*=", line) and "int(" not in line and "integer" not in line.lower():
            if "notional" in line or "price" in line:
                flag(3, "Fractional qty", "WARNING", f"backend/brokers/alpaca.py:{i}",
                     f"qty assigned without int(): {line.strip()[:60]}")


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


# ── CHECK 10 — Ticker signal data coverage ────────────────────────────────────

def check10():
    db_py    = REPO / "backend/db.py"
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


# ── CHECK 12 — Lock3 context key parity (demo vs live) ───────────────────────

def check12():
    """
    Parse the ctx keys set in _build_base_context() (demo) and _build_base_context() (live)
    using AST and flag any key present in one but absent in the other.
    Known intentional differences are whitelisted.
    """
    import ast

    demo_file = REPO / "backend/gate/gate_runner.py"
    live_file = REPO / "backend/gate/gate_runner_live.py"
    if not demo_file.exists() or not live_file.exists():
        return

    LIVE_ONLY: set = {"mode"}
    DEMO_ONLY: set = set()

    def extract_ctx_keys(source: str, fn_name: str) -> set:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()

        fn_node = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                fn_node = node
                break
        if fn_node is None:
            return set()

        keys: set = set()
        for node in ast.walk(fn_node):
            if (isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Subscript)
                    and isinstance(node.targets[0].value, ast.Name)
                    and node.targets[0].value.id == "ctx"):
                slice_node = node.targets[0].slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    keys.add(slice_node.value)

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


# ── CHECK 16 — yfinance scalar extraction ─────────────────────────────────────

def check16():
    """
    Flag .iloc[-1] applied directly to a yfinance column slice without .flatten().
    Newer yfinance returns multi-column DataFrames even for single tickers — .iloc[-1]
    yields a Series instead of a scalar, silently breaking float() conversion.
    Safe pattern: .values.flatten()[-1]
    """
    _PATTERN = re.compile(r'\[["\']\w*[Cc]lose["\']\]\.iloc\[-1\]')

    for fpath in REPO.rglob("*.py"):
        if any(skip in str(fpath) for skip in ["node_modules", "venv", ".git", "__pycache__"]):
            continue
        text = fpath.read_text(errors="ignore")
        if "yf.download" not in text:
            continue
        if "_slice_history" in text:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _PATTERN.search(line) and ".flatten()" not in line and ".values" not in line:
                context = "\n".join(lines[max(0, i - 6):i - 1])
                if "yf.Ticker" in context:
                    continue
                rel = str(fpath.relative_to(REPO))
                flag(16, "yfinance scalar extraction", "WARNING", f"{rel}:{i}",
                     f"`.iloc[-1]` on column slice without `.flatten()`: {stripped[:70]}")


# ── CHECK 30 — Startup live regime exit reconciliation ───────────────────────

def check30():
    """
    Verify startup live regime exit reconciliation is wired in scheduler.py.

    Two structural assertions:
      1. _check_missed_live_exits is defined in scheduler.py.
      2. It is called in start_scheduler() AFTER _check_missed_eod_regime() — ordering
         is load-bearing: regime must be fresh before the exit check fires.

    Prevented by: 2026-05-07 gap analysis. Live regime exit check is an interval job;
    APScheduler does not fire it at scheduler.start(). Open positions could go
    unprotected for EXIT_CHECK_INTERVAL minutes after restart.
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

    eod_pos  = text.find("_check_missed_eod_regime()")
    live_pos = text.find("_check_missed_live_exits()")
    if eod_pos == -1 or live_pos <= eod_pos:
        flag(30, "Startup live regime exit reconciliation", "CRITICAL",
             "backend/scheduler.py",
             "_check_missed_live_exits must be called after _check_missed_eod_regime — "
             "regime result must be fresh before exit check fires")


# ── CHECK 31 — Live bracket TIF and exit reconciliation ──────────────────────

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
         OCA triggers, corporate actions.

    Prevented by: 2026-05-08. CAT entered April 10, bracket legs expired same day
    (DAY TIF), position sat at +14% for 4 weeks with no exit.
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


# ── CHECK 45 — Static code analysis ─────────────────────────────────────────

def check45():
    """
    Two sub-checks run on production backend (excludes backtest/).

    Sub-check A — ruff (E, F, W rules, ignore E501):
      Any finding in backend/ outside of backtest/ is WARNING. Catches unused
      imports, undefined names, unused variables, and f-string issues that
      slip through review. Backtest scripts are excluded — they are analysis
      tooling, not runtime code.

    Sub-check B — Connection leak pattern:
      `with get_db() as conn:` in any .py file outside tests/. sqlite3's
      context manager only manages transactions — it never closes the connection.
      Only the try/finally conn.close() pattern is safe. Each match is WARNING.

    Prompted by: 2026-05-29 manual audit found two leaked connections in
    scheduler.py (_check_missed_eod_regime, _check_missed_sentiment_prefetch).
    """
    import subprocess
    import sys

    # Sub-check A — ruff
    # sys.executable, not "python3": under the scheduler the bare name resolved
    # to the venv's python, which had no ruff — rc 1, empty stdout, zero
    # findings, no flag. Every scheduled run of this sub-check before 2026-09-16
    # evaluated nothing and rendered clean (13 findings appeared the first time
    # it ran with ruff on the path).
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "backend/",
             "--select", "E,F,W", "--ignore", "E501",
             "--output-format", "concise"],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        if result.returncode not in (0, 1) or (result.returncode == 1 and not result.stdout.strip()):
            flag(45, "Static code analysis", "WARNING", "audit/checks_code.py",
                 f"ruff did not run (rc={result.returncode}): {result.stderr.strip()[:160] or 'no output'} "
                 f"— sub-check A evaluated nothing")
        for line in result.stdout.splitlines():
            # Skip backtest/ — analysis scripts, not runtime code
            if "/backtest/" in line or "\\backtest\\" in line:
                continue
            # ruff concise format: path:line:col: CODE message
            if re.match(r"^backend/.*\.py:\d+:\d+:", line):
                parts = line.split(":", 3)
                file_line = f"{parts[0]}:{parts[1]}"
                detail    = parts[3].strip() if len(parts) > 3 else line
                flag(45, "Static code analysis", "WARNING", file_line,
                     f"ruff: {detail}")
    except FileNotFoundError:
        flag(45, "Static code analysis", "WARNING", "audit/checks_code.py",
             "ruff not found — install via pip install ruff")
    except Exception as e:
        flag(45, "Static code analysis", "WARNING", "audit/checks_code.py",
             f"ruff sub-check failed: {e}")

    # Sub-check B — connection leak pattern
    _LEAK = re.compile(r"\bwith\s+get_db\(\)\s+as\b")
    for fpath in REPO.rglob("*.py"):
        if any(skip in str(fpath) for skip in ["node_modules", "venv", ".git", "__pycache__", "tests/", "/audit/"]):
            continue
        try:
            for i, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
                if _LEAK.search(line) and not line.strip().startswith("#"):
                    rel = str(fpath.relative_to(REPO))
                    flag(45, "Static code analysis", "WARNING", f"{rel}:{i}",
                         "`with get_db() as conn` — sqlite3 context manager never closes "
                         "the connection; use try/finally conn.close()")
        except Exception:
            continue


def check48():
    """
    Verify that all three yfinance fetch paths in lock4_leading.py have retry
    loops to handle silently-broken responses from the shared curl_cffi session.

    Three paths must each show `for attempt in range(2)`:
      1. _fetch_options_chains  — silent empty t.options under concurrent load
      2. _check_relative_strength — partial batch response (ticker column absent)
      3. _check_volume_accumulation — wrong-ticker columns (curl_cffi session collision)

    Prevented by: 2026-06-02 incident where AVGO failed all four L4 sub-checks
    repeatedly. Root cause: shared BoringSSL TLS singleton returns stale/partial
    data under concurrent yfinance load. Retry paths close the false-negative loop.
    """
    lock4 = REPO / "backend/gate/lock4_leading.py"
    if not lock4.exists():
        flag(48, "L4 yfinance retry coverage", "CRITICAL", str(lock4),
             "lock4_leading.py not found")
        return

    src = lock4.read_text()

    # Each function must contain its own retry loop
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        flag(48, "L4 yfinance retry coverage", "CRITICAL", str(lock4),
             f"syntax error parsing lock4_leading.py: {e}")
        return

    # Extract function bodies as source slices by line range
    funcs: dict[str, str] = {}
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            end = getattr(node, "end_lineno", None)
            if end:
                funcs[node.name] = "\n".join(lines[node.lineno - 1: end])

    # Any retry count >= 2 satisfies the intent; the literal range(2) match
    # went CRITICAL on 2026-07-16's widening to range(3) (found 2026-09-11).
    retry_pattern = re.compile(r"for attempt in range\(([2-9]|\d{2,})\)")
    missing = []
    for fn in ("_fetch_options_chains", "_check_relative_strength", "_check_volume_accumulation"):
        body = funcs.get(fn, "")
        if not retry_pattern.search(body):
            missing.append(fn)

    if missing:
        flag(48, "L4 yfinance retry coverage", "CRITICAL",
             "backend/gate/lock4_leading.py",
             f"retry loop missing from: {', '.join(missing)} — "
             "silent yfinance failures under concurrent load will surface as permanent sub-check failures")


def check49():
    """
    Verify Lock 4 uses group-constraint logic (not flat min_pass) and that
    price-check error paths carry "error": True.

    The MSFT/June-2026 post-mortem showed that a flat 2-of-4 pass threshold
    allowed options-only passes when both price checks were erroring — the gate
    claimed "leading signals confirmed" while both price-based sub-checks had
    data errors. Fix: price group (RS + VA) and options group (PCR + UC) must
    each pass independently; any error in the price group fails the price group.

    Sub-check A: evaluate() contains group-constraint variables
      (price_group_pass and options_group_pass).
    Sub-check B: error return paths in _check_relative_strength and
      _check_volume_accumulation carry "error": True so the group logic can
      distinguish data errors from genuine signal failures.
    """
    lock4 = REPO / "backend/gate/lock4_leading.py"
    if not lock4.exists():
        flag(49, "L4 group constraint", "CRITICAL", str(lock4),
             "lock4_leading.py not found")
        return

    src = lock4.read_text()

    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        flag(49, "L4 group constraint", "CRITICAL", str(lock4),
             f"syntax error parsing lock4_leading.py: {e}")
        return

    lines = src.splitlines()
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            end = getattr(node, "end_lineno", None)
            if end:
                funcs[node.name] = "\n".join(lines[node.lineno - 1: end])

    # Sub-check A: group variables present in evaluate()
    evaluate_body = funcs.get("evaluate", "")
    missing_vars = [v for v in ("price_group_pass", "options_group_pass")
                    if v not in evaluate_body]
    if missing_vars:
        flag(49, "L4 group constraint", "CRITICAL",
             "backend/gate/lock4_leading.py",
             f"group constraint variables missing from evaluate(): {', '.join(missing_vars)} — "
             "flat min_pass threshold reinstated; options-only passes will be approved when price checks error")

    # Sub-check B: error flag in price-check functions
    for fn in ("_check_relative_strength", "_check_volume_accumulation"):
        body = funcs.get(fn, "")
        if '"error": True' not in body and "'error': True" not in body:
            flag(49, "L4 group constraint", "CRITICAL",
                 "backend/gate/lock4_leading.py",
                 f"{fn}() missing '\"error\": True' on data-error return paths — "
                 "group logic cannot distinguish data errors from signal failures")


def check50():
    """
    Verify no recent TRADE_EXECUTED entry passed L4 on a single group.

    The 2026-06-02 CRM/NVDA/FDX incident: flat 2-of-4 min_pass let through
    entries where the price group (RS+VA) or options group (PCR+UC) was
    entirely failing. NVDA had RS+VA both fail; FDX had PCR+UC both fail.
    Fix (d70ab53): group constraint requires both groups to pass.

    CHECK 49 verifies the code has the group constraint. This check verifies
    the runtime data: no executed trade in the last 30 trading days has
    both price sub-checks failing OR both options sub-checks failing.

    A failure here means either: (a) the group constraint code was bypassed,
    (b) a regression to flat min_pass, or (c) a data serialisation bug where
    lock_leading_checks doesn't match the actual pass decision.
    """
    import json
    import sqlite3

    db_path = REPO / "data" / "apex.db"
    if not require_data_file(50, "L4 group constraint (live data)", db_path):
        return

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT ticker, timestamp, lock_leading_checks
            FROM live_gate_history
            WHERE gate_decision = 'TRADE_EXECUTED'
              AND lock_leading_pass = 1
              AND lock_leading_checks IS NOT NULL
              AND timestamp >= '2026-06-03'
            ORDER BY timestamp DESC
            """
        ).fetchall()
    finally:
        con.close()

    for ticker, ts, checks_json in rows:
        try:
            checks = json.loads(checks_json)
        except (json.JSONDecodeError, TypeError):
            continue

        rs_pass   = bool(checks.get("relative_strength",   {}).get("pass", False))
        va_pass   = bool(checks.get("volume_accumulation", {}).get("pass", False))
        pcr_pass  = bool(checks.get("put_call_ratio",      {}).get("pass", False))
        uc_pass   = bool(checks.get("unusual_calls",       {}).get("pass", False))

        price_group   = rs_pass or va_pass
        options_group = pcr_pass or uc_pass

        if not price_group:
            flag(50, "L4 group constraint (live data)", "CRITICAL",
                 "data/apex.db:live_gate_history",
                 f"{ticker} @ {ts[:10]}: TRADE_EXECUTED with both price sub-checks failing "
                 f"(RS={rs_pass}, VA={va_pass}) — group constraint bypassed or code regressed")
        elif not options_group:
            flag(50, "L4 group constraint (live data)", "CRITICAL",
                 "data/apex.db:live_gate_history",
                 f"{ticker} @ {ts[:10]}: TRADE_EXECUTED with both options sub-checks failing "
                 f"(PCR={pcr_pass}, UC={uc_pass}) — group constraint bypassed or code regressed")


# ── CHECK 58 — Gate-result insert call-site field completeness ───────────────

def check58():
    """
    Every call site of insert_demo_gate_result() / insert_live_gate_result()
    must supply all five fields that db.py added defaults for piecemeal
    (ticker_signal, earnings_near, days_to_earnings, lock3_sentiment_score,
    lock3_conviction) — db.py's per-field .get() fallback only covers the
    fields someone remembered to add there; a call site missing a field
    that db.py does NOT default raises sqlite3.ProgrammingError and kills
    the whole gate cycle silently (2026-06-23: demo's skipped-ticker call
    site omitted earnings_near/days_to_earnings, added by 6d9f497 12 days
    earlier with no fallback — every cooloff/held ticker crashed run()).

    AST-parses each insert_*_gate_result(...) call site's dict literal arg
    and flags any missing required key.
    """
    import ast

    REQUIRED_KEYS = {
        "ticker_signal", "earnings_near", "days_to_earnings",
        "lock3_sentiment_score", "lock3_conviction",
    }
    targets = {
        "insert_demo_gate_result": REPO / "backend/gate/gate_runner.py",
        "insert_live_gate_result": REPO / "backend/gate/gate_runner_live.py",
    }

    for fn_name, path in targets.items():
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == fn_name and node.args
                    and isinstance(node.args[0], ast.Dict)):
                continue
            present = {
                k.value for k in node.args[0].keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            missing = REQUIRED_KEYS - present
            if missing:
                flag(58, "Gate-result insert field completeness", "CRITICAL",
                     f"{path.relative_to(REPO)}:{fn_name}() call at line {node.lineno}",
                     f"missing key(s) {sorted(missing)} — will raise sqlite3.ProgrammingError "
                     "if db.py has no fallback default for any of them")


# ── CHECK 59 — Alpaca SDK imports must be module-level ───────────────────────

def check59():
    """
    backend/brokers/alpaca.py must import alpaca.trading.* at module load
    time, not inside function bodies. Per-function lazy imports let
    concurrent threads (gate runner's ThreadPoolExecutor + FastAPI request
    threads polling /live/account, /live/positions, /live/orders) race to
    import the same submodule for the first time, which deadlocks CPython's
    per-module import lock and poisons the process until restart
    (2026-06-23: live gate stopped firing after 18:55, traced to a deadlock
    on "_ModuleLock('alpaca.trading.enums')"). alpaca-py is an unconditional
    dependency (requirements.txt) — there is no startup-safety reason for
    the lazy pattern.
    """
    path = REPO / "backend/brokers/alpaca.py"
    if not path.exists():
        return

    for i, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("from alpaca.") or stripped.startswith("import alpaca."):
            if line[0] in (" ", "\t"):  # indented = inside a function body
                flag(59, "Alpaca SDK imports must be module-level", "CRITICAL",
                     f"backend/brokers/alpaca.py:{i}",
                     f"'{stripped}' is imported inside a function — move to module top-level "
                     "to eliminate the concurrent-first-import deadlock")


# ── CHECK 60 — Lock 4 yfinance futures must specify a timeout ────────────────

def check60():
    """
    Every fut.result(...) call in lock4_leading.py's evaluate() must pass
    an explicit timeout=. yf.Ticker().options/.option_chain() take no
    timeout parameter, and curl_cffi's shared BoringSSL singleton has been
    observed to hang outright under concurrent load rather than raise.
    Without a bounded .result(), one hung ticker blocks
    ThreadPoolExecutor.shutdown(wait=True) forever — no further gate
    cycles run (2026-06-23 incident; see CHECK 7 file scope note).
    """
    import ast

    path = REPO / "backend/gate/lock4_leading.py"
    if not path.exists():
        return
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return

    fn_node = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "evaluate"),
        None,
    )
    if fn_node is None:
        return

    for node in ast.walk(fn_node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "result"
                and not any(kw.arg == "timeout" for kw in node.keywords)):
            flag(60, "Lock 4 futures must specify a timeout", "CRITICAL",
                 f"backend/gate/lock4_leading.py:{node.lineno}",
                 ".result() called without timeout= — a hung yfinance call can "
                 "block the gate cycle forever")


def check61():
    """
    All exit-path outcome classifications in wallet.py and
    live_trades_tracker.py must use >= 0 (not > 0) to classify WIN.

    The asymmetry bug (check_regime_exits used > 0, check_exits used >= 0)
    caused identical zero-PnL trades to be recorded as WIN or LOSS depending
    on which exit mechanism fired, distorting win-rate and performance metrics.
    Fixed 2026-06-25. This check prevents regression and catches the same
    class of bug in live_trades_tracker.py.
    """
    import re

    targets = [
        REPO / "backend/wallet.py",
        REPO / "backend/live_trades_tracker.py",
    ]
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r'outcome\s*=\s*"WIN"\s+if\s+pnl_pct\s*>\s*0', line):
                flag(61, "Breakeven outcome must use >= 0 not > 0", "HIGH",
                     f"{path.relative_to(REPO)}:{lineno}",
                     "pnl_pct > 0 classifies breakeven trades as LOSS; use >= 0")


def check62():
    """
    Lock 4's evaluate() and data-fetch paths must retain two concurrency guards:
      1. NoneType guard: check results accessed via `v and v.get("pass")` not
         bare `v["pass"]`; ThreadPoolExecutor may return None if a check function
         exits via an unhandled exception path (2026-06-30 incident).
      2. MultiIndex guard: `isinstance(raw.columns, ...MultiIndex)` + xs()
         normalization; concurrent yfinance session reuse can inject MultiIndex
         column structure into single-ticker downloads (2026-06-30 incident).
    Note: checks that the known guard patterns persist; does not cover novel
    None-producing escape paths that bypass evaluate()'s aggregation layer.
    """
    import re

    path = REPO / "backend/gate/lock4_leading.py"
    if not path.exists():
        return
    text = path.read_text()

    if not re.search(r'v\s+and\s+v\.get\(', text):
        flag(62, "Lock 4 NoneType guard missing", "CRITICAL",
             "backend/gate/lock4_leading.py",
             "v and v.get() guard absent from evaluate() — bare v['pass'] crashes "
             "on None futures results from ThreadPoolExecutor")

    if not re.search(r'isinstance\(.*\.columns.*MultiIndex\)', text):
        flag(62, "Lock 4 MultiIndex normalization missing", "CRITICAL",
             "backend/gate/lock4_leading.py",
             "isinstance(raw.columns, MultiIndex) guard absent — concurrent yfinance "
             "session reuse can inject MultiIndex structure into single-ticker downloads")


def check74():
    """
    Static scan for the decaying-comparison test shape (2026-09-10, design
    review of recovered/activities-reader): a test hardcodes a past date,
    and backend code compares a value derived from it against
    date.today()/datetime.now() using >=, <=, <, >, or timedelta arithmetic
    — not ==. As real days pass, the fixed date silently drifts across
    that threshold and the test starts exercising a different branch with
    no error anywhere. Three confirmed instances the same session: three
    TestReconciliationFreeze tests (max_hold_days=25) whose entry date
    aged past its window and started hitting the real, unmocked Alpaca
    endpoint instead of the mocked reconciliation path they were written
    to test.

    Deliberately narrow to the decaying operators, not "any hardcoded date
    near a wall-clock read" — a bare == against a fixed past date can only
    ever coincidentally match (astronomically unlikely, and once the
    calendar passes it, permanently safe going forward, never drifting
    back toward a false positive). Flagging that shape too would be the
    same false-positive-erodes-trust failure the lint's exact-match
    ghost-compile detector already produced once this month — see
    2026-09-01 in the vault.

    Two-pass, both static, no runtime dependency. Backend-side pass has two
    tiers, because the real 2026-09-10 instance split the clock read and
    the decaying comparison across two functions — `_trading_days_since()`
    reads date.today() internally with no comparison of its own; the
    decaying `>= max_hold_days` happens one level up, in its caller, against
    that function's *return value*, not against date.today() by name:
      Tier A (direct): a function whose OWN body contains both a wall-clock
        read (date.today()/datetime.now()) and a decaying comparison (>=,
        <=, <, >, or timedelta) against it.
      Tier B (indirect): a function that calls a Tier-A-or-clock-reading
        function and applies a decaying comparison directly to that call's
        result (`some_func(...) >= x` or `x <= some_func(...)`).
    Then: scan tests/ for files that hardcode an absolute ISO date/datetime
    literal AND call one of those specific flagged function names (as
    `name(` or `.name(`, not just the substring anywhere in the file — an
    earlier, module-granularity version of this check flagged
    test_gate_runners.py for merely mentioning "scheduler" in an unrelated
    patch target, the exact false-positive-by-coincidence shape this check
    exists to avoid producing itself), with no
    patch(...date.today...)/patch(...datetime.now...) in the same file (a
    patched clock isn't exposed to real elapsed time).

    File-level granularity for the finding itself, not per-test-function —
    a human triages from there; the check's job is to make sure this class
    doesn't reopen silently the next time a new test picks a fixed date.
    """
    import ast

    _CLOCK = r'date\.today\(\)|datetime\.now\(\)'
    # Deliberately requires the operator (or the timedelta arithmetic) to
    # sit directly against the clock read itself, not just co-occur
    # anywhere in a large function — a backtest engine's run() uses
    # timedelta pervasively for synthetic historical dates with zero
    # relation to real elapsed time; a bare "timedelta appears somewhere"
    # trigger flagged exactly that, a second false-positive shape caught
    # while building this check.
    decay_op = re.compile(
        rf'(>=|<=|<(?!=)|>(?!=))\s*({_CLOCK})'
        rf'|({_CLOCK})\s*(>=|<=|<(?!=)|>(?!=))'
        rf'|({_CLOCK})\s*[-+]\s*timedelta'
    )
    clock_read = re.compile(_CLOCK)

    # First sub-pass: which functions read the clock at all (Tier A pool +
    # the callees Tier B looks for), and which functions are Tier-A
    # decaying on their own.
    clock_reading_functions = set()
    tier_a = set()
    function_sources: dict[str, str] = {}
    backend_dir = REPO / "backend"
    if backend_dir.exists():
        for path in backend_dir.rglob("*.py"):
            try:
                source = path.read_text(errors="ignore")
                tree = ast.parse(source)
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                segment = ast.get_source_segment(source, node) or ""
                function_sources[node.name] = segment
                if clock_read.search(segment):
                    clock_reading_functions.add(node.name)
                    if decay_op.search(segment):
                        tier_a.add(node.name)

    # Second sub-pass: Tier B — a function calling one of the clock-reading
    # functions and comparing that call's result with a decaying operator,
    # even though the caller's own body never mentions date.today()/
    # datetime.now() by name.
    tier_b = set()
    if clock_reading_functions:
        for name in clock_reading_functions:
            call_adjacent_compare = re.compile(
                rf'{re.escape(name)}\([^)]*\)\s*(>=|<=|<(?!=)|>(?!=))'
                rf'|(>=|<=|<(?!=)|>(?!=))\s*{re.escape(name)}\('
            )
            for caller, segment in function_sources.items():
                if caller == name:
                    continue
                if call_adjacent_compare.search(segment):
                    tier_b.add(caller)

    decaying_functions = tier_a | tier_b
    if not decaying_functions:
        return

    iso_date   = re.compile(r'"20\d\d-\d\d-\d\d')
    clock_mock = re.compile(r'patch\([^)]*(date\.today|datetime\.now)')
    tests_dir  = REPO / "tests"
    if not tests_dir.exists():
        return

    call_patterns = {name: re.compile(rf'[.\s]{re.escape(name)}\s*\(')
                      for name in decaying_functions}

    for path in tests_dir.glob("test_*.py"):
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        if not iso_date.search(text):
            continue
        referenced = sorted(n for n, pat in call_patterns.items() if pat.search(text))
        if not referenced:
            continue
        if clock_mock.search(text):
            continue  # clock is patched somewhere in this file — not exposed
        rel = str(path.relative_to(REPO))
        flag(74, "Decaying date-comparison test shape", "WARNING", rel,
             f"hardcoded absolute date(s) alongside a call into decaying "
             f"wall-clock function(s) {', '.join(referenced)} — verify this "
             f"still exercises the intended branch, not one it drifted into")


# ── CHECK 76 — Backtest engine parity (engine.py vs engine_fast.py) ────────────
#
# backend/backtest/PRODUCTION_GAP.md is the *expected* divergence between the two
# engines; this check asserts the actual divergence equals it. Accepted rows stay
# accepted; anything new fails. engine_fast.py:9 says "drop-in replacement" — the
# first run of this check (2026-09-16) showed it is not: the slow engine always
# hard-blocks entries on FOMC/CPI/NFP days (engine.py:330, unconditional), the
# fast engine never does; three further filters are slow-only behind flags.

# Layer 1 — run() parameters present in exactly one engine. Adding a parameter to
# one engine without the other is a new divergence and must be recorded here
# and in PRODUCTION_GAP.md before this list is extended.
CHECK76_ACCEPTED_SLOW_ONLY = {"etf_negative_floor", "etf_negative_penalty",
                             "macro_hard_block", "macro_pre_event_penalty"}
CHECK76_ACCEPTED_FAST_ONLY = {"precomputed"}

# Layer 2 — production components documented ABSENT from engine_fast.py
# (PRODUCTION_GAP.md rows). If a symbol appears, the row is stale, not wrong —
# update the inventory, then this list.
CHECK76_ABSENT_FROM_FAST = {
    "row 2/5/20/21 RegimeBayes":  r"regime_bayes|RegimeBayes",
    "row 7/10 macro calendar":    r"_macro_status|_FOMC_DATES|macro_hard_block",
    "row 11 ETF negative penalty": r"etf_negative_penalty",
    "row 13 watchlist discount":  r"watchlist",
    "row 14 overflow slots":      r"overflow",
}

# Layer 3 — behavioural: both engines over one fixed window with the documented
# divergences neutralised must produce identical trade logs. Window is short
# enough to run on cached Parquet in ~40 s; the cache is host-only, so this
# layer is SKIPPED in CI. Fixed on purpose — a moving window is not a test.
CHECK76_WINDOW = ("2026-03-02", "2026-05-29")


def _check76_run_params(path):
    import ast
    tree = ast.parse(path.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    return {a.arg for a in fn.args.args + fn.args.kwonlyargs}


def _check76_trade_key(t):
    return (t["ticker"], t["entry_date"], t["exit_date"], t["exit_reason"],
            round(t["amount"], 2), round(t["pnl"], 2))


def check76():
    name = "backtest engine parity"
    slow_p = REPO / "backend/backtest/engine.py"
    fast_p = REPO / "backend/backtest/engine_fast.py"
    if not (slow_p.exists() and fast_p.exists()):
        return

    # Layer 1
    slow, fast = _check76_run_params(slow_p), _check76_run_params(fast_p)
    new_slow = (slow - fast) - CHECK76_ACCEPTED_SLOW_ONLY
    new_fast = (fast - slow) - CHECK76_ACCEPTED_FAST_ONLY
    gone     = (CHECK76_ACCEPTED_SLOW_ONLY - (slow - fast)) | (CHECK76_ACCEPTED_FAST_ONLY - (fast - slow))
    if new_slow or new_fast:
        flag(76, name, "CRITICAL", "backend/backtest/engine.py:run",
             f"undocumented run() divergence — slow-only {sorted(new_slow)}, fast-only {sorted(new_fast)}; "
             f"record in PRODUCTION_GAP.md and CHECK76_ACCEPTED_* or make the engines agree")
    if gone:
        flag(76, name, "WARNING", "audit/checks_code.py:CHECK76_ACCEPTED_SLOW_ONLY",
             f"accepted divergence no longer present: {sorted(gone)} — inventory row stale")

    # Layer 2
    fast_src = fast_p.read_text()
    for row, pat in CHECK76_ABSENT_FROM_FAST.items():
        if re.search(pat, fast_src):
            flag(76, name, "WARNING", "backend/backtest/PRODUCTION_GAP.md",
                 f"{row}: documented absent from engine_fast.py but pattern /{pat}/ now matches — "
                 f"row is stale; update the inventory and CHECK76_ABSENT_FROM_FAST")

    # Layer 3 — host only
    import hashlib
    from datetime import date, timedelta
    try:
        from backend.ticker_config import get_sectors
        from backend.config import SPY_TICKER
    except Exception as e:
        flag(76, name, "WARNING", "backend/ticker_config.py", f"could not import universe: {e} — layer 3 did not evaluate")
        return
    sectors = get_sectors()
    tickers = [SPY_TICKER, "^VIX"] + [c["etf"] for c in sectors.values()] + [t for c in sectors.values() for t in c["tickers"]]
    start, end = date.fromisoformat(CHECK76_WINDOW[0]), date.fromisoformat(CHECK76_WINDOW[1])
    lookback = start - timedelta(days=130)   # both engines use the same 130-day buffer
    key = hashlib.md5((",".join(sorted(tickers)) + f"|{lookback}|{end}").encode()).hexdigest()[:12]
    cache = REPO / "data" / "backtest_cache" / f"{key}.parquet"
    if not require_data_file(76, name, cache,
                             hint="run either engine once over CHECK76_WINDOW on the host to build it"):
        return
    try:
        from backend.backtest import engine as slow_e, engine_fast as fast_e
        # One dataset for both engines. Each engine downloads and caches its own
        # Parquet; yfinance adjusted closes drift between fetches, and two
        # fetches produced a uniform 0.0002 score offset on the first run of
        # this layer. The slow engine's downloader is patched to return the
        # fast engine's frame so the comparison is code-only.
        pre = fast_e.precompute(*CHECK76_WINDOW)
        _orig_dl, _orig_macro = slow_e._download_all, slow_e._macro_status
        slow_e._download_all = lambda *_a, **_k: pre["raw_data"]
        # Neutralise the documented, unconditional slow-only divergence (row 7):
        # macro calendar returns "clear" for every day of the parity run.
        slow_e._macro_status = lambda _d: "clear"
        try:
            rs = slow_e.run(*CHECK76_WINDOW)
        finally:
            slow_e._download_all, slow_e._macro_status = _orig_dl, _orig_macro
        rf = fast_e.run(*CHECK76_WINDOW, precomputed=pre)
    except Exception as e:
        flag(76, name, "WARNING", "backend/backtest/engine.py", f"parity run failed: {e} — layer 3 did not evaluate")
        return
    ks = {_check76_trade_key(t) for t in rs["trade_log"]}
    kf = {_check76_trade_key(t) for t in rf["trade_log"]}
    if ks != kf:
        only_s, only_f = sorted(ks - kf), sorted(kf - ks)
        first = min(only_s + only_f, key=lambda k: k[1]) if (only_s or only_f) else None
        flag(76, name, "CRITICAL", "backend/backtest/engine_fast.py:9",
             f"engines diverge on {CHECK76_WINDOW[0]}→{CHECK76_WINDOW[1]} with documented divergences "
             f"neutralised: slow {len(ks)} trades, fast {len(kf)}, common {len(ks & kf)}; "
             f"first differing entry {first} — an undocumented model difference, not a PRODUCTION_GAP.md row")


_C79_MARKER = re.compile(
    r"(?i)\b(?:interim|provisional|temporar(?:y|ily)|unvalidated|stopgap)\b"
    r"|\bonce\s+\w+\s+has\b|\breplaces?\s+the\s+interim\b|\breplace\s+with\b"
    r"|\brevisit\s+(?:after|in|once|when)\b|\buntil\s+\w+\s+accumulates\b"
)
# Gate forms found by reading the comment corpus (2026-09-16 rejects pass): "once <table> has
# N[-M] weeks", "after N-M weeks of data", "revisit after 3-6 months of live data". Age comes
# from the named table's MIN(date) when there is one, else from a YYYY-MM-DD in the same block.
_C79_GATE   = re.compile(
    r"(?i)(?:once\s+(?P<table>\w+)\s+has|after|in)\s+(?:[≥>]=?\s*|~\s*)?(?P<lo>\d+)(?:\s*[-–]\s*(?P<hi>\d+))?\s*(?P<unit>weeks?|months?)\b"
)
_C79_DATE   = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_C79_TABLE  = re.compile(r"\b(\w+_history)\b")
_C79_SCAN   = ("backend", "audit", "scripts")
_C79_SKIP   = ("audit/checks_code.py",)
_C79_TRIPLE = ('"' * 3, "'" * 3)


def _c79_comment_lines(path):
    """(lineno, text) for every comment or docstring line in a .py file —
    markers live in comments and docstrings, never in identifiers."""
    import tokenize
    out = []
    try:
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.COMMENT:
                    out.append((tok.start[0], tok.string))
                elif tok.type == tokenize.STRING and any(t in tok.string for t in _C79_TRIPLE):
                    for i, line in enumerate(tok.string.splitlines()):
                        out.append((tok.start[0] + i, line))
    except (tokenize.TokenError, SyntaxError, OSError):
        pass
    return out


def _c79_blocks(lines):
    """Group (lineno, text) pairs into contiguous comment/docstring blocks so a
    gate stated one line after its marker ("Interim: replace with X" / "once
    <table> has 4-8 weeks") is read as one statement."""
    blocks, cur = [], []
    for ln, text in lines:
        if cur and ln > cur[-1][0] + 1:
            blocks.append(cur); cur = []
        cur.append((ln, text))
    if cur:
        blocks.append(cur)
    return blocks


def _c79_weeks_of(table):
    """Weeks of data in a history table, or None if the DB/table is absent."""
    import sqlite3
    from datetime import date
    db = REPO / "data/apex.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(db)
        row = conn.execute(f"SELECT MIN(date) FROM {table}").fetchone()
        conn.close()
    except Exception:
        return None
    if not row or not row[0]:
        return 0.0
    return (date.today() - date.fromisoformat(row[0][:10])).days / 7


def _c79_uncalled_accessors(table):
    """get_* functions anywhere in the repo whose body reads the gate table and
    that are called nowhere — the tell that the replacement was never written
    (get_pcr_history in backend/db.py reads lock4_pcr_history; no caller,
    2026-09-16)."""
    sources = {}
    for sub in _C79_SCAN:
        for f in (REPO / sub).rglob("*.py"):
            if "tests" in f.parts or "__pycache__" in f.parts:
                continue
            sources[f] = f.read_text(encoding="utf-8", errors="replace")
    readers = set()
    for txt in sources.values():
        for m in re.finditer(r"^\s*def (get_\w+)\(.*?(?=^\s*def |\Z)", txt, re.M | re.S):
            if table in m.group(0):
                readers.add(m.group(1))
    uncalled = []
    for n in sorted(readers):
        called = sum(len(re.findall(rf"(?<!def )\b{n}\(", txt)) for txt in sources.values())
        if not called:
            uncalled.append(n)
    return uncalled


def check79():
    """
    Deferred-replacement markers past their gate.

    A code comment that says "interim: replace with X once <table> has N weeks"
    is a commitment with no surfacing mechanism — nothing reads comments on a
    schedule. Found 2026-09-16 by one grep: PCR_THRESHOLD=0.85 (lock4_leading.py,
    gate 4–8 weeks, 17 weeks old, replacement never written — get_pcr_history has
    no caller), regime-bucket _WEIGHTS (aggregator.py) and bull/bear thresholds
    (regime_bayes.py), both gated on ≥4 weeks of sector_posterior_history at 16
    weeks with no calibration code. Three markers, one shared gate, all past it.

    Scans comments and docstrings under backend/, audit/, scripts/ for marker
    vocabulary. Where the gate is machine-readable ("once <table> has N[-M]
    weeks") the table's age is read from apex.db and the marker is flagged when
    past the upper bound: WARNING, CRITICAL beyond 2x the bound. A get_* accessor
    named beside the marker that is defined but never called is reported as the
    unbuilt-replacement tell. Markers with no machine-readable gate are listed
    in the finding text when any finding fires, and are otherwise not surfaced.
    Vocabulary was grown from the corpus, not the author: on 2026-09-16 the
    6,662 comment/docstring lines the first regex rejected were probed for
    deferral vocabulary (67 hits read); three genuine markers had been missed
    ("revisit after 3-6 months", "unvalidated ... until N accumulates",
    "Recalibrate if market regime changes") and the first two forms were added
    with month units and a dated-block age source. The third (a condition, not
    a gate) stays ungated by design. Gate ages need apex.db; without it the
    check is SKIPPED, not a pass.
    """
    name = "Deferred-replacement markers past their gate"
    if not require_data_file(79, name, REPO / "data/apex.db"):
        return
    past, ungated = [], []
    for sub in _C79_SCAN:
        for f in sorted((REPO / sub).rglob("*.py")):
            rel = f.relative_to(REPO).as_posix()
            if rel in _C79_SKIP or "tests" in f.parts or "__pycache__" in f.parts:
                continue
            for block in _c79_blocks(_c79_comment_lines(f)):
                hits = [ln for ln, text in block if _C79_MARKER.search(text)]
                if not hits:
                    continue
                lineno = hits[0]
                block_text = " ".join(text for _, text in block)
                g = _C79_GATE.search(block_text)
                table = (g.group("table") if g and g.group("table") else None) or \
                        (_C79_TABLE.search(block_text).group(1) if _C79_TABLE.search(block_text) else None)
                if not g:
                    ungated.append(f"{rel}:{lineno}")
                    continue
                hi = int(g.group("hi") or g.group("lo"))
                if g.group("unit").lower().startswith("month"):
                    hi = round(hi * 52 / 12)
                age = _c79_weeks_of(table) if table else None
                if age is None:
                    d = _C79_DATE.search(block_text)
                    if d:
                        from datetime import date as _date
                        age = (_date.today() - _date.fromisoformat(d.group(1))).days / 7
                        table = f"dated {d.group(1)}"
                if age is None:
                    ungated.append(f"{rel}:{lineno} (gate stated, no table or date to age it from)")
                    continue
                if age > hi:
                    past.append((rel, lineno, table, hi, age, _c79_uncalled_accessors(table) if not table.startswith('dated ') else []))
    if not past:
        return
    # Event vs level (2026-09-16): a marker that crossed its gate within the last
    # week is the event — CRITICAL once, while it is news. A marker 17 weeks past
    # a 4–8 week gate is a standing decision item (INDEX.md commitment) and reads
    # WARNING; CRITICAL beyond 2x the bound made every scheduled run repeat it.
    sev = "CRITICAL" if any(hi < age <= hi + 1 for _, _, _, hi, age, _ in past) else "WARNING"
    items = []
    for rel, lineno, table, hi, age, uncalled in past:
        tell = f"; {', '.join(uncalled)} defined but never called" if uncalled else ""
        items.append(f"{rel}:{lineno} gated on {table} >={hi}w, now {age:.0f}w{tell}")
    extra = (f" {len(ungated)} further marker(s) carry no machine-readable gate: "
             f"{', '.join(ungated[:6])}{' ...' if len(ungated) > 6 else ''}.") if ungated else ""
    flag(79, name, sev, "backend/,audit/,scripts/ (comments and docstrings)",
         f"{len(past)} deferred replacement(s) past their stated gate — {'; '.join(items)}. "
         f"A marker past its gate is a decision item, not a build order: the design "
         f"intent may predate what the series now shows (PCR 25% design vs 58.4% realised, "
         f"2026-09-16). Decide, re-date the gate, or remove the marker.{extra}")


def run() -> None:
    check3()
    check6()
    check10()
    check12()
    check16()
    check30()
    check31()
    check45()
    check48()
    check49()
    check50()
    check58()
    check59()
    check60()
    check61()
    check62()
    check74()
    check76()
    check79()
