"""
Sector-domain mechanical checks — CHECKs 5, 27, 36, 41, 42, 43, 57.

Covers: sector name string consistency, GICS classification parity,
L4 sub-check pass rates, new-sector integrity, sector addition completeness,
and SIC_TO_SECTOR/SECTORS key parity.
"""
import json
import re
import sqlite3
from datetime import date, timedelta

from audit._audit_core import REPO, flag


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
        "NOW": "Technology",  "QCOM": "Technology",
        # Healthcare (XLV)
        "JNJ": "Healthcare", "PFE": "Healthcare", "UNH": "Healthcare",
        "MRNA": "Healthcare", "LLY": "Healthcare",
        "ISRG": "Healthcare", "TMO": "Healthcare",
        # Energy (XLE)
        "EOG": "Energy", "CVX": "Energy", "HAL": "Energy",
        "COP": "Energy", "OXY": "Energy",
        # Industrials (XLI)
        "CAT": "Industrials", "BA": "Industrials", "GE": "Industrials",
        "HON": "Industrials", "DE": "Industrials",
        # Financials (XLF)
        "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
        "BLK": "Financials", "MS": "Financials",
        # ConsumerDisc (XLY)
        "AMZN": "ConsumerDisc", "TSLA": "ConsumerDisc", "NKE": "ConsumerDisc",
        "MCD": "ConsumerDisc", "HD": "ConsumerDisc",
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
        # Defense (ITA) — added 2026-05-17
        "LMT": "Defense", "RTX": "Defense", "NOC": "Defense",
        "GD": "Defense",  "HII": "Defense",
        # Homebuilders (ITB) — added 2026-05-18; expanded 2026-06-14; GICS: Consumer Discretionary sub-industry
        "DHI": "Homebuilders", "LEN": "Homebuilders", "PHM": "Homebuilders",
        "TOL": "Homebuilders", "NVR": "Homebuilders",
        "MHO": "Homebuilders", "TMHC": "Homebuilders", "BLD": "Homebuilders",
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
    if not db.exists():
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
        below ALLOCATION_FLOOR (0.35) — floor failures likely in live gate.
        Action: remove ISRG and TMO via ticker_config.remove_ticker() and diagnose
        signal history before re-adding.

    Single-day readings below threshold are meaningful (threshold gap from baseline is
    wide enough that noise does not reach it).  Retires when Healthcare's post-addition
    composite has been stable above 0.62 for 30 consecutive trading days.
    """
    BASELINE_VALUE = 0.6736
    BASELINE_DATE  = "2026-07-01"
    WARN_FLOOR     = 0.60
    CRIT_FLOOR     = 0.55

    db = REPO / "data/apex.db"
    if not db.exists():
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
        flag(63, "Healthcare composite dilution monitor", "CRITICAL",
             "data/apex.db:sector_snapshots",
             f"Healthcare avg_score={avg_score:.4f} (at {ts}) is below critical floor {CRIT_FLOOR}; "
             f"baseline was {BASELINE_VALUE} on {BASELINE_DATE} (pre-ISRG/TMO). "
             f"At neutral posterior (~0.55), adjusted_score ≈ {avg_score * 0.55:.3f}, "
             f"below ALLOCATION_FLOOR 0.35. Action: remove ISRG and TMO via "
             f"ticker_config.remove_ticker() and diagnose signal history before re-adding.")
    elif avg_score < WARN_FLOOR:
        flag(63, "Healthcare composite dilution monitor", "WARNING",
             "data/apex.db:sector_snapshots",
             f"Healthcare avg_score={avg_score:.4f} (at {ts}) dropped below {WARN_FLOOR}; "
             f"baseline was {BASELINE_VALUE} on {BASELINE_DATE} (pre-ISRG/TMO). "
             f"Investigate ISRG/TMO signal quality in sector_snapshots before next gate cycle.")


def run() -> None:
    check5()
    check27()
    check36()
    check41()
    check42()
    check43()
    check57()
    check63()
