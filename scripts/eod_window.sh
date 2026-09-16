#!/usr/bin/env bash
# APEX EOD window — launched by Windows Task Scheduler (task "APEX EOD window")
# via wsl.exe at 22:10 and 21:10 Stockholm on weekdays. Serves the two jobs that
# earn a schedule (apex-moc, 2026-09-16): eod_regime 16:15 ET, collect_pcr 16:30 ET.
#
# Rules:
#   - Window is defined on the ET clock, not Stockholm: 16:05–16:45 ET. Two Windows
#     triggers (21:10 / 22:10 local) cover the EU/US DST-mismatch weeks; the one
#     that lands outside the ET window exits here without starting anything.
#   - No --reload. The serving process is never the edited process.
#   - If port 8000 is already bound (a manual instance is up), exit — that
#     instance's APScheduler owns the slot. Two schedulers would double-fire.
#   - Stop at 16:36 ET (PCR snapshot completes ~16:31:35 in the log record).
set -u
APEX=/home/promenix/apex
PORT=8000
LOG_DIR=$APEX/logs
mkdir -p "$LOG_DIR"
LOG=$LOG_DIR/eod_window_$(TZ=America/New_York date +%F).log
log() { echo "$(date '+%F %T %Z') | $*" >> "$LOG"; }

# EOD_WINDOW_TEST_SECONDS=N: skip the ET guard and hold the process N seconds
# (smoke test of the launch path only; never set by the scheduled task).
TEST=${EOD_WINDOW_TEST_SECONDS:-}
et_hm=$(TZ=America/New_York date +%H%M)
if [[ -z $TEST ]] && (( 10#$et_hm < 1605 || 10#$et_hm > 1645 )); then
    log "outside ET window (ET now $et_hm) — nothing started"
    exit 0
fi

if ss -ltn "( sport = :$PORT )" | grep -q ":$PORT"; then
    log "port $PORT already bound — manual instance owns this slot, exiting"
    exit 0
fi

cd "$APEX" || exit 1
log "starting uvicorn (no --reload), ET now $et_hm"
"$APEX/venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" >> "$LOG" 2>&1 &
PID=$!

# Sleep until 16:36 ET today.
end_epoch=$(TZ=America/New_York date -d "$(TZ=America/New_York date +%F) 16:36" +%s)
now_epoch=$(date +%s)
sleep_s=$(( end_epoch - now_epoch ))
[[ -n $TEST ]] && sleep_s=$TEST
(( sleep_s > 0 )) && sleep "$sleep_s"

log "window closed — SIGTERM $PID"
kill -TERM "$PID" 2>/dev/null
for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
if kill -0 "$PID" 2>/dev/null; then log "did not exit in 30s — SIGKILL"; kill -KILL "$PID"; fi
wait "$PID" 2>/dev/null
log "exit"
