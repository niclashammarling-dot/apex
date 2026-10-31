"""
scripts/fixture_provenance.py: a production-derived fixture row is compared field by field (2026-10-31).
Built on an in-memory table; never opens data/apex.db.
"""
import json
import sqlite3

from scripts.fixture_provenance import check_rows, compare_value


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE live_gate_history (id INTEGER, ticker TEXT, signal_score REAL, lock1_pass INTEGER, "
              "lock3_reasoning TEXT, lock_leading_checks TEXT)")
    c.execute("INSERT INTO live_gate_history VALUES (13309, 'TMO', 0.6049, 0, 'Signal score 0.5747 below threshold "
              "0.5892, a long tail', ?)",
              (json.dumps({"put_call_ratio": {"pass": False, "reason": "P/C 1.85"}, "unusual_calls": {"pass": True}}),))
    return c


def test_a_true_copy_matches_including_cut_text_and_a_json_subset():
    row = {"id": 13309, "ticker": "TMO", "signal_score": 0.6049, "lock1_pass": 0,
           "lock3_reasoning": "Signal score 0.5747 below threshold 0.5892",
           "lock_leading_checks": json.dumps({"put_call_ratio": {"pass": False}})}
    assert check_rows(_db(), [("live_gate_history", row)]) == ["OK live_gate_history 13309"]


def test_the_10_29_shape_an_invented_flag_is_a_mismatch():
    # The founding case: production identity, invented state.
    [line] = check_rows(_db(), [("live_gate_history", {"id": 13309, "ticker": "TMO", "lock1_pass": 1})])
    assert line.startswith("MISMATCH live_gate_history 13309") and "lock1_pass" in line


def test_a_missing_row_column_or_json_key_is_a_mismatch():
    c = _db()
    assert check_rows(c, [("live_gate_history", {"id": 1})])[0].startswith("MISMATCH")
    assert "no such column" in check_rows(c, [("live_gate_history", {"id": 13309, "nope": 1})])[0]
    row = {"id": 13309, "lock_leading_checks": json.dumps({"relative_strength": {"pass": True}})}
    assert "not in production" in check_rows(c, [("live_gate_history", row)])[0]


def test_compare_value_rules():
    assert compare_value(0.6049, 0.6049000000001) is None
    assert compare_value("abc", "abcdef") is None
    assert compare_value("abd", "abcdef") is not None
    assert compare_value("", "abc") is not None          # an empty fixture string is not a cut
    assert compare_value(None, 0) is not None


def test_the_classifier_tests_opt_in():
    from tests import test_history_classify as t
    assert len(t.PRODUCTION_ROWS) == len(t.CASES)
    assert all("id" in r for _, r in t.PRODUCTION_ROWS)
