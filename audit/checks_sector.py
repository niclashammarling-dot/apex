"""
Sector-domain mechanical checks — CHECKs 5, 27, 36, 41, 42, 43, 57, 63, 65, 71.

Covers: sector name string consistency, GICS classification parity,
L4 sub-check pass rates, new-sector integrity, sector addition completeness,
SIC_TO_SECTOR/SECTORS key parity, Healthcare dilution, posterior saturation,
and the per-sector entry-floor ceiling sweep.
"""
import json
import re
import sqlite3
from datetime import date, timedelta

from audit._audit_core import REPO, flag, require_data_file


# ── CHECK 5 — Sector name strings ─────────────────────────────────────────────

def check5():
    cfg = REPO / "backend/config.py"
    if not cfg.exists():
        return
    m = re.search(r'SECTORS\s*=\s*\[([^\]]+)\]', cfg.read_text())
    if not m:
        return
    canonical = set(re.findall(r'"([^"]+)"', m.group(1)))

    for ext in ["*.py", "*.ts", "*.tsx"]:
        for fpath in REPO.rglob(ext):
            if "node_modules" in str(fpath) or "venv" in str(fpath):
                continue
            for i, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
                for word in re.findall(r'"([A-Z][a-zA-Z]{3,})"', line):
                    if word in {
                        "Technology","Healthcare","Energy","Industrials","Financials",
                        "ConsumerDisc","ConsumerStaples","Communication","Utilities",
                        "Materials","RealEstate"
                    } - canonical:
                        rel = str(fpath.relative_to(REPO))
                        flag(5, "Sector name strings", "WARNING", f"{rel}:{i}",
                             f"'{word}' not in canonical SECTORS list")


# ── CHECK 27 — GICS sector classification parity ─────────────────────────────

def check27():
    """
    Verify each ticker in tickers.json is placed in its correct GICS sector.

    Uses a hardcoded authoritative map — the source of truth is GICS/S&P, not
    yfinance (which can lag reclassifications). Map must be updated manually when
    GICS restructurings occur (last major restructuring: September 2018).

    Prevented by: May 2026 incident where META, V, and MA were left in pre-2018
    sector assignments after the 2018 GICS restructuring.
    """
    GICS_MAP = {
        # Technology (XLK) — V post-2018 GICS stays in IT (payment networks reclassified)
        "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
        "AMD": "Technology",  "V": "Technology",    "AVGO": "Technology",
        "CRM": "Technology",  "ORCL": "Technology", "ADBE": "Technology",
        "NOW": "Technology",  "QCOM": "Technology", "ANET": "Technology",  # added 2026-08-08
        # Healthcare (XLV)
        "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
        "MRNA": "Healthcare", "LLY": "Healthcare",
        "ISRG": "Healthcare", "TMO": "Healthcare",
        # Energy (XLE)
        "EOG": "Energy", "CVX": "Energy", "HAL": "Energy",
        "COP": "Energy", "OXY": "Energy",
        # Industrials (XLI) — ETN/PWR/TT added 2026-08-08
        "CAT": "Industrials", "BA": "Industrials", "GE": "Industrials",
        "HON": "Industrials", "DE": "Industrials",
        "ETN": "Industrials", "PWR": "Industrials", "TT": "Industrials",
        # Financials (XLF)
        "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
        "BLK": "Financials", "MS": "Financials",
        # ConsumerDisc (XLY) — BKNG added 2026-08-08; AMZN removed 2026-05-22
        # (do-not-resuggest: PF 0.118/8 trades, signal-architecture incompatibility —
        # see [[2026-05-22-apex-consumerdic-amzn-removal-floor-correction]])
        "TSLA": "ConsumerDisc", "NKE": "ConsumerDisc",
        "MCD": "ConsumerDisc", "HD": "ConsumerDisc", "BKNG": "ConsumerDisc",
        # ConsumerStaples (XLP)
        "PG": "ConsumerStaples", "KO": "ConsumerStaples", "PEP": "ConsumerStaples",
        "WMT": "ConsumerStaples", "COST": "ConsumerStaples",
        # Communication (XLC) — META and GOOGL post-2018 restructuring
        "GOOGL": "Communication", "META": "Communication", "NFLX": "Communication",
        "DIS": "Communication",   "SPOT": "Communication",
        # Utilities (XLU)
        "DUK": "Utilities", "SO": "Utilities", "AEP": "Utilities",
        "EXC": "Utilities", "NEE": "Utilities",
        # Materials (XLB)
        "LIN": "Materials", "APD": "Materials", "NEM": "Materials",
        "FCX": "Materials", "SHW": "Materials",
        # RealEstate (XLRE)
        "PLD": "RealEstate", "AMT": "RealEstate", "EQIX": "RealEstate",
        "SPG": "RealEstate", "WELL": "RealEstate", "DLR": "RealEstate",
        "O": "RealEstate",   "VTR": "RealEstate",  "PSA": "RealEstate",
        # Semiconductors (SOXX) — added 2026-05-17; expanded 2026-06-14
        "ASML": "Semiconductors", "AMAT": "Semiconductors", "LRCX": "Semiconductors",
        "KLAC": "Semiconductors", "MU": "Semiconductors",
        "MRVL": "Semiconductors", "ON": "Semiconductors", "TER": "Semiconductors",
        "ADI": "Semiconductors",
        # Defense (ITA) — added 2026-05-17; AXON/HWM added 2026-08-08
        # (both GICS industry = Aerospace & Defense, confirmed via yfinance;
        # AXON was misassigned to Industrials in the original candidate list —
        # caught before wiring, same class as the 2026-05-06 V/MA incident)
        "LMT": "Defense", "RTX": "Defense", "NOC": "Defense",
        "GD": "Defense",  "HII": "Defense",
        "AXON": "Defense", "HWM": "Defense",
        # Homebuilders (ITB) — added 2026-05-18; expanded 2026-06-14; GICS: Consumer Discretionary sub-industry
        "DHI": "Homebuilders", "LEN": "Homebuilders", "PHM": "Homebuilders",
        "TOL": "Homebuilders", "NVR": "Homebuilders",
        "MHO": "Homebuilders", "TMHC": "Homebuilders",
        "MTH": "Homebuilders",
        # Transportation (IYT) — added 2026-05-18; expanded 2026-06-14; GICS: Industrials sub-industry
        "UNP": "Transportation", "CSX": "Transportation", "FDX": "Transportation",
        "UPS": "Transportation", "JBHT": "Transportation",
        "WAB": "Transportation", "ODFL": "Transportation", "CHRW": "Transportation",
        "DAL": "Transportation",
    }

    tickers_path = REPO / "data/tickers.json"
    if not tickers_path.exists():
        flag(27, "GICS sector classification parity", "CRITICAL",
             "data/tickers.json", "tickers.json not found")
        return

    universe = json.loads(tickers_path.read_text())

    for apex_sector, meta in universe.items():
        for ticker in meta.get("tickers", []):
            expected = GICS_MAP.get(ticker)
            if expected is None:
                flag(27, "GICS sector classification parity", "WARN",
                     "data/tickers.json",
                     f"{ticker} has no GICS entry in the audit map — add it when onboarding new tickers")
            elif expected != apex_sector:
                flag(27, "GICS sector classification parity", "CRITICAL",
                     "data/tickers.json",
                     f"{ticker} is in APEX sector '{apex_sector}' but GICS assigns it to '{expected}'; "
                     f"Lock 4 ETF benchmark and sector score will be wrong")


# ── CHECK 36 — L4 sub-check pass rates ───────────────────────────────────────

def check36():
    """
    L4 sub-check pass rates must all be above a dead-weight floor.

    A 2-of-N gate can absorb a permanently-failing sub-check without surfacing
    it in aggregate pass rate. L4 ran as 2-of-3 for its entire operational life
    while the architecture said 2-of-4 — insider_cluster had 0% pass rate over
    101 evaluations, invisible at the gate level.

    Flag any sub-check with <5% pass rate when at least MIN_OBS observations exist.
    """
    MIN_OBS        = 10
    DEAD_THRESHOLD = 0.05
    WINDOW_DAYS    = 30

    db = REPO / "data/apex.db"
    if not require_data_file(36, "L4 sub-check pass rates", db):
        return

    cutoff = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()

    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT lock_leading_checks FROM signals "
            "WHERE lock_leading_checks IS NOT NULL AND timestamp >= ?",
            (cutoff,)
        ).fetchall()
        conn.close()
    except Exception as e:
        flag(36, "L4 sub-check pass rates", "WARNING", "data/apex.db:signals",
             f"could not query signals table: {e}")
        return

    if not rows:
        return

    counts: dict = {}
    for (raw,) in rows:
        try:
            checks = json.loads(raw)
        except Exception:
            continue
        for name, result in checks.items():
            if name not in counts:
                counts[name] = [0, 0]
            counts[name][0] += 1
            if isinstance(result, dict) and result.get("pass"):
                counts[name][1] += 1

    for name, (total, passes) in counts.items():
        if total < MIN_OBS:
            continue
        rate = passes / total
        if rate < DEAD_THRESHOLD:
            flag(36, "L4 sub-check pass rates", "WARNING",
                 "data/apex.db:signals",
                 f"L4 sub-check '{name}' passed {passes}/{total} times "
                 f"({rate*100:.1f}%) over {WINDOW_DAYS}d — likely dead weight; "
                 f"review base-rate assumption for the APEX universe")


# ── CHECK 41 — New-sector integrity (Semiconductors, Defense) ─────────────────

def check41():
    """
    New-sector integrity — Semiconductors and Defense.

    Three assertions:
      A. "Semiconductors" in config.SECTORS with ETF=SOXX.
      B. "Defense" in config.SECTORS with ETF=ITA.
      C. Neither sector appears in EXCLUDED_SECTORS.

    Prevented by: 2026-05-17 sector expansion sweep. Both sectors passed PF > 1.0
    at baseline L1=0.70. Isolated threshold sweep (2026-05-18) confirmed:
    Semiconductors PF 1.431 at 0.70 (all tickers positive); Defense PF 1.692 at
    0.70, floor at 0.75 removed (22 trades, portfolio-sweep contamination).
    Silent removal or ETF drift would resume evaluating them under the wrong
    regime signal.
    """
    config_path = REPO / "backend/config.py"
    config_text = config_path.read_text()

    if '"Semiconductors"' not in config_text:
        flag(41, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             '"Semiconductors" missing from config.SECTORS — sector removed or renamed')
    elif '"SOXX"' not in config_text:
        flag(41, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             'Semiconductors ETF is not SOXX — regime signal will track wrong benchmark')

    if '"Defense"' not in config_text:
        flag(41, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             '"Defense" missing from config.SECTORS — sector removed or renamed')
    elif '"ITA"' not in config_text:
        flag(41, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             'Defense ETF is not ITA — regime signal will track wrong benchmark')

    excl_start = config_text.find("EXCLUDED_SECTORS")
    excl_end   = config_text.find("}", excl_start)
    excl_block = config_text[excl_start:excl_end] if excl_start >= 0 else ""
    for sector in ("Semiconductors", "Defense"):
        if f'"{sector}"' in excl_block:
            flag(41, "New-sector integrity", "CRITICAL",
                 "backend/config.py",
                 f'"{sector}" found in EXCLUDED_SECTORS — sweep-validated sector blocked at L1')


# ── CHECK 42 — New-sector integrity (Homebuilders, Transportation) ────────────

def check42():
    """
    New-sector integrity — Homebuilders and Transportation.

    Three assertions:
      A. "Homebuilders" in config.SECTORS with ETF=ITB.
      B. "Transportation" in config.SECTORS with ETF=IYT.
      C. Neither sector appears in EXCLUDED_SECTORS.

    No SECTOR_THRESHOLD_FLOORS entries for either sector — both run at baseline
    0.70. Isolated sweep (2026-05-18): Homebuilders PF 1.727 at 0.70 (126 trades,
    5-year distributed edge); Transportation PF 1.47 at 0.70 (17 trades).
    Portfolio sweep non-monotonicity confirmed as slot-competition artifact —
    isolated sweep is the validated methodology for per-sector threshold decisions.
    """
    config_path = REPO / "backend/config.py"
    config_text = config_path.read_text()

    if '"Homebuilders"' not in config_text:
        flag(42, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             '"Homebuilders" missing from config.SECTORS — sector removed or renamed')
    elif '"ITB"' not in config_text:
        flag(42, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             'Homebuilders ETF is not ITB — regime signal will track wrong benchmark')

    if '"Transportation"' not in config_text:
        flag(42, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             '"Transportation" missing from config.SECTORS — sector removed or renamed')
    elif '"IYT"' not in config_text:
        flag(42, "New-sector integrity", "CRITICAL",
             "backend/config.py",
             'Transportation ETF is not IYT — regime signal will track wrong benchmark')

    excl_start = config_text.find("EXCLUDED_SECTORS")
    excl_end   = config_text.find("}", excl_start)
    excl_block = config_text[excl_start:excl_end] if excl_start >= 0 else ""
    for sector in ("Homebuilders", "Transportation"):
        if f'"{sector}"' in excl_block:
            flag(42, "New-sector integrity", "CRITICAL",
                 "backend/config.py",
                 f'"{sector}" found in EXCLUDED_SECTORS — sweep-validated sector blocked at L1')


# ── CHECK 43 — Sector addition completeness ───────────────────────────────────

def check43():
    """
    Verify that every sector added to config.SECTORS is fully wired into all
    dependent systems. Two sub-checks:

      A. Company names — every ticker in config.SECTORS has an entry in
         frontend/src/companyNames.js. Missing names silently show blank/ticker-only
         labels in the sector grid, trade log, watchlist, and positions views.

      B. Regime classification — every sector in config.SECTORS appears in either
         CYCLICAL or DEFENSIVE in backend/sector_regime.py. Unclassified sectors get
         regime_alignment=0.1 (worst possible rotation score) in all market conditions
         and are excluded from cyclical/defensive avg in regime detection.

    Prevented by: 2026-05-23 incident where Homebuilders and Transportation were added
    to SECTORS and the frontend grid but missed companyNames.js and CYCLICAL, causing
    blank company labels and permanent regime_alignment=0.1 for both sectors.
    """
    import ast

    # ── Sub-check A: company names ─────────────────────────────────────────────
    config_path = REPO / "backend/config.py"
    names_path  = REPO / "frontend/src/companyNames.js"

    config_text = config_path.read_text()
    # Extract SECTORS block — find the dict literal
    sectors_match = re.search(r'SECTORS\s*=\s*\{', config_text)
    if sectors_match:
        # Collect all tickers from "tickers": [...] lines within SECTORS
        tickers_in_sectors = set(re.findall(r'"([A-Z]{1,5})"', config_text[sectors_match.start():]))
        # Remove sector names and ETF-looking values — keep only ticker-like uppercase strings
        # Heuristic: ETFs are 2-4 chars; tickers can be 1-5 chars. We rely on the GICS map
        # in check27 for sector assignment; here we just need all quoted uppercase strings
        # that are actual tickers (not sector names or ETFs). Pull from tickers: arrays only.
        tickers_in_sectors = set()
        for m in re.finditer(r'"tickers"\s*:\s*\[([^\]]+)\]', config_text):
            tickers_in_sectors.update(re.findall(r'"([A-Z]{1,5})"', m.group(1)))
    else:
        tickers_in_sectors = set()

    if names_path.exists():
        names_text = names_path.read_text()
        for ticker in sorted(tickers_in_sectors):
            if f"{ticker}:" not in names_text:
                flag(43, "Sector addition completeness", "WARNING",
                     "frontend/src/companyNames.js",
                     f'ticker {ticker} has no company name entry — blank label in grid, trade log, and positions views')

    # ── Sub-check B: regime classification ────────────────────────────────────
    regime_path = REPO / "backend/sector_regime.py"
    regime_text = regime_path.read_text()

    cyclical_match  = re.search(r'CYCLICAL\s*=\s*\{([^}]+)\}',  regime_text)
    defensive_match = re.search(r'DEFENSIVE\s*=\s*\{([^}]+)\}', regime_text)
    cyclical  = set(re.findall(r'"([^"]+)"', cyclical_match.group(1)))  if cyclical_match  else set()
    defensive = set(re.findall(r'"([^"]+)"', defensive_match.group(1))) if defensive_match else set()
    classified = cyclical | defensive

    # Extract sector names from the SECTORS = { ... } block only
    # Slice from "SECTORS = {" to the matching closing brace (first "}" at column 0)
    sectors_start = config_text.find("SECTORS = {")
    sectors_block = ""
    if sectors_start >= 0:
        depth = 0
        for i, ch in enumerate(config_text[sectors_start:], sectors_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    sectors_block = config_text[sectors_start:i + 1]
                    break
    sector_names = set(re.findall(r'^\s+"([A-Za-z]+)"\s*:', sectors_block, re.MULTILINE))
    # SECTORS top-level keys are TitleCase words; inner keys are "etf" and "tickers" (lowercase)
    sector_names = {s for s in sector_names if s[0].isupper()}

    for sector in sorted(sector_names):
        if sector not in classified:
            flag(43, "Sector addition completeness", "CRITICAL",
                 "backend/sector_regime.py",
                 f'sector "{sector}" not in CYCLICAL or DEFENSIVE — regime_alignment=0.1 '
                 f'in all non-neutral regimes; excluded from cyclical/defensive avg')


# ── CHECK 57 — SIC_TO_SECTOR / SECTORS parity ────────────────────────────────

def check57():
    """
    Assert every sector name used in SIC_TO_SECTOR (backend/regime/ipo_sentiment.py)
    exists as a key in SECTORS (backend/config.py), and flag any SECTORS key with
    zero SIC mappings.

    Prevented: SIC_TO_SECTOR used GICS names ("Health Care", "Information Technology",
    "Communication Services", "Real Estate", "Consumer Discretionary", "Consumer
    Staples") while SECTORS uses apex's own keys ("Healthcare", "Technology",
    "Communication", "RealEstate", "ConsumerDisc", "ConsumerStaples"). The mismatch
    caused _confirm_listings to silently drop every IPO in 6 of 11 sectors —
    total_ipos read 3 when it should have been 8, with no error anywhere.
    """
    try:
        from backend.config import SECTORS
        from backend.regime.ipo_sentiment import SIC_TO_SECTOR
    except Exception as e:
        flag(57, "SIC_TO_SECTOR/SECTORS parity", "WARNING",
             "backend/regime/ipo_sentiment.py",
             f"could not import SIC_TO_SECTOR/SECTORS: {e}")
        return

    sector_keys = set(SECTORS.keys())
    sic_sectors = {s for _, s in SIC_TO_SECTOR}

    unknown = sic_sectors - sector_keys
    if unknown:
        flag(57, "SIC_TO_SECTOR/SECTORS parity", "CRITICAL",
             "backend/regime/ipo_sentiment.py",
             f"SIC_TO_SECTOR references sector(s) not in SECTORS: {sorted(unknown)} — "
             f"_confirm_listings silently drops these IPOs (config.SECTORS={sorted(sector_keys)})")

    unmapped = sector_keys - sic_sectors
    if unmapped:
        flag(57, "SIC_TO_SECTOR/SECTORS parity", "INFO",
             "backend/regime/ipo_sentiment.py",
             f"SECTORS key(s) with no SIC_TO_SECTOR mapping: {sorted(unmapped)} — "
             f"these sectors can never register IPO activity")


# ── CHECK 63 — Healthcare composite dilution monitor ──────────────────────────


# ── Market-wide-decline suppression for the dilution monitors (2026-09-11) ────
# CHECKs 63/67/68/69 measure one thing: did adding tickers to a sector dilute
# its composite avg_score below a floor pinned to the pre-addition baseline?
# Since 2026-08-10 they have been firing on something else — a market-wide
# composite decline across most sectors (found 2026-09-01 by querying
# sector_snapshots directly; 8 of 11 sectors with data sat below 75% of their
# own 60-day peak on 2026-09-11, Defense at 25%). A dilution diagnosis in that
# regime is a confound, and nightly CRITICAL emails that are true-but-
# misattributed train the reader to ignore the channel.
#
# Discharge condition is mechanical, not remembered: while at least half of
# the sectors with data are "depressed" (current avg_score < DEPRESSED_RATIO ×
# their own trailing PEAK_WINDOW_DAYS peak), dilution findings are recorded at
# severity SUPPRESSED — still a triggered row (the floor IS breached, the
# registry's last_triggered still advances), but not a CRITICAL/WARNING, so
# no alert email and no fixer pass. When the market recovers and fewer than
# half the sectors are depressed, the same findings return to CRITICAL/WARNING
# with no one having to lift anything.

DEPRESSED_RATIO   = 0.75
PEAK_WINDOW_DAYS  = 60
_decline_cache: dict = {}


def _market_wide_decline() -> tuple[bool, str]:
    """Return (suppress?, one-line evidence). Cached per run."""
    if "v" in _decline_cache:
        return _decline_cache["v"]
    db = REPO / "data/apex.db"
    result = (False, "no data")
    if db.exists():
        try:
            conn = sqlite3.connect(db)
            rows = conn.execute("""
                WITH cur AS (
                    SELECT sector, avg_score FROM sector_snapshots s
                    WHERE timestamp = (SELECT MAX(timestamp) FROM sector_snapshots
                                       WHERE sector = s.sector)
                ), pk AS (
                    SELECT sector, MAX(avg_score) AS peak FROM sector_snapshots
                    WHERE timestamp >= date('now', ?) GROUP BY sector
                )
                SELECT cur.sector, cur.avg_score, pk.peak
                FROM cur JOIN pk USING (sector) WHERE pk.peak > 0
            """, (f"-{PEAK_WINDOW_DAYS} days",)).fetchall()
            conn.close()
            depressed = [r[0] for r in rows if r[1] < DEPRESSED_RATIO * r[2]]
            total = len(rows)
            suppress = total > 0 and len(depressed) * 2 >= total
            result = (suppress,
                      f"{len(depressed)}/{total} sectors below {DEPRESSED_RATIO:.0%} of their "
                      f"{PEAK_WINDOW_DAYS}d peak ({', '.join(sorted(depressed))})")
        except Exception as e:
            result = (False, f"decline query failed: {e}")
    _decline_cache["v"] = result
    return result


def _dilution_flag(check_num: int, name: str, sev: str, file_line: str, finding: str) -> None:
    suppress, evidence = _market_wide_decline()
    if suppress and sev in ("CRITICAL", "WARNING"):
        flag(check_num, name, "SUPPRESSED", file_line,
             f"[{sev} suppressed — market-wide decline: {evidence}. This monitor measures "
             f"ticker-addition dilution; the floor breach is currently attributable to the "
             f"broad decline, not the additions. Lifts itself when fewer than half the sectors "
             f"are depressed.] {finding}")
    else:
        flag(check_num, name, sev, file_line, finding)


def check63():
    """
    Monitor Healthcare avg_score for dilution from the ISRG/TMO addition (2026-07-01).

    Baseline: 0.6736 — Healthcare avg_score with the original 5-ticker list, recorded
    2026-07-01 immediately before adding ISRG and TMO.  This baseline is FIXED and
    must not become a rolling window; a sustained drop below it IS the dilution signal,
    not noise to be averaged away.

    Thresholds (applied to the most recent sector_snapshots reading):
      - avg_score < 0.60  → WARNING: meaningful dilution from 0.6736 baseline;
        investigate ISRG/TMO signal quality before the next gate cycle.
      - avg_score < 0.55  → CRITICAL: at a neutral posterior (~0.55), adjusted_score ≈ 0.30,
        below ALLOCATION_ENTRY_THRESHOLD (0.37) — floor failures likely in live gate.
        Action: remove ISRG and TMO via ticker_config.remove_ticker() and diagnose
        signal history before re-adding.

    Single-day readings below threshold are meaningful (threshold gap from baseline is
    wide enough that noise does not reach it).  Retires when Healthcare's post-addition
    composite has been stable above WARN_FLOOR + 0.02 (currently 0.62) for 30 consecutive
    trading days.  SYK addition is gated on the same condition — if WARN_FLOOR changes,
    update the SYK/ABBV revisit trigger in CHECKS.md to match.
    """
    BASELINE_VALUE = 0.6736
    BASELINE_DATE  = "2026-07-01"
    WARN_FLOOR     = 0.60
    CRIT_FLOOR     = 0.55

    db = REPO / "data/apex.db"
    if not require_data_file(63, "Healthcare composite dilution monitor", db):
        return

    try:
        conn = sqlite3.connect(db)
        row = conn.execute("""
            SELECT avg_score, timestamp
            FROM sector_snapshots
            WHERE sector = 'Healthcare'
            ORDER BY timestamp DESC
            LIMIT 1
        """).fetchone()
        conn.close()
    except Exception as e:
        flag(63, "Healthcare composite dilution monitor", "WARNING",
             "data/apex.db:sector_snapshots",
             f"could not query sector_snapshots: {e}")
        return

    if row is None:
        return

    avg_score, ts = row[0], row[1]

    if avg_score < CRIT_FLOOR:
        _dilution_flag(63, "Healthcare composite dilution monitor", "CRITICAL",
             "data/apex.db:sector_snapshots",
             f"Healthcare avg_score={avg_score:.4f} (at {ts}) is below critical floor {CRIT_FLOOR}; "
             f"baseline was {BASELINE_VALUE} on {BASELINE_DATE} (pre-ISRG/TMO). "
             f"At neutral posterior (~0.55), adjusted_score ≈ {avg_score * 0.55:.3f}, "
             f"below ALLOCATION_ENTRY_THRESHOLD 0.37. Action: remove ISRG and TMO via "
             f"ticker_config.remove_ticker() and diagnose signal history before re-adding.")
    elif avg_score < WARN_FLOOR:
        _dilution_flag(63, "Healthcare composite dilution monitor", "WARNING",
             "data/apex.db:sector_snapshots",
             f"Healthcare avg_score={avg_score:.4f} (at {ts}) dropped below {WARN_FLOOR}; "
             f"baseline was {BASELINE_VALUE} on {BASELINE_DATE} (pre-ISRG/TMO). "
             f"Investigate ISRG/TMO signal quality in sector_snapshots before next gate cycle.")


# ── CHECKs 67/68/69 — 2026-08-08 sector-addition dilution monitors ───────────
# Same instrument as CHECK 63, generalized. Each of the three sectors touched
# by the 2026-08-08 expansion got at least one MIXED-shape candidate (weak 20d/
# strong 90d excess vs. its own sector ETF) — the confirmation-window condition
# on those tickers is this monitor staying clear, mirroring the SYK/CHECK 63
# gating precedent. Fixed baselines pinned immediately before addition.

def _dilution_monitor(check_num: int, sector: str, added: str, baseline: float,
                       baseline_date: str, warn_floor: float, crit_floor: float,
                       confirm_note: str) -> None:
    db = REPO / "data/apex.db"
    if not require_data_file(check_num, f"{sector} composite dilution monitor", db):
        return
    try:
        conn = sqlite3.connect(db)
        row = conn.execute("""
            SELECT avg_score, timestamp
            FROM sector_snapshots
            WHERE sector = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (sector,)).fetchone()
        conn.close()
    except Exception as e:
        flag(check_num, f"{sector} composite dilution monitor", "WARNING",
             "data/apex.db:sector_snapshots", f"could not query sector_snapshots: {e}")
        return

    if row is None:
        return
    avg_score, ts = row[0], row[1]

    if avg_score < crit_floor:
        _dilution_flag(check_num, f"{sector} composite dilution monitor", "CRITICAL",
             "data/apex.db:sector_snapshots",
             f"{sector} avg_score={avg_score:.4f} (at {ts}) is below critical floor {crit_floor}; "
             f"baseline was {baseline} on {baseline_date} (pre-{added}). "
             f"At neutral posterior (~0.55), adjusted_score ≈ {avg_score * 0.55:.3f}, "
             f"below ALLOCATION_ENTRY_THRESHOLD 0.37. Action: remove {added} via "
             f"ticker_config.remove_ticker() and diagnose signal history before re-adding.")
    elif avg_score < warn_floor:
        _dilution_flag(check_num, f"{sector} composite dilution monitor", "WARNING",
             "data/apex.db:sector_snapshots",
             f"{sector} avg_score={avg_score:.4f} (at {ts}) dropped below {warn_floor}; "
             f"baseline was {baseline} on {baseline_date} (pre-{added}). "
             f"Investigate {added} signal quality in sector_snapshots before next gate cycle. "
             f"{confirm_note}")


def check67():
    """
    Industrials composite dilution monitor — ETN/PWR/TT added 2026-08-08 (5→8 tickers).

    Baseline: 0.6009, pinned 2026-08-08 immediately before addition. WARNING < 0.53,
    CRITICAL < 0.48 (baseline-relative gap matches CHECK 63's ~0.07/~0.12 spacing).
    TT is the confirmation-flagged ticker in this sector (weak-20d/strong-90d shape
    vs XLI — pullback-in-trend read, not yet confirmed). Retires with CHECK 68/69
    under the shared 30-consecutive-trading-day-above-WARN_FLOOR+0.02 condition.
    """
    _dilution_monitor(67, "Industrials", "ETN/PWR/TT", 0.6009, "2026-08-08",
                       warn_floor=0.53, crit_floor=0.48,
                       confirm_note="TT's confirmation-window flag depends on this staying clear.")


def check68():
    """
    Defense composite dilution monitor — AXON/HWM added 2026-08-08 (5→7 tickers).

    Baseline: 0.7169, pinned 2026-08-08 immediately before addition. WARNING < 0.65,
    CRITICAL < 0.60. Both AXON and HWM are confirmation-flagged (weak-20d/strong-90d
    shape vs ITA). Note AXON was corrected from Industrials to Defense at addition
    time — GICS industry is Aerospace & Defense per yfinance, same as HWM/LMT/RTX/NOC/GD/HII;
    the original candidate list had it misclassified.
    """
    _dilution_monitor(68, "Defense", "AXON/HWM", 0.7169, "2026-08-08",
                       warn_floor=0.65, crit_floor=0.60,
                       confirm_note="AXON's and HWM's confirmation-window flags depend on this staying clear.")


def check69():
    """
    Technology composite dilution monitor — ANET added 2026-08-08 (11→12 tickers).

    Baseline: 0.6257, pinned 2026-08-08 immediately before addition. WARNING < 0.56,
    CRITICAL < 0.51. ANET is confirmation-flagged (weak-20d/strong-90d shape vs XLK).
    """
    _dilution_monitor(69, "Technology", "ANET", 0.6257, "2026-08-08",
                       warn_floor=0.56, crit_floor=0.51,
                       confirm_note="ANET's confirmation-window flag depends on this staying clear.")


# ── CHECK 65 — Posterior saturation monitor ───────────────────────────────────

def check65():
    """
    Flag any sector whose pre_clamp_posterior exceeded the clamp ceiling (0.95) in the
    most recent regime run, or that is approaching it (> 0.90) while aggregate is weak.

    Reads regime_signal_trace.jsonl — appended daily by regime_bayes.py. The stored
    posterior in sector_posterior_history is always the *clamped* value; only the
    pre_clamp_posterior field in the trace reveals when the clamp is binding.

    Thresholds:
      - clamp_binding=True: CRITICAL — a signal formula delivered a raw posterior above
        0.95, overriding all other signals. The clamp fired; investigate which LR caused
        it (lr_ipo_raw field is most commonly the culprit).
      - pre_clamp_posterior > 0.90 with aggregate_score < 0.55: WARNING — approaching
        saturation with weak sector momentum; conviction/momentum divergence, potential
        early rotation candidate.

    Fires on the latest date present in the trace file.
    Silently skips if the file does not exist (regime has not run since the JSONL
    feature was added — no false alarm on first deploy).
    """
    trace_path = REPO / "data" / "regime_signal_trace.jsonl"
    if not require_data_file(65, "posterior saturation monitor", trace_path):
        return

    try:
        lines = trace_path.read_text().strip().splitlines()
    except Exception as e:
        flag(65, "posterior saturation monitor", "WARNING",
             "data/regime_signal_trace.jsonl",
             f"could not read trace file: {e}")
        return

    if not lines:
        return

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

    if not records:
        return

    latest_date = max(r.get("date", "") for r in records)
    today_records = [r for r in records if r.get("date") == latest_date]

    for r in today_records:
        sector        = r.get("sector", "?")
        clamp_binding = r.get("clamp_binding", False)
        pre_clamp     = r.get("pre_clamp_posterior")
        aggregate     = r.get("aggregate_score", 1.0)
        lr_ipo_raw    = r.get("lr_ipo_raw")
        ipo_share     = r.get("ipo_share")

        # clamp_binding is two-sided: the floor (0.05) binding on a depressed
        # sector is the regime gate working, not saturation. Only the ceiling
        # side is this check's subject (first real-data run 2026-09-11 flagged
        # six floor-clamps as CRITICAL "exceeded ceiling" at posterior 0.04).
        if clamp_binding and pre_clamp is not None and pre_clamp < 0.5:
            continue

        if clamp_binding:
            detail = (
                f"{sector}: pre_clamp_posterior={pre_clamp:.4f} exceeded clamp ceiling 0.95 "
                f"on {latest_date}. "
            )
            if lr_ipo_raw is not None and lr_ipo_raw > 10.0:
                detail += (
                    f"lr_ipo_raw={lr_ipo_raw:.1f} (ipo_share={ipo_share}) was the trigger — "
                    f"check ipo_sentiment smoothing or LR cap."
                )
            else:
                detail += f"lr_ipo_raw={lr_ipo_raw}; check all LR formulas for uncapped range."
            flag(65, "posterior saturation monitor", "CRITICAL",
                 "data/regime_signal_trace.jsonl", detail)

        elif pre_clamp is not None and pre_clamp > 0.90 and aggregate < 0.55:
            flag(65, "posterior saturation monitor", "WARNING",
                 "data/regime_signal_trace.jsonl",
                 f"{sector}: pre_clamp_posterior={pre_clamp:.4f} approaching ceiling with "
                 f"aggregate_score={aggregate:.4f} on {latest_date} — "
                 f"conviction/momentum divergence; potential rotation signal.")


# ── CHECK 71 — Per-sector posterior-ceiling entry-floor sweep ─────────────────

# Thresholds are this check's own, not the model's — surfaced here and in
# CHECKS.md so a change is a visible decision, not a silent edit.
CHECK71_DARK_SHARE_WARN   = 0.50  # WARNING when more than half the eligible sectors are dark
CHECK71_ENTERABLE_CRIT    = 2     # CRITICAL when this many or fewer sectors can enter at all
CHECK71_PERSIST_SHARE     = 0.80  # "structural" = dark on at least this share of trace dates
CHECK71_WINDOW_DATES      = 30    # most recent trace dates considered for persistence


def _entry_floor_sweep(records: list[dict], entry_threshold: float, posterior_ceil: float,
                       window: int = CHECK71_WINDOW_DATES) -> dict:
    """
    Pure core of CHECK 71. `records` are regime_signal_trace.jsonl rows.

    A sector is *dark* on a date when aggregate_score × posterior_ceil < entry_threshold:
    no posterior — not even the clamp ceiling — can lift it over the entry floor, so the
    regime gate is not the binding constraint; the sector's own composite is. Duplicate
    (date, sector) rows (same-day re-runs) collapse to the last one written.
    """
    latest: dict[tuple[str, str], dict] = {}
    for r in records:
        d, sec = r.get("date"), r.get("sector")
        if d and sec and isinstance(r.get("aggregate_score"), (int, float)):
            latest[(d, sec)] = r
    dates = sorted({d for d, _ in latest})[-window:]
    if not dates:
        return {"latest_date": None, "sectors": {}, "dark_today": [], "enterable": []}
    today = dates[-1]
    min_agg = entry_threshold / posterior_ceil  # composite needed for entry to be possible at all

    sectors: dict[str, dict] = {}
    for (d, sec), r in latest.items():
        if d not in dates:
            continue
        st = sectors.setdefault(sec, {"dark_days": 0, "days": 0, "agg": None, "ceil_adj": None})
        st["days"] += 1
        agg = float(r["aggregate_score"])
        if agg * posterior_ceil < entry_threshold:
            st["dark_days"] += 1
        if d == today:
            st["agg"]      = round(agg, 4)
            st["ceil_adj"] = round(agg * posterior_ceil, 4)
    for st in sectors.values():
        st["dark_today"] = st["ceil_adj"] is not None and st["ceil_adj"] < entry_threshold
        st["persistent"] = st["days"] > 0 and st["dark_days"] / st["days"] >= CHECK71_PERSIST_SHARE
    present   = {s for s, st in sectors.items() if st["agg"] is not None}
    dark      = sorted(s for s in present if sectors[s]["dark_today"])
    enterable = sorted(s for s in present if not sectors[s]["dark_today"])
    return {"latest_date": today, "n_dates": len(dates), "min_agg": round(min_agg, 4),
            "sectors": sectors, "dark_today": dark, "enterable": enterable}


def check71():
    """
    List every sector structurally unable to enter allocation *at any posterior*.

    Entry requires adjusted_score = aggregate_score × posterior ≥ ALLOCATION_ENTRY_THRESHOLD
    (regime_bayes.py). Posterior is clamped to POSTERIOR_CLAMP_CEIL, so a sector whose
    aggregate_score × ceiling is below the floor is dark regardless of regime — the
    Transportation finding of 2026-08-08 (0.3025 × 0.95 = 0.287 < 0.37) generalised
    across all eligible sectors. This is the inventory of the upstream structural
    constraint the seven tunables sit downstream of; it is the closest direct read on
    the founding "too few open positions" complaint. LEADERBOARD_SIZE (12) exceeds the
    eligible sector count, so rank is not a second ceiling.

    Severity is about breadth, not any one sector: WARNING when more than
    CHECK71_DARK_SHARE_WARN of eligible sectors are dark today; CRITICAL when at most
    CHECK71_ENTERABLE_CRIT sectors can enter at all. Each dark sector is listed with its
    composite, ceiling-adjusted score, and dark-day count over the last
    CHECK71_WINDOW_DATES trace dates — "persistent" marks ≥ CHECK71_PERSIST_SHARE.

    Reads regime_signal_trace.jsonl (host-only; SKIPPED in CI). Thresholds are imported
    from regime_bayes so the check self-invalidates if the model's floor or clamp moves.
    """
    trace_path = REPO / "data" / "regime_signal_trace.jsonl"
    if not require_data_file(71, "entry-floor ceiling sweep", trace_path):
        return
    try:
        from backend.regime.regime_bayes import ALLOCATION_ENTRY_THRESHOLD, POSTERIOR_CLAMP_CEIL
    except Exception as e:
        flag(71, "entry-floor ceiling sweep", "WARNING", "backend/regime/regime_bayes.py",
             f"could not import ALLOCATION_ENTRY_THRESHOLD / POSTERIOR_CLAMP_CEIL: {e} — "
             f"check did not evaluate")
        return
    records = []
    for line in trace_path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    res = _entry_floor_sweep(records, ALLOCATION_ENTRY_THRESHOLD, POSTERIOR_CLAMP_CEIL)
    if res["latest_date"] is None:
        flag(71, "entry-floor ceiling sweep", "WARNING", "data/regime_signal_trace.jsonl",
             "trace file present but no parseable rows — check did not evaluate")
        return

    dark, enterable = res["dark_today"], res["enterable"]
    n = len(dark) + len(enterable)
    if not dark:
        return
    rows = []
    for s in dark:
        st = res["sectors"][s]
        rows.append(f"{s} agg={st['agg']:.4f} ceil_adj={st['ceil_adj']:.4f} "
                    f"dark {st['dark_days']}/{st['days']}d{' persistent' if st['persistent'] else ''}")
    detail = (
        f"{res['latest_date']}: {len(dark)}/{n} eligible sectors cannot clear the "
        f"{ALLOCATION_ENTRY_THRESHOLD} entry floor at any posterior (composite must be ≥ "
        f"{res['min_agg']} = floor/{POSTERIOR_CLAMP_CEIL}); enterable: "
        f"{', '.join(enterable) or 'none'}. Dark: " + "; ".join(rows) + "."
    )
    if len(enterable) <= CHECK71_ENTERABLE_CRIT:
        sev = "CRITICAL"
    elif len(dark) / n > CHECK71_DARK_SHARE_WARN:
        sev = "WARNING"
    else:
        sev = "INFO"
    flag(71, "entry-floor ceiling sweep", sev, "data/regime_signal_trace.jsonl", detail)


def run() -> None:
    check5()
    check27()
    check36()
    check41()
    check42()
    check43()
    check57()
    check63()
    check65()
    check67()
    check68()
    check69()
    check71()
