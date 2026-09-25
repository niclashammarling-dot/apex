"""
No test writes to the production logs/ directory.

Found 2026-09-26: backend.main attaches a loguru file sink at import, and it
pointed at the repo's logs/ unconditionally — so every suite run that imported
backend.main wrote scheduler-start and EOD catch-up lines into
logs/apex_<date>.log, the evidence later sessions grep to count real firings.
"""
import uuid
from pathlib import Path

from loguru import logger

REPO_LOGS = Path(__file__).resolve().parent.parent / "logs"


def test_backend_main_sink_does_not_write_repo_logs():
    import backend.main as main_module
    marker = f"log-sink-isolation-{uuid.uuid4()}"
    logger.info(marker)
    logger.complete()
    assert main_module._LOG_DIR.resolve() != REPO_LOGS
    for f in REPO_LOGS.glob("apex_*.log"):
        assert marker not in f.read_text(errors="replace"), f"test log line reached {f}"
    written = [f for f in main_module._LOG_DIR.glob("apex_*.log")
               if marker in f.read_text(errors="replace")]
    assert written, "positive control: the sink should have written the marker to its own dir"
