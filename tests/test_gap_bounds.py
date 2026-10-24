"""gap_bounds: every result lists the PRODUCTION_GAP rows it does not model (2026-10-17)."""
from backend.backtest.gap_bounds import gap_rows, open_rows


def test_reads_the_real_table_with_known_rows():
    rows = {r["row"]: r for r in gap_rows()}
    assert {"1", "7", "15", "18", "27"} <= set(rows)                 # known-present controls
    open_on = {r["row"] for r in open_rows(regime_layer=True)}
    assert {"15", "17", "18"} <= open_on                              # not reconstructible: always open
    assert "1" not in open_on and "2" not in open_on                  # modelled (2 under the flag)


def test_flag_off_adds_the_regime_rows():
    on = {r["row"] for r in open_rows(regime_layer=True)}
    off = {r["row"] for r in open_rows(regime_layer=False)}
    assert {"2", "5", "20", "21"} <= off - on


def test_synthetic_table(tmp_path):
    p = tmp_path / "g.md"
    p.write_text("| # | C | W | S | N |\n|---|---|---|---|---|\n| 1 | a | x | **yes** | |\n"
                 "| 2 | b | x | **no** in fast | |\n| 3 | c | x | **partial** — z | |\n")
    assert [r["row"] for r in open_rows(True, p)] == ["2", "3"]


def test_off_both_rows_stay_in_the_description(tmp_path):
    """A row the engines match only because live's rule is dead is still not modelled (2026-10-18)."""
    p = tmp_path / "g.md"
    p.write_text("| # | C | W | S | N |\n|---|---|---|---|---|\n| 1 | a | x | **yes** | |\n"
                 "| 2 | b | x | **no** | |\n| 8 | e | x | **off-both** (live defect) | |\n")
    assert [(r["row"], r["kind"]) for r in open_rows(True, p)] == [("2", "open"), ("8", "off-both")]


def test_real_table_names_the_earnings_rows_as_off_both():
    from backend.backtest.gap_bounds import describe
    off = {r["row"] for r in open_rows(True) if r["kind"] == "off-both"}
    assert {"8", "9"} <= off
    assert "off in both engines and in live (live defect): 8, 9" in describe(True)
