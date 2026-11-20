"""Read-only summary of options_source_probe runs, one method for every run (2026-11-20).

The 11-04 reading recomputed 11-02 with a script that was not kept, so its figures could not be traced
(vault raw/notes/2026-11/2026-11-02-apex-options-source-probe.md § 2026-11-04 rerun). This is the kept one.
Per run: Alpaca's OI dates; OI identical on contracts both sides list; PCR |alpaca/yf - 1| (median, p90,
tickers above 20 %) and PCR pass flips; today's volume match; unusual_calls passes, errors and flips, with
the flipped tickers and passes on fewer than SMALL contracts traded today.

Usage: python scripts/options_probe_compare.py RUN.json [RUN.json ...]
"""
import collections
import json
import statistics
import sys

SMALL = 20   # the unusual_calls pilot's small-count line (R089 a, vault INDEX)


def summarise(d: dict) -> dict:
    ok = [r for r in d["results"] if "yf" in r and "alpaca" in r]
    s = {"run_utc": d["run_utc"], "commit": d["producer"]["commit"][:7], "tickers": len(d["results"]),
         "missing_side": [r.get("ticker") for r in d["results"] if r not in ok],
         "oi_dates": dict(collections.Counter(",".join(r["alpaca"].get("oi_dates") or []) for r in ok))}
    same = shared = 0
    rel, far, pcr_flips = [], [], []
    for r in ok:
        a, y = r["alpaca"].get("oi_by_symbol") or {}, r["yf"].get("oi_by_symbol") or {}
        for sym in set(a) & set(y):
            if a[sym] is not None and y[sym] is not None:
                shared += 1
                same += a[sym] == y[sym]
        pa, py = r["alpaca"].get("pcr") or {}, r["yf"].get("pcr") or {}
        if pa.get("pc_ratio") and py.get("pc_ratio"):
            x = abs(pa["pc_ratio"] / py["pc_ratio"] - 1)
            rel.append(x)
            if x > 0.20:
                far.append(r["ticker"])
        if not pa.get("error") and not py.get("error") and pa.get("pass") != py.get("pass"):
            pcr_flips.append(r["ticker"])
    s["oi_identical"] = (same, shared)
    s["pcr_rel_median"] = statistics.median(rel) if rel else None
    s["pcr_rel_p90"] = statistics.quantiles(rel, n=10)[8] if len(rel) >= 2 else None
    s["pcr_far"], s["pcr_flips"] = far, pcr_flips
    vm = collections.Counter()
    for r in ok:
        vm.update(r.get("volume_match") or {})
    s["volume_match"] = dict(vm)
    u = collections.Counter()
    flips, small = [], {"alpaca": [], "yf": []}
    for r in ok:
        ua, uy = r["alpaca"].get("ucv") or {}, r["yf"].get("ucv") or {}
        u["alpaca_error"] += bool(ua.get("error"))
        u["yf_error"] += bool(uy.get("error"))
        u["alpaca_pass"] += bool(ua.get("pass"))
        u["yf_pass"] += bool(uy.get("pass"))
        if not ua.get("error") and not uy.get("error") and ua.get("pass") != uy.get("pass"):
            flips.append(r["ticker"])
        for side, v in (("alpaca", ua), ("yf", uy)):
            if v.get("pass") and (v.get("call_vol", 0) + v.get("put_vol", 0)) < SMALL:
                small[side].append(r["ticker"])
    s["ucv"], s["ucv_flips"], s["ucv_small_passes"] = dict(u), flips, small
    return s


def main(paths: list[str]) -> None:
    for p in paths:
        s = summarise(json.load(open(p)))
        same, shared = s["oi_identical"]
        print(f"== {p.rsplit('/', 1)[-1]}  run {s['run_utc']}  commit {s['commit']}  tickers {s['tickers']}, "
              f"missing a side {s['missing_side']}")
        print(f"alpaca oi_dates {s['oi_dates']}; OI identical {same}/{shared} = {100 * same / max(shared, 1):.1f} %")
        print(f"PCR |al/yf-1| median {100 * (s['pcr_rel_median'] or 0):.1f} %, p90 {100 * (s['pcr_rel_p90'] or 0):.1f} %; "
              f">20 %: {len(s['pcr_far'])} {s['pcr_far']}; pass flips {len(s['pcr_flips'])} {s['pcr_flips']}")
        vm = s["volume_match"]
        print(f"volume today {vm}; alpaca/yf on both {vm.get('vol_alpaca_both', 0) / max(vm.get('vol_yf_both', 0), 1):.3f}")
        print(f"unusual_calls {s['ucv']}; flips {len(s['ucv_flips'])} {s['ucv_flips']}; "
              f"passes on < {SMALL} contracts {s['ucv_small_passes']}")


if __name__ == "__main__":
    main(sys.argv[1:])
