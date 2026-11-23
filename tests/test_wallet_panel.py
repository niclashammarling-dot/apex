"""Wallet panel decisions (Niclas 2026-11-23, vault INDEX "APEX drift monitor rebuilt as demo/live/real parity").
SHARPE removed: both adapters set it to 0, so it never showed, and computed for real it would be noise (its standard
error over a few months is near 2, 2026-10-16). The demo header reads the configured starting balance instead of a
second copy of it (one value per fact, M12). Demo REALIZED is marked pre-fee: the simulator charges none, and live's
broker fees were $1.87 over 75 trades since 07-07 (/api/live/stats, 2026-11-23), too small for an estimate.
Source-level, like tests/test_live_config_one_value.py: the frontend has no JS test runner."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERM = ROOT / "frontend/src/components/ApexTerminal.jsx"
APP = ROOT / "frontend/src/App.jsx"


def _wallet(src: str) -> str:
    m = re.search(r"function Wallet\(\{ D, mode \}\) \{.*?\n\}\n", src, re.S)
    assert m, "Wallet component not found"
    return m.group(0)


def test_no_sharpe_on_the_wallet_or_the_equity_header():
    assert "SHARPE" not in TERM.read_text()
    assert not re.search(r"\bsharpe\s*:", APP.read_text())


def test_demo_header_reads_the_configured_starting_balance():
    w = _wallet(TERM.read_text())
    head = w.split("t-card-head", 1)[1].split("t-card-body", 1)[0]
    assert "$2,000" not in head and "w.starting" in head


def test_demo_realized_is_marked_pre_fee_and_live_is_not():
    w = _wallet(TERM.read_text())
    assert '"REALIZED · PRE-FEE"' in w and 'mode === "live" ? "REALIZED"' in w
