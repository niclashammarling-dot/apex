"""Read-only inventory: readers with no writer, writers with no reader (Saturday 2026-11-07).

Vault scope: raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md § 12 (Niclas 2026-11-05: "alla writers
behöver en reader, annars är det lönlöst att skriva grejer som ingen läser"). The output is a candidate list: every
row is read at its source before anything is deleted or wired.

A. Readers with no writer: field names the frontend reads off an object (`x.field`, `x?.field`, `x["field"]`) that
   no backend source can produce. The backend universe is every column of the schema init_db() builds, every
   string constant that is a whole identifier (a dict key's form; docstrings excluded), every SQL `AS alias`,
   keyword argument and annotated class field in backend/ and audit/. Names the
   frontend itself defines (object-literal keys, assignments) and JS built-ins are not readers of the API. A
   union-level test: a name some endpoint returns is not an orphan here even when the card reads it off a different
   endpoint (the per-endpoint form for SELECT * cards is tests/test_trade_log_fields.py).
B1. Columns with no reader: a column whose name occurs nowhere outside write contexts. Write contexts are SQL
   constants starting CREATE / ALTER / INSERT, the SET list of an UPDATE, and `_add_column_if_missing` calls; every
   other occurrence in backend/, audit/, scripts/, frontend/src counts as a read (Python code, SELECT text, JSX).
B2. data/ files: per file under data/ (top level), the modules that name it, for reading by hand; a file named by
   no module is listed as unreferenced.

Known-present controls (the run exits 2 if any misses):
  - A on the parent of 39db75f: LiveTradeLog.jsx must yield `timestamp_exit` (the § 10 reader with no writer);
  - A on a planted line `t.zz_planted_reader` must yield it;
  - B1: `exited_at` (read by the trade-log cards) must not be a candidate; a planted column in a scratch copy of the
    schema must be.

Opens no database except a temp schema built by init_db(); writes only OUT.json.
Usage: .venv/bin/python -u scripts/reader_writer_inventory.py OUT.json
"""
import ast
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WHY = "raw/notes/2026-11/2026-11-02-apex-saturday-scopes-11-07.md"   # § 12 (producer block, vault DATA-PROVENANCE)
PY_DIRS = ("backend", "audit")
READ_DIRS = ("backend", "audit", "scripts", "frontend/src")
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
# JS built-ins and DOM/React members read off non-API objects.
JS = set("""length map filter reduce forEach find findIndex some every includes indexOf slice splice push pop shift
unshift concat join sort reverse keys values entries toFixed toString toLocaleString toLowerCase toUpperCase trim split
replace replaceAll match startsWith endsWith padStart padEnd substring charAt current target value checked preventDefault
stopPropagation then catch finally json ok status statusText headers body log warn error info debug max min abs round floor
ceil sqrt pow log10 random now parse stringify isArray from assign freeze getTime getDate getMonth getFullYear getHours
getMinutes getSeconds getDay toISOString toLocaleDateString toLocaleTimeString setTimeout clearTimeout setInterval
clearInterval addEventListener removeEventListener clientX clientY innerWidth innerHeight style width height left top right
bottom getBoundingClientRect scrollTo focus blur key href location pathname search hash env mode prototype call apply bind
size has get set add delete clear flat flatMap fill at lastIndexOf name message stack code type data default props children
""".split())

# Read names that are not API fields, each with its reason (the bundle's EXCEPTIONS idiom).
NOT_API = {
    "changed": "CSS selector td.changed in App.jsx's style string", "saved": "CSS selector .settings-save-btn.saved",
    "critical": "CSS selector .audit-badge.critical in NightLog.jsx", "cos": "Math.cos", "sin": "Math.sin",
    "msg": "FastAPI's 422 validation detail (e.msg), produced by the framework",
    "md": "the file name audit/QUESTIONS.md in OverviewPyramid.jsx's text",
}


def _git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def frontend_reads(src: str) -> set[str]:
    src = re.sub(r"//[^\n]*|/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"^\s*import\b[^\n]*", " ", src, flags=re.M)                  # module paths (./x.jsx)
    reads = {m.group(1) for m in re.finditer(r"(?<![\w$.])(?:[A-Za-z_$][\w$]*(?:\?\.|\.))+?([a-z][a-z0-9_]*)\b", src)}
    reads |= set(re.findall(r"\[\s*[\"']([a-z][a-z0-9_]*)[\"']\s*\]", src))
    return reads


def frontend_defined(src: str) -> set[str]:
    keys = set(re.findall(r"[{,]\s*([a-z][a-z0-9_]*)\s*:", src))           # object-literal keys
    keys |= set(re.findall(r"\.([a-z][a-z0-9_]*)\s*=(?!=)", src))         # assignments
    keys |= set(re.findall(r"[{,]\s*([a-z][a-z0-9_]*)\s*(?=[,}])", src))   # shorthand keys / destructuring
    return keys


def schema_columns(extra_sql: str = "") -> dict[str, set[str]]:
    import backend.db as db
    db.DB_PATH = Path(tempfile.mkdtemp()) / "schema.db"
    db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    if extra_sql:
        conn.execute(extra_sql)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    cols = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in tables}
    conn.close()
    return cols


def backend_universe(cols: dict[str, set[str]]) -> set[str]:
    names = set().union(*cols.values())
    for d in PY_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:
                continue
            docs = {id(b.body[0].value) for b in ast.walk(tree)
                    if isinstance(b, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and b.body
                    and isinstance(b.body[0], ast.Expr) and isinstance(b.body[0].value, ast.Constant)}
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                    if IDENT.match(n.value):
                        names.add(n.value)
                    names |= set(re.findall(r"\bAS\s+([a-z_][a-z0-9_]*)", n.value, re.I))
                elif isinstance(n, ast.keyword) and n.arg:
                    names.add(n.arg)
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    names.add(n.target.id)
    return names


def inventory_a(universe: set[str]) -> dict[str, list[str]]:
    files = sorted((ROOT / "frontend/src").rglob("*.js*"))
    srcs = {str(p.relative_to(ROOT)): p.read_text() for p in files}
    defined = set().union(*(frontend_defined(s) for s in srcs.values()))
    out = {}
    for f, s in srcs.items():
        orphans = sorted(frontend_reads(s) - JS - set(NOT_API) - defined - universe)
        if orphans:
            out[f] = orphans
    return out


def _write_spans(text: str) -> list[str]:
    """Column-bearing text of write contexts in a Python file: CREATE/ALTER/INSERT constants, UPDATE SET lists,
    _add_column_if_missing(...) calls."""
    spans = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return spans
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            s = n.value.strip()
            head = s[:12].upper()
            if head.startswith(("CREATE", "ALTER", "INSERT")):
                spans.append(s)
            elif head.startswith("UPDATE"):
                m = re.search(r"\bSET\b(.*?)(\bWHERE\b|$)", s, re.S | re.I)
                if m:
                    spans.append(m.group(1))
        elif isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "_add_column_if_missing":
            spans.append(ast.get_source_segment(text, n) or "")
    return spans


def inventory_b1(cols: dict[str, set[str]]) -> dict[str, list[str]]:
    total, written = defaultdict(int), defaultdict(int)
    allcols = set().union(*cols.values())
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, allcols), key=len, reverse=True)) + r")\b")
    for d in READ_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in (".py", ".js", ".jsx") or "node_modules" in p.parts or p.resolve() == Path(__file__).resolve():
                continue
            text = p.read_text(errors="replace")
            for m in pat.finditer(text):
                total[m.group(1)] += 1
            if p.suffix == ".py":
                for span in _write_spans(text):
                    for m in pat.finditer(span):
                        written[m.group(1)] += 1
    out = {}
    for t, cs in sorted(cols.items()):
        if t.startswith("sqlite_"):
            continue
        cand = sorted(c for c in cs if total[c] - written[c] <= 0)
        if cand:
            out[t] = cand
    return out


def inventory_b2() -> dict[str, list[str]]:
    names = sorted(p.name for p in (ROOT / "data").iterdir() if p.is_file() and not p.name.endswith(("-shm", "-wal")))
    refs = defaultdict(list)
    for d in READ_DIRS + ("tests",):
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in (".py", ".js", ".jsx", ".ps1", ".bat") or "node_modules" in p.parts:
                continue
            text = p.read_text(errors="replace")
            for n in names:
                if n in text:
                    refs[n].append(str(p.relative_to(ROOT)))
    return {n: refs.get(n, []) for n in names}


def main(out: str) -> int:
    misses = []
    parent = _git("show", "39db75f~1:frontend/src/components/LiveTradeLog.jsx")
    if "timestamp_exit" not in frontend_reads(parent):
        misses.append("A control: timestamp_exit not read from the parent LiveTradeLog.jsx")
    if "zz_planted_reader" not in frontend_reads("const x = t.zz_planted_reader;"):
        misses.append("A control: planted reader not found")

    cols = schema_columns()
    universe = backend_universe(cols)
    if "timestamp_exit" in universe:
        misses.append("A control: timestamp_exit is in the backend universe")
    a = inventory_a(universe)
    b1 = inventory_b1(cols)
    if "exited_at" in b1.get("live_trades", []) or "exited_at" in b1.get("trades", []):
        misses.append("B1 control: exited_at listed as a column with no reader")
    planted = inventory_b1(schema_columns("ALTER TABLE live_trades ADD COLUMN zz_planted_column TEXT"))
    if "zz_planted_column" not in planted.get("live_trades", []):
        misses.append("B1 control: planted column not listed")
    b2 = inventory_b2()

    commit = _git("rev-parse", "--short", "HEAD").strip()
    result = {
        "producer": {"repo": "apex", "commit": commit, "script": "scripts/reader_writer_inventory.py",
                     "run": f".venv/bin/python -u scripts/reader_writer_inventory.py {out}", "why": WHY,
                     "run_at": datetime.now(timezone.utc).isoformat(), "db": "temp schema from init_db() only"},
        "a_readers_with_no_writer": a,
        "b1_columns_with_no_reader": b1,
        "b2_data_files": b2,
        "controls": {"missed": misses},
    }
    Path(out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("a_readers_with_no_writer", "b1_columns_with_no_reader", "controls")},
                     indent=1))
    print("B2 unreferenced:", [n for n, r in b2.items() if not r])
    return 2 if misses else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
