"""
S14 episode probe (2026-10-21, exploratory; after the pre-registered run, no verdict rests on it).
Why: the S14 L0's level classes (VIX >= 30) are 149 IS closes, but closes inside one VIX episode
share their forward windows, so the independent sample is the episode count, and one episode
(the 2020 crash) could carry the h = 5/10 reading. How: episodes are runs of level closes where a
gap of one non-level session joins the run; the L0 regression is re-run without the largest
episode. What: the episode list and the ex-episode effects, printed.

Usage: python -m backend.backtest.s14_episodes_probe_2026_10_21
"""
from __future__ import annotations

import pandas as pd

from backend.backtest import s13_macro_l0_2026_10_21 as l0
from backend.backtest import s14_vix_l0_2026_10_21 as s


def episodes(level: list[bool], days: list[str]) -> list[tuple[str, str, int]]:
    """(first, last, level closes) per run of level closes; one non-level session inside joins the run."""
    out, i = [], 0
    while i < len(level):
        if not level[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(level) and (level[j + 1] or (j + 2 < len(level) and level[j + 2])):
            j += 1
        out.append((days[i], days[j], sum(level[i:j + 1])))
        i = j + 1
    return out


def main() -> int:
    closes, spy, classes, _, _ = s.window("IS")
    eps = episodes(list(classes.isin(["LEVEL_ONLY", "BOTH"])), list(classes.index))
    print(len(eps), "episodes")
    for e in eps:
        print(" ", e)
    first, last, _ = max(eps, key=lambda e: e[2])
    for h in s.HORIZONS:
        for name, ser in (("pool", closes), ("spy", spy)):
            y = l0.forward_returns(ser, h)
            c = classes.loc[y.index]
            keep = ~((c.index >= first) & (c.index <= last))
            r = s.regress(y[keep], c[keep], h)
            print(f"h{h} {name} ex {first}..{last} neither {r['neither_mean']:+.3f} " + "  ".join(
                f"{k} {r[k]['effect']:+.3f} [{r[k]['ci95'][0]:+.2f},{r[k]['ci95'][1]:+.2f}] n{r['n'][k]}"
                for k in s.CLASSES), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
