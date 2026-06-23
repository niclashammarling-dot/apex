"""
Code-health mechanical checks — CHECKs 3, 6, 10, 12, 16, 30, 31, 45, 48.

Covers: fractional qty in broker, test DB isolation, ticker signal data
coverage, Lock3 context key parity, yfinance scalar extraction pattern,
startup live regime exit reconciliation, live bracket TIF/exit fallback,
static code analysis (ruff + connection-leak patterns), and L4 yfinance
retry coverage.
"""
import re

from audit._audit_core import REPO, flag


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

    # Sub-check A — ruff
    try:
        result = subprocess.run(
            ["python3", "-m", "ruff", "check", "backend/",
             "--select", "E,F,W", "--ignore", "E501",
             "--output-format", "concise"],
            cwd=REPO, capture_output=True, text=True, timeout=30,
        )
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

    retry_pattern = re.compile(r"for attempt in range\(2\)")
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
    if not db_path.exists():
        flag(50, "L4 group constraint (live data)", "WARNING", str(db_path),
             "apex.db not found — cannot verify L4 group constraint on executed trades")
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
              AND timestamp >= datetime('now', '-30 days')
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
