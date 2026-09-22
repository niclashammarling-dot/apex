"""One scheduler per DB: backend.main._acquire_scheduler_lock is an exclusive, process-scoped flock."""
import subprocess
import sys


def test_lock_is_exclusive_across_processes(tmp_path, monkeypatch):
    import backend.main as m
    monkeypatch.setattr(m, "SCHEDULER_LOCK", tmp_path / "scheduler.lock")
    fh = m._acquire_scheduler_lock()
    assert fh is not None
    probe = (f"import backend.main as m, pathlib; m.SCHEDULER_LOCK = pathlib.Path({str(tmp_path / 'scheduler.lock')!r}); "
             f"print('held' if m._acquire_scheduler_lock() is None else 'free')")
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.strip().endswith("held")
    fh.close()
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.strip().endswith("free")


def test_lifespan_api_only_unless_serve_and_lock(tmp_path, monkeypatch):
    """Without APEX_SERVE the lifespan never touches the lock; with it, a held lock means API only."""
    from fastapi.testclient import TestClient
    import backend.main as m
    monkeypatch.setattr(m, "SCHEDULER_LOCK", tmp_path / "scheduler.lock")
    monkeypatch.delenv("APEX_SERVE", raising=False)
    with TestClient(m.app) as c:
        h = c.get("/health").json()
    assert h["scheduler_owner"] is False and h["scheduler_jobs"] == []
    probe = m._acquire_scheduler_lock()
    assert probe is not None            # the no-serve path left the lock untouched
    monkeypatch.setenv("APEX_SERVE", "1")
    with TestClient(m.app) as c:        # serve requested, but `probe` holds the lock
        h = c.get("/health").json()
    assert h["scheduler_owner"] is False and h["scheduler_jobs"] == []
    probe.close()
