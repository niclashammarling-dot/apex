"""
ipo_sentiment.py — IPO cluster signal for apex regime module.

Fetches recent IPO listings from SEC EDGAR (public endpoints, no auth required)
and cross-references with yfinance to confirm actual first trading dates.
Computes each sector's share of total market IPO activity over the last 30 days.

Output feeds directly into regime_bayes.py as signal 4 (IPO sector share).

Pipeline:
  1. Query EDGAR full-text search for recent S-1/S-1A filings
  2. Extract CIK directly from filing ID (zero-padded 10-digit prefix)
  3. Fetch company SIC code from EDGAR submissions endpoint
  4. Cross-reference with yfinance — only count tickers with price history
     starting within last 30 days (confirmed listings, no noise)
  5. Map SIC codes to 11 GICS sectors
  6. Compute per-sector share of total market IPOs
  7. Detect risk-off if zero IPOs in window

Runs once daily at end of day. Results cached to disk so the regime module
can read the latest output without triggering a network fetch.

Usage:
    from backend.regime.ipo_sentiment import IpoSentiment

    ipo = IpoSentiment(sectors_cfg)
    result = ipo.compute()
    # result.ipo_shares  → {sector: share} for regime_bayes
    # result.risk_off    → bool, True if no IPO activity in window
    # result.context     → str summary for Claude Lock 4
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf
from loguru import logger

# ── Constants ─────────────────────────────────────────────────────────────────

EDGAR_SEARCH_URL  = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMIT_URL  = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_HEADERS     = {
    "User-Agent": "apex-trading-system contact@apex.local",
    "Accept":     "application/json",
}

IPO_WINDOW_DAYS    = 30     # lookback window for IPO listings
PRICE_HISTORY_DAYS = 35     # yfinance lookback to confirm first trading date
REQUEST_DELAY_SEC  = 0.15   # EDGAR rate limit — 10 requests/second max
MAX_FILINGS        = 300    # max S-1 filings to process per run — self-imposed cost cap,
                             # not an EDGAR limit. Raised from 100 on 2026-06-24: actual
                             # 30-day S-1 volume measured at 222, so 100 was silently
                             # truncating ~55% of filings before sector classification
                             # ever ran. 300 gives headroom above the measured volume.

EDGAR_MAX_RETRIES   = 3     # retries for transient 5xx errors before giving up
EDGAR_RETRY_BACKOFF = 2.0   # seconds, multiplied by attempt number

# Path relative to this file: backend/regime/ → backend/ → apex(inner) → data/
CACHE_PATH   = Path(__file__).parent.parent.parent / "data" / "ipo_sentiment_cache.json"
HISTORY_PATH = Path(__file__).parent.parent.parent / "data" / "ipo_sentiment_history.json"
HISTORY_MAX_DAYS = 14   # retained for CHECK 55 consecutive-zero detection

# Laplace smoothing for sector IPO share — prevents single-IPO days from producing
# share=1.0 and the resulting extreme LR in regime_bayes._lr_ipo_cluster.
# With α=1 and n active sectors, one IPO in sector X gives (1+1)/(1+n) ≈ 2/13
# instead of 1.0 — a mild nudge that scales up proportionally as total IPOs grow.
IPO_LAPLACE_ALPHA = 1

# Cache/history entries dated before this are contaminated: a redundant `q`
# param caused EDGAR to 500 on every search, an `entity_name` vs
# `display_names` field mismatch dropped every dedup'd hit, and CIKs were
# read from the filing-agent prefix instead of source.ciks — compounding to
# total_ipos=0/risk_off=True on every run regardless of actual IPO activity.
# Fixed 2026-06-15. Any backtest or analysis touching ipo_sentiment_cache
# history before this date must treat total_ipos=0 as "unknown", not "zero".
PRE_FIX_CONTAMINATION_DATE = "2026-06-15"

# Regex for standard EDGAR filing IDs: 0001234567-23-000001
# The first 10 digits are the zero-padded CIK.
_EDGAR_ID_RE = re.compile(r"^(\d{10})-\d{2}-\d{6}")


# ── SIC → GICS sector mapping ─────────────────────────────────────────────────
# Maps SEC SIC code ranges to the 11 GICS sectors used in apex.
# SIC codes: https://www.sec.gov/info/edgar/siccodes.htm

SIC_TO_SECTOR: list[tuple[range, str]] = [
    # Energy
    (range(1311, 1390), "Energy"),
    (range(1400, 1500), "Energy"),
    (range(2900, 2999), "Energy"),
    (range(4911, 4912), "Energy"),   # electric services (partial)

    # Materials
    (range(1000, 1100), "Materials"),
    (range(2600, 2700), "Materials"),
    (range(2800, 2820), "Materials"),
    (range(3300, 3400), "Materials"),

    # Industrials
    (range(3400, 3600), "Industrials"),
    (range(3700, 3720), "Industrials"),
    (range(4400, 4800), "Industrials"),
    (range(7500, 7600), "Industrials"),

    # Consumer Discretionary (ConsumerDisc)
    (range(5200, 5400), "ConsumerDisc"),
    (range(5600, 5700), "ConsumerDisc"),
    (range(5900, 5940), "ConsumerDisc"),
    (range(7000, 7100), "ConsumerDisc"),
    (range(7200, 7300), "ConsumerDisc"),
    (range(3711, 3714), "ConsumerDisc"),   # auto

    # Consumer Staples (ConsumerStaples)
    (range(2000, 2100), "ConsumerStaples"),
    (range(2100, 2200), "ConsumerStaples"),
    (range(5400, 5500), "ConsumerStaples"),
    (range(5900, 5912), "ConsumerStaples"),

    # Healthcare
    (range(2830, 2837), "Healthcare"),
    (range(3841, 3852), "Healthcare"),
    (range(8000, 8100), "Healthcare"),

    # Financials
    (range(6000, 6300), "Financials"),
    (range(6300, 6412), "Financials"),
    (range(6500, 6600), "Financials"),
    (range(6700, 6800), "Financials"),

    # Semiconductors — must precede Technology (SIC 3674 is inside Technology's 3670-3680 range)
    (range(3674, 3675), "Semiconductors"),

    # Defense — must precede Technology (SIC 3812 is defense electronics; narrow range)
    (range(3761, 3770), "Defense"),   # guided missiles & space vehicles
    (range(3812, 3813), "Defense"),   # search/detection/navigation systems

    # Technology
    (range(3570, 3580), "Technology"),
    (range(3670, 3680), "Technology"),
    (range(7370, 7380), "Technology"),
    (range(3672, 3678), "Technology"),

    # Homebuilders — SIC 1521-1532 (residential construction); no overlap with existing ranges
    (range(1521, 1533), "Homebuilders"),

    # Transportation — railroads and trucking not covered by Industrials range (4400-4800)
    (range(4011, 4014), "Transportation"),   # railroads
    (range(4210, 4216), "Transportation"),   # trucking & courier services

    # Communication
    (range(4800, 4900), "Communication"),
    (range(7810, 7820), "Communication"),
    (range(7900, 7999), "Communication"),

    # Utilities
    (range(4900, 4940), "Utilities"),
    (range(4940, 4992), "Utilities"),

    # Real Estate (RealEstate)
    (range(6510, 6553), "RealEstate"),
    (range(6726, 6727), "RealEstate"),
]


def _entity_name(source: dict) -> str:
    """
    Extract a filer name from an EFTS hit's _source dict.

    EFTS ignores the requested `_source` field list and always returns its
    standard hit shape, where the filer name lives in `display_names`
    (e.g. "Pattern Group Inc.  (PTRN)  (CIK 0001811935)"), not `entity_name`.
    """
    names = source.get("display_names") or []
    return names[0] if names else ""


def _sic_to_sector(sic_code: int) -> Optional[str]:
    """Map a SIC code to an apex sector. Returns None if unclassifiable."""
    for sic_range, sector in SIC_TO_SECTOR:
        if sic_code in sic_range:
            return sector
    return None


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class IpoListing:
    ticker:    str
    company:   str
    cik:       str
    sic_code:  int
    sector:    Optional[str]
    list_date: date


@dataclass
class IpoSentimentResult:
    date:        str
    ipo_shares:  dict[str, float]    # {sector: share_of_total} for regime_bayes
    ipo_counts:  dict[str, int]      # {sector: raw_count} for reporting
    total_ipos:  int
    risk_off:    bool                # True if no IPOs in window
    listings:    list[IpoListing]    # confirmed listings for audit
    context:     str                 # pre-formatted Lock 4 context string


# ── Main class ────────────────────────────────────────────────────────────────

class IpoSentiment:
    """
    IPO cluster signal generator for apex regime module.

    Fetches recent SEC S-1 filings, confirms listings via yfinance,
    and computes per-sector IPO share for the last 30 days.
    """

    def __init__(self, sectors_cfg: dict):
        """
        sectors_cfg: SECTORS config dict — used to validate sector names
                     against apex's tradeable sectors.

        Excludes EXCLUDED_SECTORS (Financials, Utilities, ConsumerStaples —
        no backtest-validated momentum edge, never entered): without this,
        their IPO activity inflated the share denominator and diluted the
        per-sector IPO signal for sectors that can actually be traded.
        Mirrors the filtering RegimeBayes.update() already does.
        """
        from backend.config import EXCLUDED_SECTORS
        self.valid_sectors = {s for s in sectors_cfg if s not in EXCLUDED_SECTORS}

    def compute(self, reference_date: Optional[date] = None) -> IpoSentimentResult:
        """
        Run the full IPO sentiment pipeline.
        Results are cached to disk — call this once at end of day.

        reference_date: override today for backtesting. Defaults to date.today().
        """
        ref = reference_date or date.today()
        today_str = ref.isoformat()

        # Return cached result if already computed today
        cached = self._load_cache(today_str)
        if cached:
            logger.info(f"IpoSentiment: loaded from cache ({today_str})")
            return cached

        logger.info(f"IpoSentiment: computing for {today_str}")

        # 1. Fetch S-1 filings from EDGAR
        filings, fetch_failed = self._fetch_edgar_filings(ref)
        logger.info(f"IpoSentiment: {len(filings)} S-1 filings found")

        # 2. Resolve SIC codes and sector for each filing
        candidates = self._resolve_filings(filings)
        logger.info(f"IpoSentiment: {len(candidates)} filings resolved with SIC codes")

        # 3. Confirm actual listings via yfinance
        confirmed = self._confirm_listings(candidates, ref)
        logger.info(f"IpoSentiment: {len(confirmed)} confirmed listings in last {IPO_WINDOW_DAYS} days")

        # 4. Compute sector shares
        result = self._compute_shares(confirmed, ref)

        # 5. Cache and return — but never cache a result derived from a
        # failed EDGAR fetch, or a transient outage gets baked in as a
        # full day's "risk_off" signal (data coverage gap masquerading
        # as a real regime read).
        if fetch_failed:
            logger.warning(
                "IpoSentiment: EDGAR fetch failed after retries — "
                "skipping cache write so a coverage gap isn't read as a signal"
            )
        else:
            self._save_cache(result)
        logger.info(
            f"IpoSentiment: total={result.total_ipos} "
            f"risk_off={result.risk_off} | "
            + " | ".join(f"{s}={c}" for s, c in result.ipo_counts.items() if c > 0)
        )
        return result

    # ── EDGAR fetching ────────────────────────────────────────────────────────

    def _fetch_edgar_filings(self, ref: date) -> tuple[list[dict], bool]:
        """
        Query EDGAR full-text search for S-1 and S-1/A filings
        in the last IPO_WINDOW_DAYS days.

        Transient 5xx errors are retried with backoff — EDGAR's EFTS
        endpoint intermittently returns 500s under load, and treating
        those as "zero filings" would misclassify a coverage gap as a
        risk-off signal.

        Returns (raw filing dicts, fetch_failed) — fetch_failed is True
        if any form_type's query was abandoned after exhausting retries.
        """
        start = (ref - timedelta(days=IPO_WINDOW_DAYS)).isoformat()
        end   = ref.isoformat()

        PAGE_SIZE = 10  # EDGAR EFTS fixed page size

        filings = []
        fetch_failed = False
        for form_type in ("S-1", "S-1/A"):
            offset = 0
            type_count = 0
            total_hits: Optional[int] = None  # read from first page to cap pagination
            while type_count < MAX_FILINGS:
                params = {
                    "dateRange": "custom",
                    "startdt":   start,
                    "enddt":     end,
                    "forms":     form_type,
                    "_source":   "file_date,display_names,file_num,period_of_report",
                    "from":      offset,
                }

                hits = None
                for attempt in range(1, EDGAR_MAX_RETRIES + 1):
                    try:
                        resp = requests.get(
                            EDGAR_SEARCH_URL,
                            params=params,
                            headers=EDGAR_HEADERS,
                            timeout=15,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        hits = data.get("hits", {}).get("hits", [])
                        if total_hits is None:
                            t = data.get("hits", {}).get("total", {})
                            total_hits = t.get("value") if isinstance(t, dict) else (t if isinstance(t, int) else None)
                        break
                    except requests.exceptions.HTTPError as e:
                        status = e.response.status_code if e.response is not None else None
                        if status is not None and status >= 500 and attempt < EDGAR_MAX_RETRIES:
                            logger.warning(
                                f"IpoSentiment: EDGAR search transient error for {form_type} "
                                f"(attempt {attempt}/{EDGAR_MAX_RETRIES}): {e} — retrying"
                            )
                            time.sleep(EDGAR_RETRY_BACKOFF * attempt)
                            continue
                        logger.warning(f"IpoSentiment: EDGAR search failed for {form_type}: {e}")
                        break
                    except Exception as e:
                        logger.warning(f"IpoSentiment: EDGAR search failed for {form_type}: {e}")
                        break

                if hits is None:
                    fetch_failed = True
                    break

                filings.extend(hits)
                type_count += len(hits)
                time.sleep(REQUEST_DELAY_SEC)
                if len(hits) < PAGE_SIZE:
                    break  # last page
                if total_hits is not None and offset + PAGE_SIZE >= total_hits:
                    break  # next offset would exceed result set — avoid EDGAR 500 on empty page
                offset += PAGE_SIZE

        # Deduplicate by entity name
        seen: set[str] = set()
        unique = []
        for f in filings:
            name = _entity_name(f.get("_source", {}))
            if name and name not in seen:
                seen.add(name)
                unique.append(f)

        return unique[:MAX_FILINGS], fetch_failed

    def _resolve_filings(self, filings: list[dict]) -> list[dict]:
        """
        For each filing, fetch the company's CIK submissions to get
        SIC code, ticker, and company name.
        Returns enriched filing dicts with sic_code and ticker.
        """
        resolved = []
        for filing in filings:
            source = filing.get("_source", {})
            name   = _entity_name(source) or "unknown"

            # Prefer the company's own CIK(s) from the hit source — the CIK
            # embedded in the accession number prefix (_id) is the filing
            # agent's CIK, which is often a shared third-party filer and
            # does not resolve to the issuing company's submissions.
            ciks = source.get("ciks") or []
            cik  = ciks[0] if ciks else self._extract_cik(filing.get("_id", ""), name)
            if not cik:
                continue

            try:
                url  = EDGAR_SUBMIT_URL.format(cik=cik.zfill(10))
                resp = requests.get(url, headers=EDGAR_HEADERS, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                sic_str = data.get("sic", "0")
                ticker  = (data.get("tickers") or [""])[0]
                company = data.get("name", name)

                try:
                    sic_code = int(sic_str)
                except ValueError:
                    sic_code = 0

                if sic_code > 0 and ticker:
                    resolved.append({
                        "cik":     cik,
                        "ticker":  ticker.upper(),
                        "company": company,
                        "sic":     sic_code,
                    })

                time.sleep(REQUEST_DELAY_SEC)

            except Exception as e:
                logger.debug(f"IpoSentiment: submissions fetch failed for {name}: {e}")

        return resolved

    def _extract_cik(self, filing_id: str, name: str) -> Optional[str]:
        """
        Extract CIK from a filing ID.

        Primary: standard EDGAR filing IDs start with a zero-padded 10-digit CIK
        followed by the filing year and sequence, e.g. 0001234567-23-000001.
        A regex match on this format is unambiguous and requires no network call.

        Fallback: if the ID format doesn't match, search EDGAR by company name
        and extract entity_id from the first result.
        """
        # Primary — regex on standard EDGAR ID format
        m = _EDGAR_ID_RE.match(filing_id)
        if m:
            return m.group(1)

        # Fallback — EDGAR full-text search by company name
        logger.debug(f"IpoSentiment: non-standard filing ID '{filing_id}' — falling back to name search for '{name}'")
        try:
            resp = requests.get(
                EDGAR_SEARCH_URL,
                params={"q": f'"{name}"', "forms": "S-1"},
                headers=EDGAR_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits:
                entity_id = hits[0].get("_source", {}).get("entity_id", "")
                if entity_id and entity_id.isdigit():
                    return entity_id.zfill(10)
            time.sleep(REQUEST_DELAY_SEC)
        except Exception as e:
            logger.debug(f"IpoSentiment: name search fallback failed for '{name}': {e}")
        return None

    # ── yfinance confirmation ─────────────────────────────────────────────────

    def _confirm_listings(
        self,
        candidates: list[dict],
        ref: date,
    ) -> list[IpoListing]:
        """
        Cross-reference candidates with yfinance.
        Only accept tickers where price history starts within IPO_WINDOW_DAYS.
        This confirms actual listing date and filters out noise.
        """
        confirmed: list[IpoListing] = []
        cutoff = ref - timedelta(days=IPO_WINDOW_DAYS)
        fetch_start = (ref - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()

        for c in candidates:
            ticker = c["ticker"]
            try:
                hist = yf.download(
                    ticker,
                    start=fetch_start,
                    end=ref.isoformat(),
                    auto_adjust=True,
                    progress=False,
                )
                if hist.empty:
                    continue

                first_date = hist.index[0].date()

                # Only count if first trading day is within the IPO window
                if first_date < cutoff:
                    logger.debug(f"IpoSentiment: {ticker} listed {first_date} — outside window, skipping")
                    continue

                sector = _sic_to_sector(c["sic"])
                if sector is None:
                    logger.debug(f"IpoSentiment: {ticker} SIC={c['sic']} — unclassifiable, skipping")
                    continue

                # Only include sectors configured in apex
                if sector not in self.valid_sectors:
                    continue

                confirmed.append(IpoListing(
                    ticker=ticker,
                    company=c["company"],
                    cik=c["cik"],
                    sic_code=c["sic"],
                    sector=sector,
                    list_date=first_date,
                ))
                logger.debug(f"IpoSentiment: confirmed {ticker} ({sector}) listed {first_date}")

            except Exception as e:
                logger.debug(f"IpoSentiment: yfinance check failed for {ticker}: {e}")

        return confirmed

    # ── Share computation ─────────────────────────────────────────────────────

    def _compute_shares(
        self,
        listings: list[IpoListing],
        ref: date,
    ) -> IpoSentimentResult:
        """
        Compute per-sector IPO share of total market activity.
        Handles risk-off (zero IPOs) by returning uniform shares.
        """
        today_str = ref.isoformat()

        # Count per sector
        counts: dict[str, int] = {s: 0 for s in self.valid_sectors}
        for listing in listings:
            if listing.sector:
                counts[listing.sector] = counts.get(listing.sector, 0) + 1

        total = sum(counts.values())
        risk_off = total == 0

        n = max(len(self.valid_sectors), 1)
        if risk_off:
            # Uniform shares — neutral signal for all sectors
            shares = {s: round(1.0 / n, 4) for s in self.valid_sectors}
            logger.info("IpoSentiment: risk-off detected — no IPO activity in window")
        else:
            # Laplace-smoothed shares: prevents a single IPO from claiming 100% share
            # and producing an extreme LR downstream. Signal strength scales with evidence.
            shares = {
                s: round((count + IPO_LAPLACE_ALPHA) / (total + IPO_LAPLACE_ALPHA * n), 4)
                for s, count in counts.items()
            }

        context = self._build_context(counts, shares, total, risk_off, today_str)

        return IpoSentimentResult(
            date=today_str,
            ipo_shares=shares,
            ipo_counts=counts,
            total_ipos=total,
            risk_off=risk_off,
            listings=listings,
            context=context,
        )

    # ── Context for Claude Lock 4 ─────────────────────────────────────────────

    def _build_context(
        self,
        counts: dict[str, int],
        shares: dict[str, float],
        total: int,
        risk_off: bool,
        today_str: str,
    ) -> str:
        """
        Pre-formatted IPO sentiment summary for injection into Claude Lock 4.
        """
        lines = [
            f"IPO Sentiment ({today_str}, last {IPO_WINDOW_DAYS} days):",
            f"  Total market IPOs: {total}",
        ]

        if risk_off:
            lines += [
                "  RISK-OFF SIGNAL: Zero IPO activity in window.",
                "  Interpretation: institutional capital avoiding new listings.",
                "  Consider: reduce position sizing, favour defensive sectors.",
            ]
        else:
            lines.append("  Sector distribution:")
            active = {s: c for s, c in counts.items() if c > 0}
            for sector, count in sorted(active.items(), key=lambda x: x[1], reverse=True):
                share = shares.get(sector, 0)
                bar   = "█" * count
                lines.append(f"    {sector:<25} {count:>2} IPOs  ({share:.0%})  {bar}")

        return "\n".join(lines)

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _save_cache(self, result: IpoSentimentResult) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date":       result.date,
                "ipo_shares": result.ipo_shares,
                "ipo_counts": result.ipo_counts,
                "total_ipos": result.total_ipos,
                "risk_off":   result.risk_off,
                "context":    result.context,
                "listings": [
                    {
                        "ticker":    listing.ticker,
                        "company":   listing.company,
                        "sector":    listing.sector,
                        "sic_code":  listing.sic_code,
                        "list_date": listing.list_date.isoformat(),
                    }
                    for listing in result.listings
                ],
            }
            with open(CACHE_PATH, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"IpoSentiment: cache write failed: {e}")

        self._append_history(result)

    def _append_history(self, result: IpoSentimentResult) -> None:
        """
        Append today's total_ipos to a rolling history log, used by
        CHECK 55 (nightly audit) to detect N consecutive zero-IPO days —
        a pipeline-level regression signal distinct from genuinely sparse
        IPO weeks.
        """
        try:
            entries = []
            if HISTORY_PATH.exists():
                with open(HISTORY_PATH) as f:
                    entries = json.load(f)

            entries = [e for e in entries if e.get("date") != result.date]
            entries.append({"date": result.date, "total_ipos": result.total_ipos})
            entries = sorted(entries, key=lambda e: e["date"])[-HISTORY_MAX_DAYS:]

            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_PATH, "w") as f:
                json.dump(entries, f, indent=2)
        except Exception as e:
            logger.warning(f"IpoSentiment: history write failed: {e}")

    def _load_cache(self, today_str: str) -> Optional[IpoSentimentResult]:
        try:
            if not CACHE_PATH.exists():
                return None
            with open(CACHE_PATH) as f:
                data = json.load(f)
            if data.get("date") != today_str:
                return None
            listings = [
                IpoListing(
                    ticker=entry["ticker"],
                    company=entry["company"],
                    cik="",
                    sic_code=entry["sic_code"],
                    sector=entry["sector"],
                    list_date=date.fromisoformat(entry["list_date"]),
                )
                for entry in data.get("listings", [])
            ]
            return IpoSentimentResult(
                date=data["date"],
                ipo_shares=data["ipo_shares"],
                ipo_counts=data["ipo_counts"],
                total_ipos=data["total_ipos"],
                risk_off=data["risk_off"],
                listings=listings,
                context=data["context"],
            )
        except Exception as e:
            logger.debug(f"IpoSentiment: cache read failed: {e}")
            return None


# ── Scheduler entry point ─────────────────────────────────────────────────────

def run_daily(sectors_cfg: dict) -> IpoSentimentResult:
    """
    Entry point for daily scheduler — call at end of trading day.
    Returns result for immediate use and caches for next morning's regime update.
    """
    ipo = IpoSentiment(sectors_cfg)
    return ipo.compute()
