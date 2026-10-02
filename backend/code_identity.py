"""
What code this process is serving (2026-10-02). Read once at import of
backend.main and logged at startup, so "is the trading process running today's
commit" is answered by the process itself. Before this, the only record was the
heartbeat's host_commit: a `git rev-parse HEAD` on the checkout about two
minutes after launch, blind to uncommitted edits and to a checkout that moved
after import.

`dirty` counts tracked files modified against HEAD outside data/. data/ holds
runtime-edited tracked config (live_config.json, demo_config.json), which would
make every Settings save read as dirty code.
"""
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def read_code_identity(repo: Path = _REPO) -> dict:
    """{"commit": full sha or None, "dirty": bool or None, "dirty_files": int or None}.
    Never raises: a process that cannot read git still starts, and says so."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        changed = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", ".", ":(exclude)data"],
                                 cwd=repo, capture_output=True, text=True, timeout=5,
                                 check=True).stdout.split()
        return {"commit": sha, "dirty": bool(changed), "dirty_files": len(changed)}
    except Exception:
        return {"commit": None, "dirty": None, "dirty_files": None}


def identity_line(ident: dict) -> str:
    """The startup log line; ops/window parses it back (signals_router._SERVING_LINE)."""
    if ident["commit"] is None:
        return "Serving commit unknown (git unreadable at import)"
    return (f"Serving commit {ident['commit'][:12]} "
            f"(dirty: {ident['dirty']}, {ident['dirty_files']} tracked code file(s) modified)")
