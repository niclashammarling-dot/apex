#!/usr/bin/env bash
# uvicorn stand-in for tests/test_session_heartbeat.py (the launcher calls "$APEX_UVICORN" with uvicorn args).
exec "${APEX_PYTHON:-python3}" "$(dirname "$0")/fake_uvicorn.py" "$@"
