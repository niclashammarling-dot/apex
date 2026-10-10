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
from datetime import datetime, timezone
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
    **{f"parity:column:signals.{c}": (
        "writes stopped 2026-10-10, history kept (Niclas: \"Stop the 6, store info\")", "2027-04-10")
       for c in ("high_60d", "low_60d", "avg_vol_30d", "macd_hist", "effective_sl")},
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
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"\bSELECT\b", node.value, re.I):
            sql = re.sub(r"(?is)\bSET\b.*?(?=\bWHERE\b|$)", " ", node.value)   # drop UPDATE SET targets
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
            if not _refs(texts, t, (db_py,)):
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

def build(write: bool = False, now: datetime | None = None, db: Path | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date().isoformat()
    prev = json.loads(BUNDLE.read_text()) if BUNDLE.exists() else {}
    first_seen = {i["key"]: i["first_seen"] for i in prev.get("items", []) if "first_seen" in i}

    snap = config_snapshot()
    items = findings_items() + todo_items() + parity_items(db) + config_items(prev.get("config"), snap)
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

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    bundle = {"generated_at": now.isoformat(), "host_commit": commit, "items": kept,
              "excepted": excepted, "config": snap,
              "counts": {**{s: sum(1 for i in kept if i["section"] == s)
                            for s in ("findings", "todos", "parity", "config")},
                         "no_scope": sum(1 for i in kept if not i["scope"])}}
    if write:
        STATE.mkdir(parents=True, exist_ok=True)
        BUNDLE.write_text(json.dumps(bundle, indent=1))
    return bundle


def render_text(b: dict) -> str:
    lines = [f"APEX weekend bundle — built {b['generated_at'][:16]} UTC @ {b.get('host_commit')}",
             "Decide each item at weekend maintenance: fix, wire a reader, or keep with a reason and a date.",
             "Nothing here is removed automatically.", ""]
    names = {"findings": "AUDIT FINDINGS (non-INFO)", "todos": "TODO MARKERS",
             "parity": "SIGNALS WITH NO READER", "config": "CONFIG CHANGED SINCE LAST BUNDLE"}
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
