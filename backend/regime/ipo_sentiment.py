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
MAX_FILINGS        = 100    # max S-1 filings to process per run

# Path relative to this file: backend/regime/ → backend/ → apex(inner) → data/
CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "ipo_sentiment_cache.json"

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

    # Consumer Discretionary
    (range(5200, 5400), "Consumer Discretionary"),
    (range(5600, 5700), "Consumer Discretionary"),
    (range(5900, 5940), "Consumer Discretionary"),
    (range(7000, 7100), "Consumer Discretionary"),
    (range(7200, 7300), "Consumer Discretionary"),
    (range(3711, 3714), "Consumer Discretionary"),   # auto

    # Consumer Staples
    (range(2000, 2100), "Consumer Staples"),
    (range(2100, 2200), "Consumer Staples"),
    (range(5400, 5500), "Consumer Staples"),
    (range(5900, 5912), "Consumer Staples"),

    # Health Care
    (range(2830, 2837), "Health Care"),
    (range(3841, 3852), "Health Care"),
    (range(8000, 8100), "Health Care"),

    # Financials
    (range(6000, 6300), "Financials"),
    (range(6300, 6412), "Financials"),
    (range(6500, 6600), "Financials"),
    (range(6700, 6800), "Financials"),

    # Information Technology
    (range(3570, 3580), "Information Technology"),
    (range(3670, 3680), "Information Technology"),
    (range(7370, 7380), "Information Technology"),
    (range(3672, 3678), "Information Technology"),
    (range(3812, 3813), "Information Technology"),

    # Communication Services
    (range(4800, 4900), "Communication Services"),
    (range(7810, 7820), "Communication Services"),
    (range(7900, 7999), "Communication Services"),

    # Utilities
    (range(4900, 4940), "Utilities"),
    (range(4940, 4992), "Utilities"),

    # Real Estate
    (range(6510, 6553), "Real Estate"),
    (range(6726, 6727), "Real Estate"),
]


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
                     against apex's 11 configured sectors.
        """
        self.valid_sectors = set(sectors_cfg.keys())

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
        filings = self._fetch_edgar_filings(ref)
        logger.info(f"IpoSentiment: {len(filings)} S-1 filings found")

        # 2. Resolve SIC codes and sector for each filing
        candidates = self._resolve_filings(filings)
        logger.info(f"IpoSentiment: {len(candidates)} filings resolved with SIC codes")

        # 3. Confirm actual listings via yfinance
        confirmed = self._confirm_listings(candidates, ref)
        logger.info(f"IpoSentiment: {len(confirmed)} confirmed listings in last {IPO_WINDOW_DAYS} days")

        # 4. Compute sector shares
        result = self._compute_shares(confirmed, ref)

        # 5. Cache and return
        self._save_cache(result)
        logger.info(
            f"IpoSentiment: total={result.total_ipos} "
            f"risk_off={result.risk_off} | "
            + " | ".join(f"{s}={c}" for s, c in result.ipo_counts.items() if c > 0)
        )
        return result

    # ── EDGAR fetching ────────────────────────────────────────────────────────

    def _fetch_edgar_filings(self, ref: date) -> list[dict]:
        """
        Query EDGAR full-text search for S-1 and S-1/A filings
        in the last IPO_WINDOW_DAYS days.
        Returns list of raw filing dicts.
        """
        start = (ref - timedelta(days=IPO_WINDOW_DAYS)).isoformat()
        end   = ref.isoformat()

        filings = []
        for form_type in ("S-1", "S-1/A"):
            try:
                params = {
                    "q":         f'"{form_type}"',
                    "dateRange": "custom",
                    "startdt":   start,
                    "enddt":     end,
                    "forms":     form_type,
                    "_source":   "file_date,entity_name,file_num,period_of_report",
                    "hits.hits.total.value": MAX_FILINGS,
                }
                resp = requests.get(
                    EDGAR_SEARCH_URL,
                    params=params,
                    headers=EDGAR_HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                filings.extend(hits)
                time.sleep(REQUEST_DELAY_SEC)
            except Exception as e:
                logger.warning(f"IpoSentiment: EDGAR search failed for {form_type}: {e}")

        # Deduplicate by entity name
        seen: set[str] = set()
        unique = []
        for f in filings:
            name = f.get("_source", {}).get("entity_name", "")
            if name and name not in seen:
                seen.add(name)
                unique.append(f)

        return unique[:MAX_FILINGS]

    def _resolve_filings(self, filings: list[dict]) -> list[dict]:
        """
        For each filing, fetch the company's CIK submissions to get
        SIC code, ticker, and company name.
        Returns enriched filing dicts with sic_code and ticker.
        """
        resolved = []
        for filing in filings:
            source = filing.get("_source", {})
            name   = source.get("entity_name", "unknown")

            cik = self._extract_cik(filing.get("_id", ""), name)
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

        if risk_off:
            # Uniform shares — neutral signal for all sectors
            n = max(len(self.valid_sectors), 1)
            shares = {s: round(1.0 / n, 4) for s in self.valid_sectors}
            logger.info("IpoSentiment: risk-off detected — no IPO activity in window")
        else:
            shares = {
                s: round(count / total, 4)
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
