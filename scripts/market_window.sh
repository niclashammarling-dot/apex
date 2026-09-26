#!/usr/bin/env bash
# APEX market window — launched by Windows Task Scheduler (task "APEX market window")
# via wsl.exe at 15:20 and 14:20 Stockholm on weekdays (scripts/windows/APEX-market-window.xml).
# Serves the demo gate for the whole NYSE session and the two EOD jobs after it.
#
# Why (apex-moc, decided 2026-09-17): CHECK 80's treatment rows are live gate
# evaluations — a cycle that does not run is not reconstructible from retained
# inputs, which is the schedule-earning test's clause (a). Coverage since 09-02:
# full sessions write 16–22 cycles; 09-10 wrote 4, 09-16 wrote 7, 09-17 wrote 3 —
# the process ran when a desk session happened to be open, and that session's
# --reload instance was the one serving the gate. CHECK 80 counts cycles per
# session against the calendar (clause b′), shipped with this script.
#
# Rules (same as eod_window.sh):
#   - Window on the ET clock: start if ET is 08:15–15:30, stop at 16:40 ET
#     (publish_audit_state 16:33 + 72 s). Two Stockholm triggers cover the
#     EU/US DST-mismatch weeks; the one outside the ET window exits here.
#   - The stop is clock-based on purpose: on an early close (13:00 ET) the
#     process idles — is_market_open() is calendar-aware for early closes since
#     2026-09-17 — because eod_regime 16:15 and collect_pcr 16:30 still fire.
#   - No --reload. The serving process is never the edited process, and the
#     edited process never serves: only a process started with APEX_SERVE=1
#     (this script, eod_window.sh) may take the scheduler lock. Develop on
#     another port with a plain `uvicorn backend.main:app --port 8001 --reload`
#     and `APEX_API_PORT=8001 npm run dev`; viewing needs no second backend
#     (`npm run dev` alone proxies to :8000).
#   - Port 8000 already bound → exit; that instance's APScheduler owns the day.
#   - Interruption recovery (2026-09-21, from the 09-19 first-window-day note):
#     the wrapper watches the child instead of sleeping blind. Child gone
#     before 16:40 ET → "ended before window close" line, exit 1 → the task's
#     RestartOnFailure relaunches (the ET guard and port probe make any
#     relaunch safe). Host reboot kills the wrapper without an exit code, so
#     the task also has a LogonTrigger: same script, same guards. TERM/HUP
#     are trapped so a WSL teardown that does deliver a signal self-logs.
#   - The hold is a wall-clock loop on `date`, never a blind `sleep N`: on
#     2026-09-21 the previous `sleep` to 16:40 ET ended at 16:17 ET — WSL2's
#     monotonic clock runs fast after a host sleep (3.4% measured) while
#     realtime is kept synced to Windows. collect_pcr 16:30 and the 16:33
#     audit never fired that night. APScheduler re-checks the wall clock.
#   - eod_window.sh (22:10 / 21:10) stays registered as the fallback: it sees
#     the port bound and exits when this window is up, and serves the EOD jobs
#     when this window failed to start.
set -u
APEX=${APEX_ROOT:-/home/promenix/apex}
PY=${APEX_PYTHON:-$APEX/venv/bin/python}
PORT=8000
LOG_DIR=${APEX_LOG_DIR:-$APEX/logs}
mkdir -p "$LOG_DIR"
LOG=$LOG_DIR/market_window_$(TZ=America/New_York date +%F).log
log() { echo "$(date '+%F %T %Z') | $*" >> "$LOG"; }

# MARKET_WINDOW_TEST_SECONDS=N: skip the ET guard and hold the process N seconds
# (smoke test of the launch path only; never set by the scheduled task).
TEST=${MARKET_WINDOW_TEST_SECONDS:-}
et_hm=$(TZ=America/New_York date +%H%M)

# WSL2 clock can lag the host after a sleep/wake (WakeToRun). Log the drift so
# a wrong-time day is adjudicable — same read as eod_window.sh.
PS=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
win_epoch=$("$PS" -NoProfile -Command '[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()' 2>/dev/null | tr -d '\r')
if [[ $win_epoch =~ ^[0-9]+$ ]]; then
    log "clock drift vs Windows: $(( $(date +%s) - win_epoch ))s"
else
    log "clock drift vs Windows: unmeasured (powershell unavailable)"
fi
# NYSE session gate (2026-09-26): the Windows triggers exclude weekends, not
# holidays, so a weekday holiday started the backend — 2026-09-07 Labor Day wrote
# a sector snapshot row, and with the window held to 16:40 the 16:30 collect_pcr
# would stamp a PCR snapshot with the holiday's date. Checked before the ET guard
# and not skipped by the TEST hold (APEX_SESSION_DATE=YYYY-MM-DD overrides the
# date, for tests). Lookup failure starts anyway: a holiday start costs a few
# rows the in-process gates also refuse; a missed start costs a session.
"$PY" "$APEX/scripts/nyse_session.py" "${APEX_SESSION_DATE:-}" 2>>"$LOG"
case $? in
    0) ;;
    1) log "not an NYSE session (${APEX_SESSION_DATE:-$(TZ=America/New_York date +%F)}) — nothing started"; exit 0 ;;
    *) log "NYSE calendar lookup failed — starting anyway (see stderr above)" ;;
esac
if [[ -z $TEST ]] && (( 10#$et_hm < 815 || 10#$et_hm > 1530 )); then
    log "outside ET window (ET now $et_hm) — nothing started"
    exit 0
fi
# APEX_WINDOW_DRY_RUN=1: stop here — every guard above has run, nothing starts
# (tests/test_window_session_gate.py; never set by the scheduled task).
if [[ -n ${APEX_WINDOW_DRY_RUN:-} ]]; then
    log "dry run — guards passed, would start"
    exit 0
fi

if ss -ltn "( sport = :$PORT )" | grep -q ":$PORT"; then
    log "port $PORT already bound — another instance owns this slot, exiting"
    exit 0
fi

cd "$APEX" || exit 1
log "starting uvicorn (no --reload), ET now $et_hm"
APEX_SERVE=1 "$APEX/venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" >> "$LOG" 2>&1 &
PID=$!

on_signal() {
    log "ended before window close (signal $1), ET now $(TZ=America/New_York date +%H%M) — SIGTERM $PID"
    kill -TERM "$PID" 2>/dev/null
    exit 1
}
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

# Watch the child until 16:40 ET today; a child that dies first is a failure
# the task scheduler relaunches (RestartOnFailure in APEX-market-window.xml).
end_epoch=$(TZ=America/New_York date -d "$(TZ=America/New_York date +%F) 16:40" +%s)
[[ -n $TEST ]] && end_epoch=$(( $(date +%s) + TEST ))
while (( $(date +%s) < end_epoch )); do
    if ! kill -0 "$PID" 2>/dev/null; then
        wait "$PID"; rc=$?
        log "ended before window close (uvicorn exited rc=$rc), ET now $(TZ=America/New_York date +%H%M)"
        exit 1
    fi
    sleep 30 & wait $!   # backgrounded so a trapped signal is handled at once
done

log "window closed (ET now $(TZ=America/New_York date +%H%M)) — SIGTERM $PID"
kill -TERM "$PID" 2>/dev/null
for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
if kill -0 "$PID" 2>/dev/null; then log "did not exit in 30s — SIGKILL"; kill -KILL "$PID"; fi
wait "$PID" 2>/dev/null
log "exit"
