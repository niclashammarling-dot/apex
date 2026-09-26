#!/usr/bin/env bash
# APEX EOD window — launched by Windows Task Scheduler (task "APEX EOD window")
# via wsl.exe at 22:10 and 21:10 Stockholm on weekdays. Serves the evening jobs
# that earn a schedule (apex-moc, 2026-09-16): collect_pcr 16:30 ET and
# publish_audit_state 16:33 ET. eod_regime moved to 08:30 ET pre-open on
# 2026-09-22 (its inputs are retained; the morning run is the same computation).
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
#   - If port 8000 is bound (the market window is up), WATCH it rather than
#     exit: poll every 30 s until 16:45 ET; if the port frees before 16:38 ET,
#     start — the app's startup catch-ups (eod_regime, collect_pcr,
#     publish_audit_state, all same-day) run what the dead window missed.
#     Found 2026-09-21: the window died at 16:17 ET, this task had exited at
#     16:10 on "port bound", and collect_pcr + the nightly audit never ran.
#   - All waits are wall-clock loops on `date`, never a blind `sleep N`: WSL2's
#     monotonic clock runs fast after a host sleep (3.4% measured 2026-09-22),
#     so a 6 h `sleep` ended 23 min early. APScheduler re-checks the wall
#     clock; a shell sleep does not.
#   - Stop at 16:45 ET (PCR snapshot completes ~16:31:35; publish_audit_state
#     at 16:33 runs the full mechanical audit, 72 s measured, incl. a backtest).
set -u
APEX=${APEX_ROOT:-/home/promenix/apex}
PY=${APEX_PYTHON:-$APEX/venv/bin/python}
PORT=8000
LOG_DIR=${APEX_LOG_DIR:-$APEX/logs}
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
if [[ -z $TEST ]] && (( 10#$et_hm < 1605 || 10#$et_hm > 1645 )); then
    log "outside ET window (ET now $et_hm) — nothing started"
    exit 0
fi
# APEX_WINDOW_DRY_RUN=1: stop here — every guard above has run, nothing starts
# (tests/test_window_session_gate.py; never set by the scheduled task).
if [[ -n ${APEX_WINDOW_DRY_RUN:-} ]]; then
    log "dry run — guards passed, would start"
    exit 0
fi

et_epoch() { TZ=America/New_York date -d "$(TZ=America/New_York date +%F) $1" +%s; }
start_by=$(et_epoch 16:38)
end_epoch=$(et_epoch 16:45)
if [[ -n $TEST ]]; then start_by=$(( $(date +%s) + TEST )); end_epoch=$start_by; fi

# Wait for the port to be free (the market window owns it while alive).
waited=0
while ss -ltn "( sport = :$PORT )" | grep -q ":$PORT"; do
    (( waited == 0 )) && log "port $PORT bound — market window up, watching until ET 1638"
    waited=1
    if (( $(date +%s) >= start_by )); then
        log "port $PORT still bound at ET $(TZ=America/New_York date +%H%M) — window served the slot, exiting"
        exit 0
    fi
    sleep 30 & wait $!
done
(( waited )) && log "port $PORT freed at ET $(TZ=America/New_York date +%H%M) — window died before 16:38, taking over"

cd "$APEX" || exit 1
log "starting uvicorn (no --reload), ET now $(TZ=America/New_York date +%H%M)"
APEX_SERVE=1 "$APEX/venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" >> "$LOG" 2>&1 &
PID=$!

# Hold until 16:45 ET on the wall clock; a child that dies first is logged.
while (( $(date +%s) < end_epoch )); do
    if ! kill -0 "$PID" 2>/dev/null; then
        wait "$PID"; rc=$?
        log "ended before window close (uvicorn exited rc=$rc), ET now $(TZ=America/New_York date +%H%M)"
        exit 1
    fi
    sleep 30 & wait $!
done

log "window closed (ET now $(TZ=America/New_York date +%H%M)) — SIGTERM $PID"
kill -TERM "$PID" 2>/dev/null
for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
if kill -0 "$PID" 2>/dev/null; then log "did not exit in 30s — SIGKILL"; kill -KILL "$PID"; fi
wait "$PID" 2>/dev/null
log "exit"
