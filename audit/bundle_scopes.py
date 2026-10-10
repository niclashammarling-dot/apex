"""
Proposed scopes for weekend-bundle items (2026-10-10, Niclas: "When sending an
item to Saturday or Sunday there should also be a proposed scope on how to
handle the item. If not we will have to remember how from context several days
ago.").

A scope says how to handle the item: what to read first, the options, the
likely change and how to verify it. It is written while the context is live,
by the session that understood the item, and reviewed like code. The bundle
attaches it; an item without one is flagged "no proposed scope" in the Friday
mail and on the card, so the gap shows in the week, not on Saturday.

BY_CHECK: one scope per audit CHECK (applies to all its findings).
BY_KEY:   overrides for one item (bundle item key), when a finding needs more.
BY_KIND:  defaults for parity items, by kind.
"""

BY_CHECK: dict[int, str] = {
    4: ("Config parity flags `live_account_since` as live-only. backend/maintenance.py already lists it in "
        "_LIVE_ONLY_KEYS with a reason; audit/checks_config.py CHECK 4 has no exception list, so the two disagree. "
        "Scope: make CHECK 4 read maintenance._LIVE_ONLY_KEYS (one exception list), suite. ~15 min."),
    39: ("Verified 2026-10-10 for ADI: peak_price = entry is CORRECT when the price never rose above entry "
         "(ADI entry 421.59, max poll since 408.06 / min 400.52) — the tracker only raises peak when current > peak "
         "(live_trades_tracker.py:327). CHECK 39's premise (peak = entry after 2 days ⇒ trailing stop disabled) is a "
         "false positive for a position that has only fallen. Scope: CHECK 39 flags only when max(signals.price since "
         "entry) > peak_price; positive control = the 10-10 ADI rows (must be clean) plus a synthetic row that rose. "
         "Separately for Monday: ADI is ~4-5% down, stop 6% (~396)."),
    45: ("Static analysis. `with get_db() as conn` never closes the connection: replace with "
         "`conn = get_db(); try: … finally: conn.close()` at each named line; remove the unused import. One commit, "
         "suite. ~20 min."),
    54: ("Dashboard funnel labels use the old lock numbering (FILTERED_L1 = Lock 2 Quant shown as 'L1 · Score', "
         "FILTERED_L2 = Lock 3 Sentiment shown as 'L2 · Quant'; no L3 row). Scope (ruled: frontend reflects backend): "
         "serve the lock chain from the backend (number, name, decision strings, config keys) and render adaptFunnel, "
         "the Settings labels ('Lock 1 Fallback Threshold', 'Lock 2 Sentiment Min', App.jsx:673 map) and the "
         "threshold panel from it; CHECK 54 goes clean. Half a day."),
    63: ("Composite dilution monitor (level check, standing since the ticker additions). Scope: read per-ticker "
         "scores since each addition vs the sector baseline; options per sector — keep the ticker, remove it, or "
         "convert the monitor to event severity (new crossings only; memory: level alerts become wallpaper). Decide "
         "with 64/67/68/69 together."),
    64: ("BAYES_MARGIN_SIGNAL_THRESHOLD 0.1 never reached in 21 days (max gap 0.077). Scope: find what reads "
         "bayes_conviction_leader (chain.py); if it never fires, the feature is inert — test-decide the threshold on "
         "sector_posterior_history (tests decide, not choice) or drop the feature under the profit principle."),
    67: ("Industrials composite below its critical floor after the ETN/PWR/TT additions. Scope: as CHECK 63; the "
         "sector currently has no allocation (all FILTERED_ELIGIBILITY 10-05..10-09), so no live exposure — decide "
         "ticker changes or event conversion."),
    68: ("Defense composite 0.27 vs floor 0.6 after AXON/HWM. Scope: as CHECK 63; also no allocation this week."),
    69: ("Technology composite below 0.56 after ANET. Scope: as CHECK 63 — Technology holds 5 of 9 live positions, "
         "so read this one first."),
    74: ("Tests with hardcoded absolute dates calling check_live_exits (wall-clock). Scope: make fixture dates "
         "relative to now (the d02f8ab FRZ pattern) and assert the branch the test means; suite. ~30 min."),
    76: ("Backtest engines diverge (slow vs fast: 43/43 trades, 42 common; first differing NOW 2026-05-29 "
         "END_OF_BACKTEST). Scope: engine parity is ruled first in order (engine → AMZN re-test → floor test → Kelly "
         "test); diff the two engines on that entry, fix, then close PRODUCTION_GAP.md rows and the OFF-SHAPE "
         "labels. Multi-session."),
    77: ("Historical posterior gaps (none in the trailing 3 sessions). Level monitor re-reporting old gaps. "
         "Scope: decide — convert to event (new gaps only) or accept the historical gaps with a reason; a posterior "
         "gap is replayable only from the gap forward."),
    78: ("Historical PCR gaps (none in the trailing 3). OI is a snapshot, no backfill possible. Scope: as CHECK "
         "77 — convert to event or accept with a reason."),
}

BY_KEY: dict[str, str] = {}

BY_KIND: dict[str, str] = {
    "column": ("Column written but never read. Find the writer (grep the name in backend/); decide: stop writing "
               "(keep history), wire a reader that serves a decision, or keep with a reason and an expiry "
               "(audit/bundle.py EXCEPTIONS)."),
    "table": ("Table with no reference outside backend/db.py. Find its db.py writer/reader functions and their "
              "callers; decide drop-the-writes or wire a reader; keep with a reason otherwise."),
    "route": ("Route no frontend code calls. Check scripts/tests/curl use; decide: delete (a mutating route is "
              "cross-site surface), or wire the dashboard to it, or keep with a reason."),
    "component": "Component imported nowhere. Confirm with a grep from main.jsx; delete, or import it where it belongs.",
    "function": ("Function referenced nowhere. Check dynamic use (getattr, string dispatch, scheduler by name); "
                 "delete, or wire it if it was meant to run (e.g. a broker path) — read why it was written first."),
    "audit_path": "An audit check reads a file that does not exist: retarget the check to the rendered/current file.",
    "scan_broken": "A bundle scan failed its known-present control: fix audit/bundle.py before trusting that section.",
}

CONFIG_SCOPE = ("A config value changed since the last bundle. Find the commit (git log -S on the key) and the "
                "ruling behind it (vault INDEX/notes); if none, the change was not decided — revert or rule it.")
