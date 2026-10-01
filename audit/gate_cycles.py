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
"""
from datetime import datetime, timezone

CYCLE_KEY = "COALESCE(cycle_started_at, timestamp)"


def cycle_starts(conn, table: str, since: str) -> dict[str, list[str]]:
    """{UTC date: sorted distinct cycle start minutes 'YYYY-MM-DDTHH:MM'} for table."""
    out: dict[str, list[str]] = {}
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    key = CYCLE_KEY if "cycle_started_at" in cols else "timestamp"   # a table older than the column
    for (m,) in conn.execute(
        f"SELECT DISTINCT substr({key}, 1, 16) m FROM {table} WHERE timestamp >= ? ORDER BY m",
        (since,),
    ).fetchall():
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
