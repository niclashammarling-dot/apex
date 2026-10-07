"""
Gate cycles per session, counted one way for every reader (2026-10-01).

A cycle is its cycle_started_at: one stamp for every row it writes (832bc71,
2026-09-30). Before that, readers counted distinct row *minutes*, so a cycle
whose rows spanned a minute boundary counted twice — /api/ops/window read
demo 38/19 on 09-29 and 20/19 on 09-30 (19 real cycles), and CHECK 80's
coverage read the same inflated numbers. CHECK 83 was rekeyed on 09-30; these
two readers were not. Rows written before the column existed fall back to
their own minute, so pre-09-30 counts keep that over-count.

Expected is the number of grid slots inside the session, not length // interval.
An interval job fires every GATE_INTERVAL minutes from wherever the scheduler
started, so a full day holds 19 or 20 cycles depending on phase: live runs
:13/:33/:53 (09:33–15:53 ET, 20 cycles), demo :01/:21/:41 (09:41–15:41, 19).
The phase is read from the session's first observed start; with no starts,
the floor estimate is used. Stdlib only: the audit runs without backend imports.

Source by date (2026-10-04). Gate rows exist only for cycles that evaluated or
skipped a candidate: a quiet or halted cycle wrote none, so every reader
undercounted quiet days. From GATE_CYCLES_FROM the gate_cycles table (one row
per cycle, written by backend/gate/cycle.py) is the only source, trigger =
'scheduler' only — a manual /gate/run is not a scheduled slot and is not a
second scheduler. Before it, gate rows, with the undercount. A fixed date, not
"any day with table rows": the go-live day would mix sources, and a dead day
with one manual run would fall back to its rows and read 1/N. A DB with no
gate_cycles table (never started on the new code) reads gate rows throughout.
"""
from datetime import datetime, timezone

CYCLE_KEY = "COALESCE(cycle_started_at, timestamp)"
# First full session on the recording code. The 10-05 14:20 CEST launch served it
# uncommitted (/health: 3a76bc5, dirty) and it was committed as served that day
# (d470b80), so 10-05 is its first full session.
# Ruled 2026-10-07 (Niclas), after three sessions in production: keep this
# design — one start-time stamp per cycle plus a fixed cutover date — over one
# source per day. Evidence: a scheduler row for every slot on 10-05 and 10-06
# (demo 19/19, live 20/20; first rows 15:40:58 / 15:33:28 CEST as predicted)
# and no row left unfinished. A redesign would have no observed problem behind it.
GATE_CYCLES_FROM = "2026-10-05"
GATE_JOB = {"demo_gate_history": "run_gate", "live_gate_history": "run_live_gate"}


def has_cycle_table(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gate_cycles'").fetchone() is not None


def cycle_starts(conn, table: str, since: str) -> dict[str, list[str]]:
    """{UTC date: sorted distinct cycle start minutes 'YYYY-MM-DDTHH:MM'} for table."""
    out: dict[str, list[str]] = {}
    recorded = has_cycle_table(conn)
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    key = CYCLE_KEY if "cycle_started_at" in cols else "timestamp"   # a table older than the column
    rows_q = f"SELECT DISTINCT substr({key}, 1, 16) m FROM {table} WHERE timestamp >= ?"
    args: tuple = (since,)
    if recorded:
        rows_q += " AND timestamp < ?"
        args += (GATE_CYCLES_FROM,)
    minutes = [m for (m,) in conn.execute(rows_q, args).fetchall()]
    if recorded:
        minutes += [m for (m,) in conn.execute(
            "SELECT DISTINCT substr(started_at, 1, 16) FROM gate_cycles "
            "WHERE job = ? AND trigger = 'scheduler' AND started_at >= ?",
            (GATE_JOB[table], max(since, GATE_CYCLES_FROM))).fetchall()]
    for m in sorted(set(minutes)):
        out.setdefault(m[:10], []).append(m)
    return out


def _utc_minute(dt: datetime) -> int:
    u = dt.astimezone(timezone.utc)
    return u.hour * 60 + u.minute


def expected_cycles(open_dt: datetime, close_dt: datetime, starts: list[str], interval: int) -> int:
    """Grid slots in [open, close) at the phase of the first start (floor estimate without starts)."""
    o, c = _utc_minute(open_dt), _utc_minute(close_dt)
    if not starts:
        return max(1, (c - o) // interval)
    phase = (int(starts[0][11:13]) * 60 + int(starts[0][14:16])) % interval
    return max(1, sum(1 for t in range(o, c) if t % interval == phase))
