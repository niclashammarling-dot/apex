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

## Rule: never drop a file here as a duplicate

Do not delete a file in this archive on the strength of a label, a filename, a row count
or a size match. **Compare column sets and values first.** Deleting is the one operation
this archive cannot recover from, and the cost of being wrong is silent: the file goes,
and nothing reports which name went with it.

This is written from the near miss that produced the archive. `84485b3bd4de` and
`919d91e598ff` have identical row counts (341), identical date ranges, near-identical
sizes and 112 tickers each — every cheap signal says duplicate. They differ by one name
apiece, and one of those names is TPH, unrecoverable and held nowhere else. The proposal
on the table was to commit one file and halve the footprint; it was withdrawn only
because the column sets were compared before acting.

## Completeness: is anything already lost with no copy at all?

The sweep above finds every off-roster name that *some* cache still holds. It cannot
find a name added to the roster and removed again between cache builds, which would
leave no copy anywhere. `data/tickers.json` is fully tracked, so that set is computable:
every name ever present in any revision of it, diffed against every name held in any
cache or in this archive.

Run 2026-09-25 across all 12 revisions of `tickers.json`: **109 names have ever been on
the roster; 138 names appear in some cache; exactly one roster name has never been
cached — MA**, present in a single revision (`c1374b0`, 2026-05-05, the expansion to five
tickers per sector) and gone by the next. MA is still listed and downloads normally
(412 rows over the test window), so nothing is permanently lost through that path.

**So the archive is complete as of 2026-09-25**: the three unrecoverable names are BLD,
TMHC and TPH, all held here, and no roster name has fallen through the gap between
inclusion and caching. Re-run both sweeps after any roster removal — the completeness
check is cheap and its value is that it can return "nothing missing" honestly rather
than by omission.
