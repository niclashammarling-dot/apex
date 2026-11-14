"""
R029 (Niclas 2026-11-10, built 2026-11-14): weekly_research runs the threshold calibration
only; sweep and optimizer are paused until G1-G6 reach 100 % (R030). One value,
config.WEEKLY_SWEEP_OPTIMIZER_PAUSED, is read by the job and by the weekly report.

- Paused: the job calls recalibrate_thresholds and nothing else.
- Revived (the control): the job calls sweep, optimizer, recalibrate in that order, so the
  first test fails if the gate is removed rather than passing on a job that calls nothing.
- The report's sweep and optimizer sections say PAUSED in both the HTML and the plain-text
  part, with the date of the run the kept file holds, and render none of its numbers.
"""
import json

import backend.config as config_module
import backend.scheduler as scheduler
import backend.weekly_report as wr


def _record_jobs(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(scheduler, "run_weekend_sweep", lambda: calls.append("sweep"))
    monkeypatch.setattr(scheduler, "run_optimizer", lambda: calls.append("optimizer"))
    monkeypatch.setattr(scheduler, "recalibrate_thresholds", lambda: calls.append("recalibrate"))
    return calls


def test_paused_job_calls_only_the_calibration(monkeypatch):
    calls = _record_jobs(monkeypatch)
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", True)
    scheduler.run_weekly_research()
    assert calls == ["recalibrate"]


def test_revived_job_runs_sweep_optimizer_then_calibration(monkeypatch):
    calls = _record_jobs(monkeypatch)
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", False)
    scheduler.run_weekly_research()
    assert calls == ["sweep", "optimizer", "recalibrate"]


def test_the_shipped_value_is_paused():
    assert config_module.WEEKLY_SWEEP_OPTIMIZER_PAUSED is True


def _results_files(monkeypatch, tmp_path):
    sweep, opt = tmp_path / "sweep_results.json", tmp_path / "optimizer_results.json"
    sweep.write_text(json.dumps({"generated_at": "2026-11-09T17:42:00", "top_configs": [{"sharpe": 9.87}]}))
    opt.write_text(json.dumps({"generated_at": "2026-11-09T18:05:00", "best_score": 0.529,
                               "best_params": {"stop_loss_pct": 0.05}}))
    monkeypatch.setattr(wr, "_SWEEP_PATH", sweep)
    monkeypatch.setattr(wr, "_OPT_PATH", opt)


def test_readers_return_paused_with_the_kept_runs_date(monkeypatch, tmp_path):
    _results_files(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", True)
    s_state, s_detail, s_top = wr._sweep_best()
    o_state, o_detail, o_res = wr._optimizer_best()
    assert (s_state, s_top) == (wr._SWEEP_PAUSED, None)
    assert (o_state, o_res) == (wr._SWEEP_PAUSED, None)
    assert "R029" in s_detail and "2026-11-09T17:42" in s_detail and "not current" in s_detail
    assert "R029" in o_detail and "2026-11-09T18:05" in o_detail


def test_readers_without_the_pause_read_the_files(monkeypatch, tmp_path):
    # Control: the same files read as data when the flag is off, so the paused state comes from the flag.
    _results_files(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", False)
    assert wr._optimizer_best()[0] == wr._SWEEP_OK


def test_paused_with_no_file_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(wr, "_SWEEP_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", True)
    state, detail, _ = wr._sweep_best()
    assert state == wr._SWEEP_PAUSED and "no file" in detail


def test_both_renderings_say_paused_and_show_no_numbers(monkeypatch, tmp_path):
    _results_files(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "WEEKLY_SWEEP_OPTIMIZER_PAUSED", True)
    monkeypatch.setattr(config_module, "OPENAI_API_KEY", "")
    monkeypatch.setattr(wr, "_week_start_iso", lambda: "2034-03-06T00:00:00+00:00")
    monkeypatch.setattr(wr, "_fetch_prices", lambda tickers: {})
    _, html, plain = wr.build_report(recal_changes=None)
    assert "Sweep PAUSED" in html and "Optimizer PAUSED" in html
    assert "BEST BACKTEST CONFIGS: PAUSED" in plain and "AUTORESEARCH OPTIMIZER: PAUSED" in plain
    for part in (html, plain):
        assert "0.529" not in part and "9.87" not in part
