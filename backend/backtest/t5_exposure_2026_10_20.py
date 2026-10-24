"""
T5 read at matched exposure (2026-10-20; ruling item 8). Ranks R1 against its 100 RND seeds per
sizing arm on deployed capital, return per unit deployed, and return minus SPY at the same mean
exposure (B2). R1's lead over RND comes from the volatility tilt plus extra deployed capital, and
these ranks show how much of it the exposure accounts for.

Usage: python -m backend.backtest.t5_exposure_2026_10_20 T5_IS.json OUT.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MEASURES = {
    "deployed":          lambda x: x["mean_deployed"],
    "return_per_deployed": lambda x: x["total_return"] / x["mean_deployed"],
    "return_minus_b2":   lambda x: x["total_return"] - x["B2_spy_em"]["total_return"],
    "trades":            lambda x: x["trades"],
}


def rank(ref: dict, rnd: list[dict], f) -> dict:
    xs = [f(x) for x in rnd]
    return {"ref": f(ref), "rnd_median": sorted(xs)[len(xs) // 2], "p": sum(x >= f(ref) for x in xs) / len(xs)}


def read(t5: dict) -> dict:
    return {arm: {m: rank(v["ref"], list(v["rnd"].values()), f) for m, f in MEASURES.items()}
            for arm, v in t5["arms"].items()}


def main(argv: list[str]) -> int:
    out = read(json.loads(Path(argv[0]).read_text()))
    Path(argv[1]).write_text(json.dumps(out, indent=1))
    for arm, m in out.items():
        print(arm, {k: (round(v["ref"], 4), round(v["p"], 3)) for k, v in m.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
