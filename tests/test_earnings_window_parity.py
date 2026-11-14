"""
One value per earnings parameter (R065 a, Niclas 2026-11-14: "ett värde på tre ställen är samma lagrade fakta
som driver"). The blackout had three: the config default 3 (also check_earnings's hardcoded fallback), live 2,
demo 1, set 2026-05-22 from a post-report window the rule never blocks. The fallback now reads the config
constant; this fails when demo, live and the default differ without a written entry in DIFFERENCES.
"""
import json
from pathlib import Path

import backend.config as config_module

REPO = Path(__file__).resolve().parent.parent
KEYS = ("l2_risk_earnings_days", "l2_risk_earnings_near_days", "l2_risk_earnings_near_penalty")
DIFFERENCES: dict[str, str] = {}   # key -> why demo and live differ on purpose (dated, with its ruling)


def _values(key):
    live = json.loads((REPO / "data/live_config.json").read_text())[key]
    demo = json.loads((REPO / "data/demo_config.json").read_text())[key]
    return live, demo, getattr(config_module, key.upper())


def _drift(read, keys, differences):
    return {k: read(k) for k in keys if k not in differences and len(set(read(k))) > 1}


def test_demo_live_and_the_default_agree_unless_marked():
    drift = _drift(_values, KEYS, DIFFERENCES)
    assert not drift, f"(live, demo, default) differ with no entry in DIFFERENCES: {drift}"


def test_the_05_22_shape_is_drift_unless_marked():
    # Control: live 2, demo 1, default 3 is caught; a written entry is the only way past it.
    planted = {"l2_risk_earnings_days": (2, 1, 3)}.get
    assert _drift(planted, ["l2_risk_earnings_days"], {}) == {"l2_risk_earnings_days": (2, 1, 3)}
    assert _drift(planted, ["l2_risk_earnings_days"], {"l2_risk_earnings_days": "demo validates ahead"}) == {}
