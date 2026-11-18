# data/backups: kept records

The `.db` files here are git-ignored and live on this host's disk only (no off-host copy: R114, 2026-11-18).
This file records which snapshots are kept on purpose, what only they hold, and who reads them.

## apex_2026-10-08_pre-prune.db (kept, no date; R113 a, Niclas 2026-11-18)

Production before the 10-08 prune. Integrity `ok` on 2026-11-18. Open it with
`sqlite3 'file:data/backups/apex_2026-10-08_pre-prune.db?immutable=1'` (as the scripts below do) so no
`-shm`/`-wal` side files appear.

What only this file holds: 32,516 un-gated `signals` rows (`gate_decision IS NULL`), 2026-09-16 04:47 to
2026-10-07 19:50 UTC, 83 tickers: the window when `prune_signals` was silent (audit/CHECKS.md row 86). Before
09-16 the prune kept 10 un-gated rows per ticker by design (`backend/db.py` `prune_signals`), so the file holds
no more than production for that earlier period.

Readers:
- A re-run of the Finding 1 replay, `scripts/finding1_snapshot_test.py <copy of this file>` (run 2026-10-09:
  25 of 55 live entries since the 07-07 reset refused, 15 same-day pre-open and 10 earlier-day). The replay
  matches each entry to the latest signal row before it, and production no longer holds those rows.
- Any read of what the gate scored but did not pass between 09-16 and 10-07. Of the 25 stale entries, 8 fall
  in that window (all same-day pre-open); the other 17 come before it.
- Not a reader: S12's L0 card (2026-11-13) ran on the engine's price caches, not on this file.

`scripts/flag_live_signal_class.py` read it once to write `live_trades.signal_class` for history (all 110 rows
classified on 2026-11-18, 0 NULL); entries after the bound are classified from production.

## entry_fill_backfill_2026-10-13T070516.json (kept; R113 a)

Per-row before/after of the 10-13 entry-fill backfill (56 rows). Production holds both values on all 56:
`signal_price` is the quote and `entry_price` the fill (checked 2026-11-18). The pre-backfill snapshot
`apex_2026-10-13_pre-entry-fill.db` was deleted on 2026-11-18 (R113 a): it restored nothing production lacks.

## auto/ (daily copies, backend/db_backup.py, CHECK 95)

Restore tested 2026-11-18: `apex_auto_2026-11-18.db` (written 13:15 UTC) copied to a temporary file, sha256
equal to the source, `PRAGMA integrity_check` ok, all 26 tables and their schema identical to production.
13 tables were row-identical; every other difference was a write after 13:15 UTC (live_trades 104 exited at
14:33 UTC, `exits_checked_at` on 8 trades, appended history rows, signals pruned after the copy).
