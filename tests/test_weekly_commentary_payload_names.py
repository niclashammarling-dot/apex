"""The weekly commentary model reads the names it read before phase 3: the config and the sweep's best row go to it
under the pre-rename keys (a prompt change is not a key rename), while the code builds them under the derived ones."""
import json
from unittest.mock import MagicMock, patch

from backend import weekly_report


def _book():
    return {"closed_total": 2, "wins": 1, "losses": 1, "realized_pnl": 10.0, "regime_exits": 0, "regime_pnl": 0.0,
            "balance": 2010.0, "starting": 2000.0, "open_positions": 1}


def _funnel():
    return {"total": 5, "locks": [], "entered": 1, "unknown": 0, "other": 0}


def test_the_model_reads_the_pre_rename_names():
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=" ok "))]
    cfg = {"l3_score_threshold_min": 0.6, "take_profit_pct": 0.06, "l2_risk_max_positions_count": 10,
           "l1_eligibility_vix_max": 30}
    sweep = [{"row": {"l3_score_threshold_min": 0.62, "l1_eligibility_vix_max": None}}]
    with patch("backend.config.OPENAI_API_KEY", "k"), patch("openai.OpenAI", return_value=client), \
         patch("backend.demo_config.get_demo_config", return_value=cfg):
        out = weekly_report._gpt4o_commentary(_book(), _book(), _funnel(), _funnel(),
                                              {"demo": {}, "live": {}, "demo_open": {}, "live_open": {}},
                                              {"sectors": []}, sweep, None, [])
    assert out == "ok"
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert sent["current_config"] == {"lock1_threshold": 0.6, "take_profit_pct": 0.06, "max_positions": 10,
                                      "vix_threshold": 30}
    assert sent["sweep_best"] == {"lock1_threshold": 0.62, "vix_threshold": None}


def test_a_week_stored_under_the_old_names_renders_under_the_new():
    seen = {}

    def build(snapshot):
        seen.update(snapshot)
        return None, None, "plain"

    snap = {"week": "2026-W43", "written_at": "2026-10-26T08:00:00+00:00",
            "payload": {"sweep_top": [{"row": {"lock1_threshold": 0.62}}]}}
    with patch("backend.db.get_weekly_snapshot", return_value=snap), \
         patch("backend.weekly_report.build_report", side_effect=build):
        assert weekly_report.render_stored("2026-W43") == "plain"
    assert seen["sweep_top"] == [{"row": {"l3_score_threshold_min": 0.62}}]
