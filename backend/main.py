import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.db import init_db
from backend.scheduler import start_scheduler, scheduler, poll_all_sectors
from backend.routers.signals_router import router as signals_router


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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("APEX backend starting…")
    init_db()
    logger.info("Running initial sector poll…")
    try:
        poll_all_sectors(force=True)
    except Exception as e:
        logger.warning(f"Initial poll failed (non-fatal): {e}")
    start_scheduler()
    yield
    scheduler.shutdown(wait=False)
    logger.info("APEX backend stopped")


app = FastAPI(title="APEX", version="1.0", lifespan=lifespan)

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


@app.get("/health")
def health():
    """Health check — also reports scheduler status."""
    from backend.scheduler import scheduler as _sched
    jobs = [
        {"id": j.id, "next_run": str(j.next_run_time)}
        for j in _sched.get_jobs()
    ]
    return {"status": "ok", "scheduler_jobs": jobs}
