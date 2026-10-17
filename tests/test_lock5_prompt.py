"""Lock 5's system prompt describes the pipeline as the code runs it (2026-10-17).
Until then it said "allocation weighting is handled downstream": the allocation acts
upstream (Lock 1 admits only sectors with allocation > 0, lock1_eligibility.py) and
downstream only as a sector-cap weight; the post-L5 multiplier is score-relative
(RegimeBayes.size_multipliers, n·s/Σs). Niclas 10-17: L5 makes the overall judgment,
so it needs the right definitions. The binding no-Bayesian-sizing sentence is pinned
verbatim: a prompt edit can move L5's sizing."""
from backend.gate.lock5_claude import SYSTEM_PROMPT

BINDING = ("Do NOT factor regime_bayes_allocation, regime_bayes_posterior, regime_bayes_adjusted_score, "
           "regime_bayes_qualified, regime_bayes_rank, or any Bayesian allocation field into your size")


def test_binding_sizing_instruction_is_unchanged():
    assert BINDING in SYSTEM_PROMPT


def test_prompt_places_the_allocation_upstream_and_the_multiplier_on_the_score():
    assert "handled downstream" not in SYSTEM_PROMPT
    assert "Lock 1 admits only sectors with allocation > 0" in SYSTEM_PROMPT
    assert "signal score relative to the other trades queued in its sector" in SYSTEM_PROMPT


def test_the_claims_hold_in_code():
    """The prompt's two claims, checked against the code they describe."""
    import inspect
    from backend.gate import lock1_eligibility
    from backend.gate.gate_runner import _compute_bayesian_multipliers
    from backend.regime.regime_bayes import RegimeResult
    assert "if allocation == 0:" in inspect.getsource(lock1_eligibility)
    ev = [({"ticker": t, "sector": "Tech", "signal_score": s}, {"outcome": "TRADE_QUEUED"})
          for t, s in (("A", 0.8), ("B", 0.4))]
    for alloc in (0.2, 0.9):                         # the allocation level does not move it
        rr = RegimeResult(date="2026-10-16", leaderboard=[], allocation={"Tech": alloc},
                          leader="Tech", qualifiers=["Tech"])
        m = _compute_bayesian_multipliers(ev, rr)
        assert m["A"] > 1 > m["B"]                   # relative to the sector's queued mean
        assert abs(m["A"] - 2 * 0.8 / 1.2) < 1e-3


def _ctx(dynamic_caps):
    from backend.gate.chain import build_base_context
    wallet = {"balance": 10_000.0, "open_positions": 2, "sector_exposure": {"Technology": 0.2},
              "dynamic_caps": dynamic_caps}
    cfg = {"max_positions": 9, "max_sector_exposure": 0.30, "max_position_size": 0.15, "daily_loss_cap": 500}
    return build_base_context({"ticker": "AAPL", "sector": "Technology"}, wallet, cfg,
                              starting_balance=10_000.0, gate_history_fn=lambda t, limit: [])


def test_lock5_is_given_the_cap_execution_enforces():
    """Until 2026-10-17 L5 judged the flat max_sector_exposure (0.30) while execution
    enforced the dynamic cap (0.15–0.45; 0.45 on the last live row read)."""
    rl = _ctx({"Technology": 0.45})["risk_limits"]
    assert rl["sector_cap"] == 0.45 and rl["sector_cap_source"] == "dynamic"
    assert rl["max_sector_exposure"] == 0.30                       # the base, still shown
    rl = _ctx({})["risk_limits"]                                    # execution's fallback
    assert rl["sector_cap"] == 0.30 and rl["sector_cap_source"] == "flat_fallback"
    assert "against risk_limits.sector_cap" in SYSTEM_PROMPT
    assert "Reject if at or near risk_limits.sector_cap" in SYSTEM_PROMPT


def test_both_runners_hand_the_enforced_caps_to_the_context():
    import inspect
    from backend.gate import gate_runner, gate_runner_live
    assert 'wallet_ctx["dynamic_caps"] = dynamic_caps_live' in inspect.getsource(gate_runner_live.run)
    assert '"dynamic_caps": dynamic_caps' in inspect.getsource(gate_runner)

