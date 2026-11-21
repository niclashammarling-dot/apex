"""L6 REVIEW reads the decision object wherever it sits in the reply (R124 a, 2026-11-21).

claude-sonnet-4-6 sometimes reasons in prose before the JSON the prompt asks for. Until 2026-11-21 the reply was
decoded only at character 0, so such a reply failed the lock closed: live 11-02..11-20, 8 of 34 L6 calls; VTR 11-04
and DLR 11-12 were lost without a decision (vault raw/notes and Vault/data/ruling-positions.tsv R124). The reply
shapes below are the logged ones (logs/apex_2026-11-12.log, logs/apex_2026-11-17.log), shortened.
"""
import json

import pytest

from backend.gate import l6_review as l6

FINAL = {"decision": "BUY", "confidence": 0.72, "position_size_pct": 0.08,
         "reasoning": "Within limits; sentiment supportive."}

PROSE_THEN_JSON = ("I need to work through each responsibility systematically.\n\n**1. Portfolio Risk**\n"
                   "- Open positions: 6 of 9 max → 3 slots remaining ✓\n\n**Decision**\n\n" + json.dumps(FINAL))


def _decision(raw):
    return l6._parse_to_result(raw, 0.6, "TEST", l6._SONNET_MODEL)


def test_plain_json_reply():
    r = _decision(json.dumps(FINAL))
    assert r.passed and r.data["decision"] == "BUY" and r.data["confidence"] == 0.72


def test_fenced_json_reply():
    r = _decision("```json\n" + json.dumps(FINAL) + "\n```")
    assert r.passed and r.data["decision"] == "BUY"


def test_prose_before_the_json_is_read():
    """The 11-12 / 11-17 shape. Fails on the parent (JSONDecodeError at character 0)."""
    r = _decision(PROSE_THEN_JSON)
    assert r.passed and r.data["reasoning"] == FINAL["reasoning"]


def test_two_objects_take_the_last():
    """Niclas's addition at the ruling: a draft object in the reasoning, the final answer after it."""
    draft = dict(FINAL, decision="BUY", confidence=0.9, reasoning="draft")
    final = dict(FINAL, decision="HOLD", confidence=0.4, reasoning="final: sector near its cap")
    raw = ("Let me sketch a first answer: " + json.dumps(draft) + "\n\nBut the sector cap binds, so revising.\n\n"
           + json.dumps(final))
    r = _decision(raw)
    assert not r.passed and r.data["decision"] == "HOLD" and r.data["reasoning"].startswith("final")


def test_objects_without_the_four_keys_are_skipped():
    raw = 'Context read: {"open_positions": 6, "max_positions": 9}. Answer: ' + json.dumps(FINAL)
    assert _decision(raw).data["decision"] == "BUY"


def test_an_invalid_decision_value_does_not_qualify():
    bad = dict(FINAL, decision="MAYBE")
    with pytest.raises(json.JSONDecodeError):
        _decision(json.dumps(bad))


@pytest.mark.parametrize("raw", [
    "I need to work through each responsibility systematically.\n\n**1. Portfolio Risk**\n- Open positions: 6/9",
    'Analysis … {"decision": "BUY", "confidence": 0.7, "position_size_pct": 0.08, "reas',   # cut at the token cap
    "",
])
def test_no_decision_object_raises(raw):
    with pytest.raises(json.JSONDecodeError):
        _decision(raw)


def _evaluate_with(monkeypatch, reply):
    monkeypatch.setattr(l6, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(l6, "_call_anthropic",
                        lambda payload, model: (reply, {"input_tokens": 1, "output_tokens": 7}, "end_turn"))
    return l6.evaluate("TEST", "Tech", {}, {}, confidence_min=0.6)


def test_evaluate_reads_a_prose_first_reply(monkeypatch):
    r = _evaluate_with(monkeypatch, PROSE_THEN_JSON)
    assert r.passed and not r.data.get("unavailable")


def test_evaluate_fails_closed_with_the_cause_when_no_object(monkeypatch):
    r = _evaluate_with(monkeypatch, "I need to work through each responsibility systematically.")
    assert not r.passed and r.data["unavailable"] is True
    assert r.reason == f"{l6.L5_UNAVAILABLE_PREFIX}: JSONDecodeError"   # CHECK 81's cause tag is unchanged


def test_evaluate_accepts_the_old_two_tuple(monkeypatch):
    """scripts/identity_replay.py replaced _call_anthropic with a two-tuple fake before 2026-11-21."""
    monkeypatch.setattr(l6, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(l6, "_call_anthropic", lambda payload, model: (json.dumps(FINAL), {"output_tokens": 1}))
    assert l6.evaluate("TEST", "Tech", {}, {}, confidence_min=0.6).passed


def test_the_output_cap_is_above_the_old_500():
    assert l6.L6_REVIEW_MAX_TOKENS >= 2000


def test_call_anthropic_sends_the_cap_and_returns_the_stop_reason(monkeypatch):
    """The request carries L6_REVIEW_MAX_TOKENS and the prompt unchanged; the stop reason comes back."""
    sent = {}

    class _Msgs:
        def create(self, **kw):
            sent.update(kw)
            block = type("B", (), {"text": "  " + json.dumps(FINAL) + "\n"})()
            return type("R", (), {"content": [block], "usage": {"output_tokens": 42}, "stop_reason": "end_turn"})()

    monkeypatch.setattr(l6, "_get_anthropic", lambda: type("C", (), {"messages": _Msgs()})())
    text, usage, stop = l6._call_anthropic({"ticker": "TEST"}, l6._SONNET_MODEL)
    assert sent["max_tokens"] == l6.L6_REVIEW_MAX_TOKENS and sent["system"] == l6.SYSTEM_PROMPT
    assert sent["model"] == "claude-sonnet-4-6"
    assert text == json.dumps(FINAL) and stop == "end_turn" and usage == {"output_tokens": 42}
