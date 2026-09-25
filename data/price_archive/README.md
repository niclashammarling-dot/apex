# price_archive — retained history for names the source no longer serves

Tracked deliberately. These files are the **only surviving price history** for three
tickers that have left the roster and are no longer downloadable from Yahoo. They are
committed because an irreplaceable dataset on one disk with no offsite copy is a larger
risk than the repo weight, and because git keeping binaries in history forever is the
desired property here, not a drawback.

Established 2026-09-25 by sweeping every `data/backtest_cache/*.parquet` for tickers
absent from the current roster, then probing each against Yahoo over 2025-02-01 →
2026-09-25. Eighteen off-roster names appeared; fifteen still download normally
(AA, AMZN, MP, NUE, SLB, STLD, T, VALE, XME, XOM and the OHLCV column labels).
Three do not:

| ticker | held in | rows | coverage | Yahoo today |
|---|---|---|---|---|
| BLD | both files | 341 | 2025-02-04 → 2026-06-12 | 0 rows, "possibly delisted" |
| TMHC | both files | 341 | 2025-02-04 → 2026-06-12 | 1 row (last print 2026-07-23) |
| TPH | **`919d91e598ff` only** | 320 | 2025-02-04 → 2026-05-13 | 0 rows, "possibly delisted" |

**Both files are kept: they are not duplicates.** They differ by one name each —
`84485b3bd4de` carries MTH (still downloadable, still on the roster), `919d91e598ff`
carries TPH, which is unrecoverable and appears in no other file anywhere. An earlier
reading called them the same dataset duplicated; that was wrong, and dropping either
one would have lost TPH silently.

TPH was not found by looking for it. It surfaced while comparing the two files' column
sets, having left the roster without a commit message that named it — which is the
argument for the sweep above being repeated rather than trusted as final.

`3ae6ed29265e.parquet` was copied here first and then removed: its only off-roster name
is TMHC, with zero non-null rows.

## Why this can happen at all

The roster in `data/tickers.json` does two jobs — it decides what trades *and* what gets
downloaded — so removing a name ends both, and the backtest window loses that name's
past as well as its future. Delistings are not random (they follow declines, or
acquisitions at a premium), so each removal biases the historical universe retroactively.
Separating the pricing universe from the trading universe is the structural fix; this
directory is the stopgap for history already at risk.

Note also `engine.py:667` / `engine_fast.py:848`: a cache read failure calls
`unlink(missing_ok=True)` and re-downloads. For a delisted name that deletes the only
copy and silently returns a narrower universe, under a WARNING that reads like recovery.
Quarantine-by-rename is queued as the fix.
