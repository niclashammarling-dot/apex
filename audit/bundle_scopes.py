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
    39: ("Open position whose peak_price lags prices seen since entry (tracker missed a higher price), or "
         "no price since entry (no feed). Scope: read live_trades_tracker's peak update for the ticker "
         "(check_live_exits → update_live_trade_peak_price) and the exit-check job's runs; if the tracker missed "
         "it, set peak from the max price since entry (snapshot first) and fix the path; if no feed, check the "
         "ticker's polls (universe, data gaps). Premise corrected 2026-10-10 (ADI false positive)."),
    45: ("Static analysis (ruff and the `with get_db()` scan). Per finding: read the line and its references "
         "before fixing. Never `ruff --fix` blind: an F401 on a re-exported name breaks its importers (10-23: "
         "lock1_eligibility's calendar aliases are imported by tests/test_lock1_macro_blackout.py; kept with a "
         "noqa and a reason). `with get_db() as conn` leaks: `conn = get_db(); try: … finally: conn.close()`. "
         "One commit, ruff clean on the touched files, full suite. ~20 min."),
    54: ("Gate lock labels have one source (backend/gate/lock_codes.py, served at /api/gate/locks; lock labels "
         "phase 1, 2026-10-26). A finding is either the table disagreeing with the approved chain (A: a filter stage's "
         "label or a missing exit_lock), or the frontend holding a vocabulary of its own (B: a FILTERED_* code paired "
         "with a label; C: a lock number written as text). Scope: render the named string from the table (as_api: "
         "labels, badges, columns, config_labels, detail_labels) or correct the table row; never add a frontend map. "
         "Verify: CHECK 54 clean, tests/test_audit_check54.py. ~30 min."),
    63: ("Composite dilution monitor (level check, standing since the ticker additions). Scope: read per-ticker "
         "scores since each addition vs the sector baseline; options per sector — keep the ticker, remove it, or "
         "convert the monitor to event severity (new crossings only; memory: level alerts become wallpaper). Decide "
         "with 64/67/68/69 together."),
    64: ("BAYES_MARGIN_SIGNAL_THRESHOLD 0.1 never reached in 21 days (max gap 0.077). Scope: find what reads "
         "bayes_conviction_leader (chain.py); if it never fires, the feature is inert — test-decide the threshold on "
         "sector_posterior_history (tests decide, not choice) or drop the feature under the profit principle."),
    65: ("Posterior ceiling pin (10-23 read: Healthcare clamped 10-19, 10-20 and 10-22, lr_ipo 4.0 → 2.4 the "
         "dominant term; 10-21 just under). Scope: read the Healthcare IPO-cluster input on the pinned days "
         "(ipo_share, which IPOs) — real IPO activity, or the 2ⁱ family amplifying a small count (apex-moc "
         "\"LR amplification is a family\"); then decide with the port-regime merge (its regime_bayes changes "
         "may move the term): no action if the merge bounds the family, else a bound on _lr_ipo_cluster with a "
         "replay of the three pinned days. A third pin within 5 sessions before then = read first in the week. ~30 min."),
    67: ("Industrials composite below its critical floor after the ETN/PWR/TT additions. Scope: as CHECK 63; the "
         "sector currently has no allocation (all FILTERED_ELIGIBILITY 10-05..10-09), so no live exposure — decide "
         "ticker changes or event conversion."),
    68: ("Defense composite 0.27 vs floor 0.6 after AXON/HWM. Scope: as CHECK 63; also no allocation this week."),
    69: ("Technology composite below 0.56 after ANET. Scope: as CHECK 63 — Technology holds 5 of 9 live positions, "
         "so read this one first."),
    74: ("Tests with hardcoded absolute dates calling check_live_exits (wall-clock). Scope: make fixture dates "
         "relative to now (the d02f8ab FRZ pattern) and assert the branch the test means; suite. ~30 min."),
    92: ("Live position protection fault. Scope: read the finding's ticker on Alpaca (get_positions, the bracket "
         "via get_order_by_id). No resting STOP leg → place_oco_exit at the trade's TP/SL and update_live_trade_order_id; "
         "stop at or above the last trade → check for a split or corporate action, re-level the leg with replace_stop_leg; "
         "short → close the short and book it as a reconciliation item. 'did not evaluate' → read the broker error. ~20 min."),
    91: ("Macro calendar ends within 60 days. Scope: add next year's FOMC dates (federalreserve.gov/monetarypolicy/"
         "fomccalendars.htm, decision day) and BLS CPI and Employment Situation dates (bls.gov/schedule/<year>) to "
         "backend/macro_calendar.py and tests/test_macro_calendar.py; check the current year's rows against the same pages. ~20 min."),
    90: ("Lock 5 prompt follow-up (rationale corrected 2026-10-17, live from 10-19). Scope: read the approval "
         "share and median notional before vs since; with about 18 L5 decisions a week (71 in the four weeks to 10-16) only a large move "
         "means anything. Read three or four L5 reasoning texts from W43 for whether they now cite the upstream "
         "allocation correctly. Decide at the 10-24 review: keep the check, re-evaluate, or retire it. ~15 min."),
    82: ("Market window interrupted. 2026-10-22 read 10-23: cause external — ISP outage (Niclas: lost ISP "
         "contact, likely weather); logs agree: NameResolutionError from 19:36 CEST, none before 19:30, yfinance "
         "\"no price data\" until 21:53; window HUP 20:04:48, relaunched 20:06:25 (97 s), so the relaunch path "
         "worked. Cost: 5 live cycles broker_unreachable (17:53–19:46 UTC), demo cycles on no fresh data; the "
         "session is flagged partial (CHECK 85 row). Scope for a new date: read the market_window log for the "
         "signal and a network-error spike in apex_<date>.log first; external cause + relaunch inside 5 min = "
         "keep with the reason; no relaunch or an internal cause = root-cause (memory: self-clearing alarms)."),
    85: ("Session flag vs coverage. Built 2026-10-24: coverage counts actionable cycles only and reads the less "
         "covered of demo and live (CHECKS.md row 85); a WARNING now is read on that count. History: "
         "2026-10-22 read 10-23: the flag is right, the check's measure is short. "
         "The ISP outage (CHECK 82 row) left the process cycling: demo coverage counts cycle starts (18/19), but "
         "live had 5 broker_unreachable cycles and demo evaluated without fresh data; the watch flagged 51 min "
         "without a live gate start. Scope: CHECK 85 counts a cycle covered only when it could act (exclude "
         "gate_cycles reason broker_unreachable; add live coverage beside demo), test with the 10-22 shape as a "
         "positive control (flag kept, no WARNING) and 10-02 unchanged. Until built: 10-22 is a correct flag. ~1 h. "
         "Definition ruled 2026-10-23 (Niclas: \"en cykel bör vara något som går att agera på\"): apply the same "
         "covered-cycle rule wherever coverage is counted (CHECK 80, the weekly report's partial-session line)."),
    89: ("Live entries without a captured fill (P&L booked on the quote). Scope: read each id's parent order "
         "(get_order_by_id); filled → scripts/backfill_entry_fills.py --apply --ids <ids> after a DB backup; "
         "partial or cancelled → decide the row (qty mismatch is a reconciliation item). If new entries keep "
         "missing, find the entry path that bypasses check_live_exits. ~20 min."),
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
