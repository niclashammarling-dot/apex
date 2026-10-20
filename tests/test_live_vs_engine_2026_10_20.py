"""live_vs_engine_2026_10_20: the per-set reading and the trade's own bracket."""
import numpy as np
import pandas as pd

import backend.backtest.live_vs_engine_2026_10_20 as lv


def test_cfg_reads_the_trades_own_bracket():
    t = {"entry_price": 100.0, "tp_price": 106.0, "sl_price": 94.0}
    assert lv._cfg(t) == {"tp": 0.06, "sl": 0.06, "pl": [0.04, 0.01], "tdays": 25}


def test_read_counts_divergence_and_ranks_against_the_draws():
    D = pd.DataFrame({"live": [0.02, -0.03, 0.01, 0.04], "eng": [-0.01, -0.02, 0.02, 0.05]})
    R = np.array([[-0.01] * 4, [-0.02] * 4, [0.01] * 4, [-0.01] * 4])      # trades x draws
    r = lv._read(D, R)
    assert (r["live_wr"], r["eng_wr"]) == (0.75, 0.5)
    assert (r["live_win_eng_loss"], r["live_loss_eng_win"]) == (1, 0)
    assert r["rnd_wr"] == 0.25 and r["p_live_wr"] == 0.0                    # every draw wins 1 of 4
    assert r["sign_agreement"] == 0.75
