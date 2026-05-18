"""
Sector-domain mechanical checks — CHECKs 5, 27, 36, 41, 42.

Covers: sector name string consistency, GICS classification parity,
L4 sub-check pass rates, and new-sector integrity for all expansion sectors.
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
        # Semiconductors (SOXX) — added 2026-05-17
        "ASML": "Semiconductors", "AMAT": "Semiconductors", "LRCX": "Semiconductors",
        "KLAC": "Semiconductors", "MU": "Semiconductors",
        # Defense (ITA) — added 2026-05-17
        "LMT": "Defense", "RTX": "Defense", "NOC": "Defense",
        "GD": "Defense",  "HII": "Defense",
        # Homebuilders (ITB) — added 2026-05-18; GICS: Consumer Discretionary sub-industry
        "DHI": "Homebuilders", "LEN": "Homebuilders", "PHM": "Homebuilders",
        "TOL": "Homebuilders", "NVR": "Homebuilders",
        # Transportation (IYT) — added 2026-05-18; GICS: Industrials sub-industry
        "UNP": "Transportation", "CSX": "Transportation", "FDX": "Transportation",
        "UPS": "Transportation", "JBHT": "Transportation",
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


def run() -> None:
    check5()
    check27()
    check36()
    check41()
    check42()
