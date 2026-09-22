import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from backend.db import init_db
from backend.routers.live_router import router as live_router
from backend.routers.signals_router import router as signals_router
from backend.scheduler import poll_all_sectors, scheduler, start_scheduler


def _sanitize(obj: Any) -> Any:
    """Replace float NaN/Inf with None so json.dumps never raises ValueError."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


class _NaNSafeResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(_sanitize(content), allow_nan=False).encode("utf-8")

# ── Logging ───────────────────────────────────────────────────────────────────
# Stdout sink is provided by loguru by default.
# Add a rotating file sink so logs survive process restarts.

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.add(
    _LOG_DIR / "apex_{time:YYYY-MM-DD}.log",
    rotation="00:00",       # new file each midnight
    retention="14 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
)


# ── App ───────────────────────────────────────────────────────────────────────

SCHEDULER_LOCK = Path(__file__).parent.parent / "data" / "scheduler.lock"


def _acquire_scheduler_lock():
    """Exclusive flock on data/scheduler.lock, or None if another process holds it.
    The handle must stay referenced for the life of the process (the OS releases
    the lock when it closes — including on a uvicorn --reload restart)."""
    import fcntl
    SCHEDULER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(SCHEDULER_LOCK, "w")  # noqa: SIM115 — held for the process lifetime on purpose
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("APEX backend starting…")
    init_db()
    from backend.demo_config import ensure_config_exists as ensure_demo
    from backend.live_config import ensure_config_exists as ensure_live
    ensure_demo()
    ensure_live()
    # The scheduler is the launcher's role, never a default. Every backend
    # instance used to run the full job set (gate, exits, EOD regime,
    # collect_pcr, publish_audit_state); on 2026-09-21 a --reload dev instance
    # on :8001 started "for viewing" placed three live bracket orders (MU,
    # QCOM, ANET) from its own phase-shifted cycles. A first fix made the
    # API-only role opt-in (APEX_NO_SCHEDULER=1) — a default that trades.
    # Now: only a process started with APEX_SERVE=1 (scripts/market_window.sh,
    # scripts/eod_window.sh) may schedule, and only under an exclusive lock on
    # data/scheduler.lock; everything else — dev instances, ad-hoc scripts,
    # tests — serves the API and says so. Niclas, 2026-09-22: "the dev
    # instance should never trade, since it's essentially the developer and
    # not the production."
    app.state.scheduler_lock = None
    if os.environ.get("APEX_SERVE") != "1":
        logger.warning("APEX_SERVE not set — API only, no initial poll, no scheduled jobs "
                       "(the launcher scripts set it; a dev instance never schedules)")
    elif (lock := _acquire_scheduler_lock()) is None:
        logger.warning("APEX_SERVE=1 but the scheduler lock is held by another instance — API only")
    else:
        app.state.scheduler_lock = lock
        logger.info("Running initial sector poll…")
        try:
            poll_all_sectors(force=True)
        except Exception as e:
            logger.warning(f"Initial poll failed (non-fatal): {e}")
        start_scheduler()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    if app.state.scheduler_lock is not None:
        app.state.scheduler_lock.close()
    logger.info("APEX backend stopped")


app = FastAPI(title="APEX", version="1.0", lifespan=lifespan,
              default_response_class=_NaNSafeResponse)

# Read CORS origins from the environment so production deployments don't need
# to modify source code. Falls back to the standard local dev addresses.
_cors_origins_raw = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
)
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(signals_router)
app.include_router(live_router)


@app.get("/health")
def health():
    """Health check — also reports scheduler status."""
    from backend.scheduler import scheduler as _sched
    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in _sched.get_jobs()
    ]
    return {"status": "ok", "scheduler_jobs": jobs,
            "scheduler_owner": app.state.scheduler_lock is not None}
