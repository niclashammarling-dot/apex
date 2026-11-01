"""Phase 3 part a: every gate config key derives from its rule (Niclas 2026-10-31: "alla namn är konsekvent efter
kodschemat ... konsekvent och lätt att härleda"), and every stored form under the old names stays readable: config
files and posted settings (upgrade_keys), the engines' keywords (accepts_legacy_keys), and the hashes that named a
battery unit or a live shape before the rename (legacy_keys)."""
import hashlib
import json

import pytest

from backend.gate import lock_codes
from backend.gate.lock_codes import KEY_KINDS, LEGACY_KEYS, RULES, config_key, legacy_keys, upgrade_keys


def test_every_rule_key_derives_from_its_rule_with_one_kind():
    for r in RULES:
        for k in r.config_keys:
            stem = lock_codes.rule_key(r.owner, r.name).lower() + "_"
            assert k.startswith(stem) and k[len(stem):] in KEY_KINDS, k
            assert config_key(r.owner, r.name, k[len(stem):]) == k


def test_the_legacy_map_covers_exactly_the_rule_keys():
    assert set(LEGACY_KEYS.values()) == {k for r in RULES for k in r.config_keys}
    assert not set(LEGACY_KEYS) & set(LEGACY_KEYS.values())


def test_a_kind_outside_the_closed_list_is_refused():
    with pytest.raises(ValueError):
        config_key(1, "vix", "threshold")


def test_upgrade_and_legacy_are_inverse_and_recursive():
    old = {"vix_threshold": 30, "runs": [{"lock1_threshold": 0.65, "take_profit_pct": 0.06}]}
    new = upgrade_keys(old)
    assert new == {"l1_eligibility_vix_max": 30,
                   "runs": [{"l3_score_threshold_min": 0.65, "take_profit_pct": 0.06}]}
    assert legacy_keys(new) == old


def test_the_derived_name_wins_when_both_are_stored():
    assert upgrade_keys({"vix_threshold": 25, "l1_eligibility_vix_max": 30}) == {"l1_eligibility_vix_max": 30}


@pytest.mark.parametrize("book", ["live", "demo"])
def test_a_config_file_under_the_old_names_is_read_and_rewritten_under_the_new(book, tmp_path, monkeypatch):
    import importlib
    mod = importlib.import_module(f"backend.{book}_config")
    path = tmp_path / f"{book}_config.json"
    path.write_text(json.dumps({"lock1_threshold": 0.71, "tp_cooloff_hours": 100, "max_positions": 3}))
    monkeypatch.setattr(mod, "_CONFIG_PATH", path)
    get = getattr(mod, f"get_{book}_config")
    cfg = get()
    assert (cfg["l3_score_threshold_min"], cfg["l1_eligibility_exit_cooloff_win_hours"],
            cfg["l2_risk_max_positions_count"]) == (0.71, 100, 3)
    assert not lock_codes.has_legacy_keys(cfg)
    getattr(mod, f"set_{book}_config")({"vix_threshold": 28})      # an old client posting the old name
    stored = json.loads(path.read_text())
    assert stored["l1_eligibility_vix_max"] == 28 and stored["l3_score_threshold_min"] == 0.71
    assert not lock_codes.has_legacy_keys(stored)


def test_the_engines_take_the_old_keyword():
    from backend.backtest import engine, engine_fast, engine_sharpe
    for mod in (engine, engine_fast, engine_sharpe):
        assert mod.run.__wrapped__                                  # accepts_legacy_keys

    @lock_codes.accepts_legacy_keys
    def run(*, l3_score_threshold_min=None, l1_eligibility_vix_max=None):
        return l3_score_threshold_min, l1_eligibility_vix_max

    assert run(lock1_threshold=0.7, vix_threshold=30) == (0.7, 30)
    with pytest.raises(TypeError):
        run(lock1_threshold=0.7, l3_score_threshold_min=0.6)


def test_a_battery_unit_keeps_the_key_it_had_before_the_rename():
    from backend.backtest.battery_2026_10 import Unit, _frozen, unit
    old = {"lock1_threshold": 0.65, "max_positions": 9, "take_profit_pct": 0.06}
    before = Unit("IS", 0, _frozen(old))          # the parent hashed self.kw, which held the old names
    after = unit("IS", upgrade_keys(old))
    assert after.kwargs == upgrade_keys(old)
    assert after.key() == before.key()
    assert unit("IS", old).key() == before.key()  # an old-named base is upgraded on the way in


def test_a_live_shape_keeps_the_hash_it_had_before_the_rename():
    from backend.backtest.live_shape import LIVE_SHAPE_KEYS, live_shape_hash
    new = {k: 1 for k in LIVE_SHAPE_KEYS}
    old = legacy_keys(new)
    assert "max_positions" in old
    assert live_shape_hash(new) == hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()[:8]


def test_every_rule_key_has_its_constant_shared_or_by_book_and_the_defaults_read_it():
    """A constant is its key in capitals; a value that belongs to one book carries the book's name (Niclas 2026-11-01:
    "Var noga med att följa kodschemat: konsekvent, lätt att läsa och alla namn kan härledas"; "Skilj demo och live
    genom boknamn"). Either every book shares the constant or each book has its own, never both. Read from the
    source, so two constants holding the same value cannot pass for each other."""
    import re
    from pathlib import Path

    from backend import config
    from backend.gate.lock_codes import constant_name
    root = Path(__file__).resolve().parents[1]
    src = {book: (root / f"backend/{book}_config.py").read_text() for book in ("demo", "live")}
    pairs = {book: dict(re.findall(r'^\s+"(\w+)":\s+(\w+),$', text, re.M)) for book, text in src.items()}
    for r in RULES:
        for k in r.config_keys:
            kind = k.rsplit("_", 1)[1]
            shared = constant_name(r.owner, r.name, kind)
            own = {b: constant_name(r.owner, r.name, kind, b) for b in ("demo", "live")}
            per_book = all(hasattr(config, n) for n in own.values())
            assert hasattr(config, shared) != per_book, k
            assert not any(hasattr(config, n) for n in own.values()) or per_book, k
            assert pairs["demo"][k] == (own["demo"] if per_book else shared), k
            if k == "l0_portfolio_loss_cap_max":    # live's is a share of equity; its stored key the dollar product
                assert '"l0_portfolio_loss_cap_max":' in src["live"]
                assert "round(LIVE_L0_PORTFOLIO_LOSS_CAP_MAX * LIVE_STARTING_BALANCE, 2)" in src["live"]
            else:
                assert pairs["live"][k] == (own["live"] if per_book else shared), k


def test_the_l0_rules_without_a_config_key_name_their_constants_by_rule_and_book():
    from backend import config
    from backend.gate import l0_portfolio
    from backend.gate.lock_codes import constant_name
    names = {constant_name(0, "halt_m4_drawdown", "max", "live"): 0.06,
             constant_name(0, "loss_cap", "max", "live"): 0.05,
             constant_name(0, "halt_m4_expectancy", "count", "live"): 40}
    assert names == {"LIVE_L0_PORTFOLIO_HALT_M4_DRAWDOWN_MAX": 0.06, "LIVE_L0_PORTFOLIO_LOSS_CAP_MAX": 0.05,
                     "LIVE_L0_PORTFOLIO_HALT_M4_EXPECTANCY_COUNT": 40}
    for n, v in names.items():
        assert getattr(config, n) == v and getattr(l0_portfolio, n) == v
    ref = constant_name(0, "halt_m4_expectancy", "ref", "live")
    assert getattr(config, ref)["n"] == 34 and getattr(l0_portfolio, ref) is getattr(config, ref)


def test_no_constant_keeps_a_pre_phase_3_name():
    from backend import config
    old = [n.upper() for n in LEGACY_KEYS] + ["LOCK3_MAX_DRAWDOWN_PCT", "MACRO_VIX_THRESHOLD",
                                              "MACRO_EVENT_BLACKOUT_DAYS", "MACRO_EARNINGS_BLACKOUT_DAYS",
                                              "M4_DRAWDOWN_PCT", "M4_DAILY_LOSS_CAP_PCT", "M4_EXPECTANCY_N",
                                              "M4_EXPECTANCY_REF"]
    for n in old:
        assert not hasattr(config, n) and not hasattr(config, "LIVE_" + n), n
    from backend import db
    from backend.backtest import live_shape
    assert hasattr(db, "get_l3_score_candidates") and not hasattr(db, "get_lock1_candidates")
    assert not hasattr(live_shape, "DAILY_LOSS_CAP_PCT")
