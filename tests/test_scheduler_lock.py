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


def test_lifespan_api_only_when_lock_held(tmp_path, monkeypatch):
    """A second instance serves the API and owns no scheduler; APEX_NO_SCHEDULER never touches the lock."""
    from fastapi.testclient import TestClient
    import backend.main as m
    monkeypatch.setattr(m, "SCHEDULER_LOCK", tmp_path / "scheduler.lock")
    holder = m._acquire_scheduler_lock()
    monkeypatch.delenv("APEX_NO_SCHEDULER", raising=False)
    with TestClient(m.app) as c:
        h = c.get("/health").json()
    assert h["scheduler_owner"] is False and h["scheduler_jobs"] == []
    holder.close()
    monkeypatch.setenv("APEX_NO_SCHEDULER", "1")
    with TestClient(m.app) as c:
        h = c.get("/health").json()
    assert h["scheduler_owner"] is False and h["scheduler_jobs"] == []
    assert m._acquire_scheduler_lock() is not None   # env path left the lock untouched
