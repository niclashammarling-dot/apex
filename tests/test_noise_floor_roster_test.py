"""NOISE_FLOOR roster test: the pre-registered rule and the arm construction."""
from scripts.noise_floor_roster_test import REMOVED, requested, verdict, without


def _s(mean, sigma, threshold):
    return {"mean": mean, "sigma": sigma, "threshold": threshold}


def test_level_material_at_the_with_threshold():
    assert verdict(_s(0.60, 0.05, 0.15), _s(0.45, 0.05, 0.15))["level_material"]        # diff 0.15 = threshold
    assert not verdict(_s(0.60, 0.05, 0.15), _s(0.46, 0.05, 0.15))["level_material"]


def test_spread_material_outside_the_f_interval():
    v = verdict(_s(0.60, 0.05, 0.15), _s(0.60, 0.12, 0.34))                             # ratio 2.4 > 2.25
    assert v["spread_material"] and v["shape3_dead"] and not v["level_material"]
    assert not verdict(_s(0.60, 0.05, 0.15), _s(0.60, 0.11, 0.31))["spread_material"]  # ratio 2.2 inside
    assert verdict(_s(0.60, 0.05, 0.15), _s(0.60, 0.02, 0.06))["spread_material"]       # ratio 0.4 < 0.445


def test_neither_material_keeps_shape3():
    assert not verdict(_s(0.60, 0.05, 0.15), _s(0.58, 0.06, 0.17))["shape3_dead"]


def test_without_drops_exactly_the_removed_names_and_keeps_etfs():
    sectors = {"Homebuilders": {"etf": "XHB", "tickers": ["DHI", "BLD", "TMHC", "LEN"]},
               "Tech": {"etf": "XLK", "tickers": ["AAPL"]}}
    w, wo = requested(sectors), requested(without(sectors))
    assert w - wo == set(REMOVED)
    assert {"SPY", "^VIX", "XHB", "XLK"} <= wo
    assert sectors["Homebuilders"]["tickers"] == ["DHI", "BLD", "TMHC", "LEN"]          # the input is not mutated
