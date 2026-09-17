#!/usr/bin/env bash
# APEX EOD window — launched by Windows Task Scheduler (task "APEX EOD window")
# via wsl.exe at 22:10 and 21:10 Stockholm on weekdays. Serves the two jobs that
# earn a schedule (apex-moc, 2026-09-16): eod_regime 16:15 ET, collect_pcr 16:30 ET.
#
# Since 2026-09-18 the market window (scripts/market_window.sh, 15:20/14:20
# Stockholm) serves the whole session including these two jobs; this task stays
# registered as the fallback — it exits on "port bound" when the window is up.
#
# Rules:
#   - Window is defined on the ET clock, not Stockholm: 16:05–16:45 ET. Two Windows
#     triggers (21:10 / 22:10 local) cover the EU/US DST-mismatch weeks; the one
#     that lands outside the ET window exits here without starting anything.
#   - No --reload. The serving process is never the edited process.
#   - If port 8000 is already bound (a manual instance is up), exit — that
#     instance's APScheduler owns the slot. Two schedulers would double-fire.
#   - Stop at 16:40 ET (PCR snapshot completes ~16:31:35; publish_audit_state
#     at 16:33 runs the full mechanical audit, 72 s measured, incl. a backtest).
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

# WSL2 clock can lag the host after a sleep/wake (WakeToRun is how this task
# starts on most nights). timesyncd is active in this distro, but the ET guard
# below, the 16:15/16:30 cron fires, and CHECK 77's written_at provenance all
# read the VM clock — log the drift so a wrong-time night is adjudicable.
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
win_epoch=$("$PS" -NoProfile -Command '[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()' 2>/dev/null | tr -d '\r')
if [[ $win_epoch =~ ^[0-9]+$ ]]; then
    log "clock drift vs Windows: $(( $(date +%s) - win_epoch ))s"
else
    log "clock drift vs Windows: unmeasured (powershell unavailable)"
fi
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

# Sleep until 16:40 ET today.
end_epoch=$(TZ=America/New_York date -d "$(TZ=America/New_York date +%F) 16:40" +%s)
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
