"""
The Friday weekend bundle (2026-10-10): every APEX fault that needs a decision,
in one dated list, built at the end of Friday's publish and decided at weekend
maintenance with the server down.

Niclas 2026-10-10: "All apex faults must find a way to the weekend bundle and be
fixed before next weeks market opens"; "Smart proposal for the end of Friday,
that's when the info is fresh. Saturday is for work when server is down"; "we
don't want automatic removals from the code ... it may surface things ... for us
to decide on weekends"; "mark maintenance items with todo, that marks work for
bundle on weekends".

Found the same day: WARNING findings had no reader since the CI audit stopped on
06-27 (only CRITICAL is mailed), TODO comments lost their only reader (the CI LLM
check) on the same day, and a whole-codebase sweep found columns, routes,
functions and components with no reader. This module is the reader for all of
them. It never changes code: it lists, and Niclas decides.

Sections:
  findings  non-INFO rows of audit/state/latest.json
  todos     TODO/FIXME/HACK comment markers; structured form
            TODO(bundle YYYY-MM-DD): what — why   (unstructured ones are flagged)
  parity    signals with no reader: columns written but never read, routes no
            frontend code calls, components imported nowhere, files an audit
            check reads that do not exist. Each scan runs a known-present
            control first; a failed control is an item, never an empty section.
  config    values as applied, read from code and config files, diffed against
            the previous bundle (a decision record is not the applied value:
            329025a's message said 0.70, the code held 0.75 for 4.5 months)
  signals   the signal register (2026-10-19): every open S-row of
            backend/backtest/PRODUCTION_GAP.md, so a weekend session works from the
            list (Niclas: "Se till så att lördags sessioner framöver vet om production
            gap och signalistan och förhåller sig till den"); a register that cannot be
            read is a WARNING, never an empty section
  retirement  Part B of the retire conditions (2026-10-31): active checks with no trigger in 90 days
            and no control column, count and the 5 oldest; the last trigger is the later of the
            CHECKS.md date and the nightly state history (origin/audit-state)
  weekly    the weekly report's named reader (2026-10-15): one item per week
            from WEEKLY_READ_START whose read is not filed in the vault
            (raw/notes/YYYY-MM/YYYY-MM-DD-apex-weekly-read-<ISO week>.md), with
            the fixed questions as its scope; a WARNING when this week's report
            was not sent, or when the vault is not reachable

Each item has a stable key and keeps its first_seen date across bundles, so the
list shows how long a fault has waited. EXCEPTIONS holds parity items kept on
purpose, each with a reason and an expiry; an expired exception is listed again.

Every item carries a proposed scope — how to handle it (Niclas 2026-10-10: "there
should also be a proposed scope on how to handle the item. If not we will have to
remember how from context several days ago"). Scopes come from audit/bundle_scopes.py
(per CHECK, per item key, per parity kind), the TODO marker's "— scope:" segment, or
the config default. An item without one is flagged NO PROPOSED SCOPE.

CLI (read-only unless --write): python -m audit.bundle [--write]
publish_state calls build(write=True) on the week's last NYSE session (is_bundle_day) and mails render_text().
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO   = Path(__file__).resolve().parent.parent
STATE  = REPO / "audit" / "state"
LATEST = STATE / "latest.json"
BUNDLE = STATE / "bundle.json"
NY     = ZoneInfo("America/New_York")

CODE_DIRS = ("backend", "frontend/src", "audit", "scripts")

# Parity items kept on purpose: key -> (reason, expiry YYYY-MM-DD).
EXCEPTIONS: dict[str, tuple[str, str]] = {
    # The drift monitor is empty and is rebuilt as demo/live/real parity (Niclas 2026-10-31: "Den ska samla parity
    # mellan demo, live och senare även real money"; scope: vault raw/notes/2026-10/2026-10-31-apex-saturday-scopes.md
    # section 3). Its unread parts are kept until the rebuild is scoped with Niclas's list of known differences.
    **{k: ("drift monitor rebuilt as demo/live/real parity (ruled 2026-10-31); kept until the rebuild scope",
           "2026-11-21")
       for k in ("parity:route:POST /api/drift/alerts/{alert_id}/acknowledge", "parity:route:POST /api/drift/seed",
                 "parity:route:POST /api/drift/run", "parity:function:backend/drift_monitor.py:format_drift_context")},
    **{f"parity:column:signals.{c}": (
        "writes stopped 2026-10-10, history kept (Niclas: \"Stop the 6, store info\")", "2027-04-10")
       for c in ("high_60d", "low_60d", "avg_vol_30d", "macd_hist", "effective_sl")},
    # Manual operator tools: no code calls them, a person does (a6ddc08 removed one as
    # "referenced nowhere" on 2026-10-10 and it was restored the same day).
    "parity:function:backend/db.py:update_live_trade_order_id": (
        "manual recovery kit with reopen_unreconciled() + place_oco_exit() (HON 2026-08-10)", "2027-04-10"),
    "parity:function:backend/brokers/alpaca.py:place_oco_exit": (
        "manual recovery kit: re-arms broker-side protection on a still-held position (HON 2026-08-10)", "2027-04-10"),
    "parity:function:backend/ticker_threshold_calibration.py:print_calibration_report": (
        "manual inspection of per-sector score distributions and thresholds", "2027-04-10"),
    **{f"parity:column:l5_token_usage.{c}": (
        "writes stopped 2026-10-10 (no reader since CHECK 52 retired 09-18), history kept", "2027-04-10")
       for c in ("input_tokens", "output_tokens", "cost_usd")},
    "parity:column:session_flags.first_seen_at": (
        "provenance of a partial-session flag (when it was first seen); read when debugging CHECK 85", "2027-04-10"),
    "parity:column:session_flags.finalized_at": (
        "provenance of a partial-session flag (when it became final); read when debugging CHECK 85", "2027-04-10"),
    "parity:column:news.published_at": (
        "article publish time, provenance for sentiment inputs; cheap", "2027-04-10"),
    "parity:column:trades.wallet_balance_after": (
        "left the schema 2026-04-01 (cc2aa23), never written; column survives in old DBs", "2027-04-10"),
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _code_files(exts: set[str]) -> list[Path]:
    out = []
    for d in CODE_DIRS:
        for p in (REPO / d).rglob("*"):
            if (p.suffix in exts and "__pycache__" not in p.parts and "node_modules" not in p.parts
                    and not p.name.startswith("nightly-report")):
                out.append(p)
    return out


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def _item(key: str, section: str, severity: str, where: str, text: str, **extra) -> dict:
    return {"key": key, "section": section, "severity": severity, "where": where, "text": text, **extra}


# ── findings ──────────────────────────────────────────────────────────────────

def findings_items() -> list[dict]:
    if not LATEST.exists():
        return [_item("findings:missing", "findings", "WARNING", _rel(LATEST),
                      "audit/state/latest.json missing — the host audit did not publish; findings unread")]
    payload = json.loads(LATEST.read_text())
    out = []
    for f in payload.get("findings", []):
        num, name, sev, where, text = (list(f) + [""] * 5)[:5]
        if sev == "INFO":
            continue
        out.append(_item(f"finding:{num}:{sev}:{where}", "findings", sev, where, f"CHECK {num} {name}: {text}"))
    return out


# ── todos ─────────────────────────────────────────────────────────────────────

_MARKER   = re.compile(r"(?:#|//|/\*|\{/\*)\s*(TODO|FIXME|HACK)\b(.*)")
_BUNDLED  = re.compile(r"\(bundle (\d{4}-\d{2}-\d{2})\):\s*(.+?)\s+—\s+(.+?)(?:\s+—\s+scope:\s*(.+?))?\s*(?:\*/\}?)?$")


def _blame_date(path: Path, line: int) -> str | None:
    r = subprocess.run(["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", str(path)],
                       cwd=REPO, capture_output=True, text=True)
    m = re.search(r"^author-time (\d+)", r.stdout, re.M)
    return datetime.fromtimestamp(int(m.group(1)), timezone.utc).date().isoformat() if m else None


def todo_items() -> list[dict]:
    out = []
    for p in _code_files({".py", ".jsx", ".js", ".sh"}):
        for i, line in enumerate(p.read_text(errors="ignore").split("\n"), 1):
            m = _MARKER.search(line)
            if not m:
                continue
            kind, rest = m.group(1), m.group(2).strip()
            b = _BUNDLED.match(rest)
            if b:
                date, what, why, scope = b.groups()
                out.append(_item(f"todo:{_rel(p)}:{what[:60]}", "todos", "WARNING", f"{_rel(p)}:{i}",
                                 f"{kind}: {what} — {why}", written=date, structured=True, scope=scope))
            else:
                text = rest.lstrip(":").strip().rstrip("*/} ").strip()
                out.append(_item(f"todo:{_rel(p)}:{text[:60]}", "todos", "WARNING", f"{_rel(p)}:{i}",
                                 f"{kind}: {text} (unstructured — add date, why and scope: "
                                 f"TODO(bundle YYYY-MM-DD): what — why — scope: how)",
                                 written=_blame_date(p, i), structured=False))
    return out


# ── parity ────────────────────────────────────────────────────────────────────

def _refs(texts: dict[Path, str], word: str, exclude: tuple[Path, ...] = ()) -> list[Path]:
    rx = re.compile(r"\b%s\b" % re.escape(word))
    return [p for p, t in texts.items() if p not in exclude and rx.search(t)]


def _control(name: str, ok: bool) -> list[dict]:
    return [] if ok else [_item(f"parity:scan_broken:{name}", "parity", "WARNING", "audit/bundle.py",
                                f"{name} scan failed its known-present control — its section is unread, "
                                f"not empty; fix the scan")]


def _db_read_tokens(db_text: str) -> set[str]:
    """Words backend/db.py reads: inside a SQL string containing SELECT (columns,
    WHERE, ORDER BY), or a row access r["col"] / .get("col"). INSERT column lists,
    UPDATE SET targets, signatures and docstrings are not reads. SELECT * is
    invisible here (known limit)."""
    words: set[str] = set()
    try:
        tree = ast.parse(db_text)
    except SyntaxError:
        return words
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):   # f-string: its literal parts read as one SQL text
            text = " ".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if text and re.search(r"\bSELECT\b", text, re.I):
            sql = re.sub(r"(?is)\bSET\b.*?(?=\bWHERE\b|$)", " ", text)   # drop UPDATE SET targets
            words |= set(re.findall(r"[A-Za-z_]\w*", sql))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            words.add(node.slice.value)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
              and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            words.add(node.args[0].value)
    return words


def parity_items(db: Path | None = None) -> list[dict]:
    me = Path(__file__).resolve()
    texts = {p: p.read_text(errors="ignore") for p in _code_files({".py", ".jsx", ".js"}) if p.resolve() != me}
    db_py = REPO / "backend" / "db.py"
    db_reads = _db_read_tokens(texts.get(db_py, ""))
    out: list[dict] = []

    # columns written but never read outside backend/db.py
    db = db or REPO / "data" / "apex.db"
    if not db.exists():
        out.append(_item("parity:columns:db_missing", "parity", "WARNING", "data/apex.db",
                         "apex.db missing — column scan unread"))
    else:
        out += _control("columns", bool(_refs(texts, "signal_score", (db_py,))))
        generic = {"id", "timestamp", "ticker", "sector", "price", "outcome", "pnl", "score", "date",
                   "day", "key", "value", "job", "name", "status", "created_at", "updated_at"}
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            if not _refs(texts, t, (db_py,)) and t not in db_reads:
                out.append(_item(f"parity:table:{t}", "parity", "WARNING", f"data/apex.db:{t}",
                                 f"table {t}: no reference outside backend/db.py"))
            for col in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
                if col in generic:
                    continue
                r = _refs(texts, col, (db_py,))
                if len(r) <= 1 and col not in db_reads:
                    out.append(_item(f"parity:column:{t}.{col}", "parity", "WARNING", f"data/apex.db:{t}",
                                     f"column {t}.{col}: " + (f"only {_rel(r[0])} (its writer?)" if r
                                                              else "no reference outside backend/db.py")))
        c.close()

    # routes no frontend code calls
    fe = " ".join(t for p, t in texts.items() if _rel(p).startswith("frontend/"))
    used = {re.sub(r"\$\{[^}]*\}|\{[^}]+\}", "*", u).rstrip("/")
            for u in re.findall(r"/api/[A-Za-z0-9_/{}$.-]+", fe)}

    def called(path: str) -> bool:
        rp = re.sub(r"\{[^}]+\}", "*", path).rstrip("/")
        rx = "^" + re.escape(rp).replace(r"\*", "[^/]+") + "$"
        return any(re.match(rx, u) or u == rp for u in used) or \
            any(u.endswith("*") and rp.startswith(u[:-1]) for u in used)

    out += _control("routes", called("/api/sectors"))
    for p in (REPO / "backend" / "routers").glob("*.py"):
        s = texts.get(p, "")
        pre = re.search(r'APIRouter\(\s*prefix="([^"]*)"', s)
        pre = pre.group(1) if pre else ""
        for m in re.finditer(r'@router\.(get|post|put|delete)\("([^"]+)"', s):
            path = pre + m.group(2)
            if not called(path):
                out.append(_item(f"parity:route:{m.group(1).upper()} {path}", "parity", "WARNING", _rel(p),
                                 f"{m.group(1).upper()} {path}: no frontend caller"))

    # components imported nowhere
    jsx = [p for p in texts if p.suffix in (".jsx", ".js") and _rel(p).startswith("frontend/")]
    out += _control("components", any(re.search(r"from\s+['\"][./\w-]*ApexTerminal(\.jsx)?['\"]", texts[x])
                                      for x in jsx))
    for p in jsx:
        if p.stem in ("main", "App"):
            continue
        if not any(x != p and re.search(r"from\s+['\"][./\w-]*%s(\.jsx|\.js)?['\"]" % re.escape(p.stem), texts[x])
                   for x in jsx):
            out.append(_item(f"parity:component:{_rel(p)}", "parity", "WARNING", _rel(p),
                             "component imported nowhere"))

    # files an audit check reads that do not exist
    for p in [x for x in texts if _rel(x).startswith("audit/")]:
        for m in re.finditer(r'REPO\s*/\s*"([^"]+)"', texts[p]):
            target = m.group(1)
            if "{" in target or "nightly" in target or (REPO / target).exists():
                continue
            if target.startswith("data/") or target.startswith("audit/state"):
                continue   # runtime files: require_data_file reports those as SKIPPED
            out.append(_item(f"parity:audit_path:{_rel(p)}:{target}", "parity", "WARNING", _rel(p),
                             f"reads {target}, which does not exist"))

    # top-level functions referenced nowhere but their definition
    out += _control("functions", bool([x for x in _refs(texts, "get_lock1_candidates")
                                       if x != db_py]))
    for p in [x for x in texts if x.suffix == ".py"]:
        try:
            tree = ast.parse(texts[p])
        except SyntaxError:
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.decorator_list \
                    and n.name != "main":
                if not [x for x in _refs(texts, n.name) if x != p] and \
                        len(re.findall(r"\b%s\b" % re.escape(n.name), texts[p])) <= 1:
                    out.append(_item(f"parity:function:{_rel(p)}:{n.name}", "parity", "WARNING",
                                     f"{_rel(p)}:{n.lineno}", f"function {n.name} referenced nowhere"))
    return out


# ── config as applied ─────────────────────────────────────────────────────────

def config_snapshot() -> dict:
    sys.path.insert(0, str(REPO))
    import backend.config as cfg
    snap = {
        "SECTOR_THRESHOLD_FLOORS": dict(cfg.SECTOR_THRESHOLD_FLOORS),
        "EXCLUDED_SECTORS":        sorted(cfg.EXCLUDED_SECTORS),
        "LIVE_TICKER_BLOCKLIST":   sorted(cfg.LIVE_TICKER_BLOCKLIST),
        "LOCK1_THRESHOLD":         cfg.LOCK1_THRESHOLD,
    }
    for name in ("live_config", "demo_config"):
        f = REPO / "data" / f"{name}.json"
        if f.exists():
            snap[name] = json.loads(f.read_text())
    try:
        from backend.gate.freshness import ENFORCE_BAR_DATE
        snap["ENFORCE_BAR_DATE"] = ENFORCE_BAR_DATE
    except Exception:
        pass
    return snap


def _flatten(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _flatten(v, f"{prefix}{k}.")
    else:
        yield prefix.rstrip("."), d


def config_items(prev: dict | None, snap: dict) -> list[dict]:
    if not prev:
        return []
    old, new = dict(_flatten(prev)), dict(_flatten(snap))
    out = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            out.append(_item(f"config:{k}:{json.dumps(new.get(k))}", "config", "WARNING", k,
                             f"{k}: {json.dumps(old.get(k))} → {json.dumps(new.get(k))} — changed since the "
                             f"last bundle; confirm the change was decided"))
    return out


# ── weekly report read ────────────────────────────────────────────────────────
#
# Niclas 2026-10-03: "a report that isn't used and learned from is pointless".
# The report is mailed Friday 16:05 ET (backend/scheduler.py, id weekly_report),
# before this bundle is built, so Saturday maintenance reads it. A week's read
# is done when its answers are filed; until then the item stays and its
# first_seen shows how long it has waited.

WEEKLY_SENT       = REPO / "data" / "weekly_report_sent.txt"   # backend/weekly_report._mark_sent
# Reads are learnings, so they live in the vault only, never mirrored here (Niclas
# 2026-10-15: "weekly reads should absolutely be in the vault"). The host bundle reads it.
VAULT             = Path("/mnt/c/Users/Nix/Niclas-KB")
WEEKLY_READ_START = "2026-W42"   # first week with a filed read due (mechanism shipped 2026-10-15)
WEEKLY_SNAPSHOT_START = "2026-W43"   # first send by code that stores weekly_report_snapshots (2026-10-19)
WEEKLY_QUESTIONS  = (
    "Which numbers sit outside their prior-week reference with n >= 10 on both sides?",
    "Does each funnel balance, and does any decision show under 'other'?",
    "Which sector carried the week's P&L, and how does that compare with its prior-week record?",
    "Did config change this week (CONFIG section), and does that explain anything in question 1?",
    "Optimizer: incumbent beaten or not, and is the noise floor valid?",
    "What do we start, stop, or do more or less of because of this, or 'no action: <why>'?",
)


def _iso_week(d) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _snapshot_stored(week: str) -> bool | None:
    """backend/weekly_report._store_snapshot's row for `week`; None when the DB or table is unreadable."""
    db = REPO / "data" / "apex.db"
    if not db.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return c.execute("SELECT 1 FROM weekly_report_snapshots WHERE week = ?", (week,)).fetchone() is not None
        finally:
            c.close()
    except sqlite3.Error:
        return None


def weekly_items(now: datetime) -> list[dict]:
    from datetime import date, timedelta
    current = _iso_week(now.astimezone(timezone.utc).date())   # _mark_sent labels weeks in UTC
    out = []
    sent = WEEKLY_SENT.read_text().strip() if WEEKLY_SENT.exists() else None
    if sent != current:
        out.append(_item(f"weekly:sent:{current}", "weekly", "WARNING", _rel(WEEKLY_SENT),
                         f"weekly report not sent for {current} (marker reads {sent or 'nothing: file missing'})",
                         scope="check the scheduler log for 'Weekly report failed'; send it before reading"))
    elif current >= WEEKLY_SNAPSHOT_START and (stored := _snapshot_stored(current)) is not True:
        out.append(_item(f"weekly:snapshot:{current}", "weekly", "WARNING", "data/apex.db:weekly_report_snapshots",
                         f"weekly report {current} sent but its snapshot is "
                         + ("not stored" if stored is False else "unreadable (no DB or no table)"),
                         scope="grep logs/apex_*.log for 'Weekly snapshot'; the week's optimizer result, "
                               "thresholds and commentary are recoverable only until next week's overwrite"))
    y, w = WEEKLY_READ_START.split("-W")
    d = date.fromisocalendar(int(y), int(w), 1)
    questions = " ".join(f"({i}) {q}" for i, q in enumerate(WEEKLY_QUESTIONS, 1))
    notes = VAULT / "raw" / "notes"
    if not notes.is_dir():
        # Unreadable is not "not filed": one item for the cause, not one per week.
        return out + [_item("weekly:vault-missing", "weekly", "WARNING", str(notes),
                            "vault not reachable from the host — weekly reads cannot be checked",
                            scope="mount/pull the vault at this path, or fix VAULT in audit/bundle.py")]
    while _iso_week(d) <= current:
        week = _iso_week(d)
        if not list(notes.glob(f"*/*-apex-weekly-read-{week}.md")):
            out.append(_item(f"weekly:read:{week}", "weekly", "TODO" if week == current else "WARNING",
                             f"vault raw/notes/YYYY-MM/YYYY-MM-DD-apex-weekly-read-{week}.md",
                             f"weekly report {week}: read not filed",
                             scope=f"read the report, file the answers in the vault as "
                                   f"raw/notes/YYYY-MM/YYYY-MM-DD-apex-weekly-read-{week}.md: {questions}"))
        d += timedelta(weeks=1)
    return out


# ── signal register ───────────────────────────────────────────────────────────

# Its own file on master since 2026-10-21 (moved from PRODUCTION_GAP.md § Signal gaps on port-regime).
GAP_FILE_REL = "backend/backtest/SIGNAL_REGISTER.md"
_S_ROW = re.compile(r"^\|\s*(S\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|")
SIGNAL_SCOPE = ("weekend order (ruled 2026-10-19): open S-rows are hypotheses; take the next one whose data "
                "exists, write its battery cell and rule before the run, run, then adopt (live and engine "
                "together) or discard, and write the verdict into the row. The battery decides edge; live "
                "shows whether the model holds against reality, and when live fails the model is wrong.")


def signal_items() -> list[dict]:
    path = REPO / GAP_FILE_REL
    if not path.exists():
        return [_item("signals:register-missing", "signals", "WARNING", GAP_FILE_REL,
                      "signal register not found — the weekend list of signal hypotheses cannot be read",
                      scope="restore backend/backtest/SIGNAL_REGISTER.md (the S-rows, moved there from PRODUCTION_GAP.md 2026-10-21)")]
    rows = [m.groups() for m in map(_S_ROW.match, path.read_text(encoding="utf-8").splitlines()) if m]
    if not rows:   # the file exists and holds S-rows: zero parsed means the reader broke, not "no gaps"
        return [_item("signals:register-unparsed", "signals", "WARNING", GAP_FILE_REL,
                      "no S-rows parsed from the signal register (format changed?)",
                      scope="fix audit/bundle.py _S_ROW against the table, test_signal_register_rows")]
    return [_item(f"signals:{sid}", "signals", "TODO", GAP_FILE_REL,
                  f"{sid}: {hyp.strip()[:160]} — data: {data.strip()[:80]} — {status.strip()[:80]}",
                  scope=SIGNAL_SCOPE)
            for sid, hyp, _info, data, status in rows if status.strip().lower().startswith("open")]


# ── retirement (Part B: quiet and unarmed) ───────────────────────────────────

QUIET_DAYS = 90       # a review cadence for the unarmed backlog, not evidence (scope 2026-10-14, Part B)
STATE_BRANCH = "origin/audit-state"
RETIRE_SCOPE = ("Silence without a control cannot be told from a broken check. Per check: give it a control (a test "
                "that makes it fire, the positive-control column) or rule it obsolete and move its row to Retired with "
                "the reason. Never retire on silence alone (scope 2026-10-14). Take the oldest first.")


def _registry_rows() -> list[dict]:
    """Active rows of audit/CHECKS.md: number, last_triggered (a date or None) and the control column."""
    out = []
    for line in (REPO / "audit" / "CHECKS.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|", line)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if m and len(cells) == 8:
            lt = cells[5] if re.match(r"\d{4}-\d{2}-\d{2}$", cells[5]) else None
            out.append({"num": int(m.group(1)), "name": cells[1], "last": lt,
                        "control": cells[7] if cells[7] not in ("", "—", "-") else None})
    return out


def _state_history_last() -> dict[int, str]:
    """Most recent date each check had a non-INFO finding in the nightly state history (origin/audit-state,
    one commit per publish since 2026-09-12). Raises when the branch cannot be read."""
    shas = subprocess.run(["git", "log", "--format=%H %cI", STATE_BRANCH, "--", "latest.json"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout.split("\n")
    last: dict[int, str] = {}
    for line in filter(None, shas):
        sha, when = line.split(" ", 1)
        raw = subprocess.run(["git", "show", f"{sha}:latest.json"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout
        day = when[:10]
        for f in json.loads(raw).get("findings", []):
            if len(f) > 2 and f[2] != "INFO":
                n = int(f[0])
                last[n] = max(last.get(n, ""), day)
    return last


def retirement_items(today: str) -> list[dict]:
    """Part B (2026-10-31, Niclas: "gör B"): active checks quiet for QUIET_DAYS and without a control.

    Built 2026-10-31 on a finding: CHECKS.md's last_triggered/last_clean stopped advancing with the CI audit
    (latest last_clean 2026-09-16), so the registry alone would call every check quiet. The last trigger is
    the later of the registry's date (kept until about 09-16) and the nightly state history (from 09-12)."""
    try:
        hist = _state_history_last()
    except Exception as e:
        return [_item("retirement:history_unreadable", "retirement", "WARNING", STATE_BRANCH,
                      f"nightly state history unreadable ({str(e)[:80]}): quiet checks cannot be told; "
                      f"fetch {STATE_BRANCH} on the host", scope=RETIRE_SCOPE)]
    cutoff = (datetime.fromisoformat(today) - timedelta(days=QUIET_DAYS)).date().isoformat()
    quiet = []
    for r in _registry_rows():
        last = max(filter(None, (r["last"], hist.get(r["num"]))), default=None)
        if r["control"] is None and (last is None or last < cutoff):
            quiet.append({**r, "last": last})
    if not quiet:
        return []
    quiet.sort(key=lambda r: r["last"] or "")
    oldest = "; ".join(f"CHECK {r['num']} {r['name'][:40]} (last {r['last'] or 'never'})" for r in quiet[:5])
    return [_item("retirement:quiet_unarmed", "retirement", "WARNING", "audit/CHECKS.md",
                  f"QUIET, UNARMED: {len(quiet)} active checks without a trigger in {QUIET_DAYS} days and without a "
                  f"control — silence cannot be told from broken. Oldest: {oldest}", scope=RETIRE_SCOPE)]


# ── scopes ────────────────────────────────────────────────────────────────────

def _scope_for(it: dict) -> str | None:
    from audit import bundle_scopes as sc
    if it["key"] in sc.BY_KEY:
        return sc.BY_KEY[it["key"]]
    if it["section"] == "findings":
        m = re.match(r"finding:(\d+):", it["key"])
        return sc.BY_CHECK.get(int(m.group(1))) if m else None
    if it["section"] == "parity":
        return sc.BY_KIND.get(it["key"].split(":")[1])
    if it["section"] == "config":
        return sc.CONFIG_SCOPE
    return None


# ── build ─────────────────────────────────────────────────────────────────────

def _check88_after_build(kept: list[dict], first_seen: dict, today: str) -> list[dict]:
    """CHECK 88 is evaluated on the bundle being built, not copied from latest.json (2026-10-31).

    The nightly audit runs before the bundle day's build, so latest.json's CHECK 88 rows describe the previous
    bundle (and its age rule is moot for a bundle built now). They are dropped and the content rules
    (audit.checks_gate.bundle_content_findings) run on this bundle's items instead."""
    from audit.checks_gate import bundle_content_findings
    kept = [i for i in kept if not i["key"].startswith("finding:88:")]
    for sev, where, text in bundle_content_findings(kept):
        key = f"finding:88:{sev}:{where}"
        it = _item(key, "findings", sev, where, f"CHECK 88 weekend bundle built (evaluated after the build): {text}")
        it["first_seen"] = first_seen.get(key, today)
        it["scope"] = _scope_for(it)
        kept.append(it)
    return kept


def throughput(prev: dict, kept: list[dict], excepted: list[dict], today: str) -> dict:
    """How fast items leave, against the previous bundle (2026-10-31). Niclas 2026-10-13: "We have always been
    good at producing signals and finding information, but action has been slow … worth keeping an eye on".
    Found 2026-10-31: the 10-30 bundle carried the 10-10 CHECK 54 pair and four drift items for three weeks and
    nothing in it said so. Written here into bundle.json; read by render_text (the Friday mail's first line,
    and the Saturday session's read). No previous bundle is stated as such, never as zero closed."""
    if not prev.get("items") and not prev.get("generated_at"):
        return {"previous": None}
    before = {i["key"] for i in prev.get("items", [])}
    now_keys = {i["key"] for i in kept}
    held = {e["key"] for e in excepted}
    oldest = min(kept, key=lambda i: i.get("first_seen") or today, default=None)
    age = ((datetime.fromisoformat(today) - datetime.fromisoformat(oldest["first_seen"])).days
           if oldest and oldest.get("first_seen") else None)
    return {"previous": prev.get("generated_at"),
            "closed": len(before - now_keys - held), "excepted": len(before & held),
            "new": len(now_keys - before), "carried": len(before & now_keys),
            "oldest_key": oldest["key"] if oldest else None, "oldest_days": age}


def _throughput_line(t: dict | None) -> str:
    if not t:
        return "THROUGHPUT: not computed (bundle built before 2026-10-31)"
    if t.get("previous") is None:
        return "THROUGHPUT: no previous bundle to compare against"
    oldest = f"; oldest {t['oldest_days']} d ({t['oldest_key']})" if t.get("oldest_key") else ""
    return (f"THROUGHPUT since {t['previous'][:10]}: closed {t['closed']}, new {t['new']}, carried {t['carried']}"
            + (f", moved to exceptions {t['excepted']}" if t.get("excepted") else "") + oldest)


def build(write: bool = False, now: datetime | None = None, db: Path | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date().isoformat()
    prev = json.loads(BUNDLE.read_text()) if BUNDLE.exists() else {}
    first_seen = {i["key"]: i["first_seen"] for i in prev.get("items", []) if "first_seen" in i}

    snap = config_snapshot()
    items = (findings_items() + todo_items() + parity_items(db) + config_items(prev.get("config"), snap)
             + weekly_items(now) + signal_items() + retirement_items(today))
    excepted = []
    kept = []
    for it in items:
        exc = EXCEPTIONS.get(it["key"])
        if exc and exc[1] >= today:
            excepted.append({"key": it["key"], "reason": exc[0], "expires": exc[1]})
            continue
        if exc:
            it["text"] += f" (exception expired {exc[1]}: {exc[0]})"
        it["first_seen"] = first_seen.get(it["key"], it.get("written") or today)
        it["scope"] = it.get("scope") or _scope_for(it)
        kept.append(it)
    kept = _check88_after_build(kept, first_seen, today)

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    bundle = {"generated_at": now.isoformat(), "host_commit": commit, "items": kept,
              "excepted": excepted, "config": snap, "throughput": throughput(prev, kept, excepted, today),
              "counts": {**{s: sum(1 for i in kept if i["section"] == s)
                            for s in ("findings", "todos", "parity", "config", "weekly", "signals", "retirement")},
                         "no_scope": sum(1 for i in kept if not i["scope"])}}
    if write:
        STATE.mkdir(parents=True, exist_ok=True)
        BUNDLE.write_text(json.dumps(bundle, indent=1))
    return bundle


def render_text(b: dict) -> str:
    lines = [f"APEX weekend bundle — built {b['generated_at'][:16]} UTC @ {b.get('host_commit')}",
             "Decide each item at weekend maintenance: fix, wire a reader, or keep with a reason and a date.",
             "Nothing here is removed automatically.", _throughput_line(b.get("throughput")), ""]
    names = {"findings": "AUDIT FINDINGS (non-INFO)", "todos": "TODO MARKERS",
             "parity": "SIGNALS WITH NO READER", "config": "CONFIG CHANGED SINCE LAST BUNDLE",
             "weekly": "WEEKLY REPORT READ", "signals": "SIGNAL REGISTER (open hypotheses)",
             "retirement": "RETIREMENT (quiet and unarmed checks)"}
    for s, title in names.items():
        rows = [i for i in b["items"] if i["section"] == s]
        lines.append(f"{title} — {len(rows)}")
        for i in sorted(rows, key=lambda x: (x["first_seen"], x["key"])):
            lines.append(f"  [{i['severity']}] since {i['first_seen']}  {i['where']}  {i['text'][:220]}")
            lines.append(f"      scope: {i['scope']}" if i["scope"] else
                         "      NO PROPOSED SCOPE — write one before the bundle day "
                         "(audit/bundle_scopes.py, or the TODO's '— scope:' segment)")
        lines.append("")
    if b["excepted"]:
        lines.append(f"EXCEPTIONS KEPT ON PURPOSE — {len(b['excepted'])} (each with reason and expiry)")
    return "\n".join(lines)


def is_bundle_day(now: datetime | None = None) -> bool:
    """Today (ET) is the week's last NYSE session — Friday, or Thursday when Friday
    is a holiday — so the bundle is built from the week's last fresh state."""
    from datetime import timedelta

    import pandas_market_calendars as mcal
    d = (now or datetime.now(timezone.utc)).astimezone(NY).date()
    sched = mcal.get_calendar("NYSE").schedule(start_date=d, end_date=d + timedelta(days=7))
    days = list(sched.index.date)
    if not days or days[0] != d:
        return False
    nxt = days[1] if len(days) > 1 else None
    return nxt is None or nxt.isocalendar()[:2] != d.isocalendar()[:2]


if __name__ == "__main__":
    b = build(write="--write" in sys.argv[1:])
    print(render_text(b))
