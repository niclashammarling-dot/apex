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



# 2026-10-26 (lock labels, prompt ruled by Niclas: "godkänd prompt"; review 2026-11-09): the prompt and
# the lock_results keys use the chain's names, and the gate history it explains is read by stage label.
def test_lock_results_keys_are_the_chain_names():
    from backend.gate.lock5_claude import _build_context_payload
    from backend.gate.types import LockResult
    results = {i: LockResult.pass_(lock_id=i, score=1.0, reason="ok") for i in (1, 2, 3, 4)}
    keys = set(_build_context_payload("AAPL", "Technology", results, {})["lock_results"])
    assert keys == {"l1_eligibility", "l3_score", "l4_sentiment", "l5_positioning"}
    for k in keys:
        assert k in SYSTEM_PROMPT


def test_prompt_has_no_module_numbering_left():
    for old in ("four independent filters", "Lock 2 (quantitative", "from Lock 3", "Repeated L3 fails",
                "Repeated L4 fails", "TRADE_EXECUTED"):
        assert old not in SYSTEM_PROMPT


def test_gate_history_labels_in_the_prompt_are_the_ones_the_reader_writes():
    """The prompt names stages by the label db._ticker_gate_fails writes into `stage`; a label the
    table does not carry would be a sentence about rows Claude never sees."""
    import inspect

    from backend.gate import chain, lock_codes
    from backend.gate.types import L5_UNAVAILABLE_PREFIX
    labels = {s.label for s in lock_codes.STAGES}
    for named in ("L4 · Sentiment", "L5 · Positioning", "L6 · Review", "Executed"):
        assert named in SYSTEM_PROMPT and named in labels
    # FILTERED_L3 also carries the guard and fail-closed rows; the prompt names both by their detail text.
    assert "mechanical pre-L5 guard" in inspect.getsource(chain) and "mechanical drawdown guard" in SYSTEM_PROMPT
    assert L5_UNAVAILABLE_PREFIX.startswith("anthropic_unavailable") and "anthropic_unavailable" in SYSTEM_PROMPT


def test_dependency_note_is_in_the_prompt():
    assert "agreement among them is one piece of evidence, not three" in SYSTEM_PROMPT
