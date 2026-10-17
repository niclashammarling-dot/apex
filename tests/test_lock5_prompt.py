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
