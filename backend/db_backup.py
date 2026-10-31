"""
Daily database backup at scheduler startup (Finding 4, 2026-10-07 review; built 2026-10-31).

The host is never up at midnight, so a cron backup would never run; the scheduler-owning process starts every
session day (scripts/market_window.sh, eod_window.sh), and that start takes the backup. Before this, the only
copies were the manual snapshots taken before a repair (data/backups/apex_<date>_pre-*.db).

Writer: backup_once() from main.lifespan, in the scheduler-owner branch only (a dev instance or a test never
writes one). sqlite3's online backup API gives a consistent copy while the WAL is live; the copy is
integrity-checked and recorded in MANIFEST. Reader: CHECK 95 (audit/checks_data.py), which warns when the newest
backup is old, missing, or failed its integrity check.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from backend import db

BACKUP_KEEP = 14            # daily copies kept; ~110 MB each on 2026-10-31


def backup_dir() -> Path:
    """Beside the database, so a test (conftest's DB) never writes into production's backups."""
    return db.DB_PATH.parent / "backups" / "auto"


def backup_once(now: datetime | None = None) -> dict | None:
    """Back up the database once per UTC day; returns the manifest entry written, or None when today's exists."""
    now = now or datetime.now(timezone.utc)
    out_dir = backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"apex_auto_{now.date().isoformat()}.db"
    if dest.exists():
        return None
    tmp = dest.with_suffix(".db.partial")
    src = sqlite3.connect(str(db.DB_PATH), timeout=30)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    tmp.rename(dest)
    entry = {"file": dest.name, "written_at": now.isoformat(), "bytes": dest.stat().st_size, "integrity": integrity}
    manifest = out_dir / "MANIFEST.json"
    entries = json.loads(manifest.read_text()) if manifest.exists() else []
    entries.append(entry)
    for old in sorted(out_dir.glob("apex_auto_*.db"))[:-BACKUP_KEEP]:
        old.unlink()
    manifest.write_text(json.dumps(entries[-200:], indent=1))
    logger.info(f"DB backup: {dest.name} ({entry['bytes'] / 1e6:.0f} MB, integrity {integrity})")
    return entry
