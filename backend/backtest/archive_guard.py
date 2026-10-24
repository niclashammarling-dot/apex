"""
Identical-arms guard (ruling Niclas 2026-10-20). Two arms whose settings differ but whose results
are identical are refused, naming the parameters, unless the pair carries a recorded explanation.

Why: the first 1,000-draw live-vs-engine run archived a pre-R9 and an R9 pass that were bit-identical.
The setting never reached the simulator (a call-time keyword overrode the patch, fixed 8f1a8de).
"No effect" and "never wired" look the same in the output; only an explanation with evidence tells
them apart. A legitimate no-effect pair is explained by showing the parameter is wired (it changes
the result elsewhere) and that it had nothing to act on here.
"""
from __future__ import annotations

import hashlib
import json


class IdenticalArms(Exception):
    pass


def fingerprint(result) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()


def identical_pairs(arms: dict[str, tuple[dict, object]]) -> list[dict]:
    """arms: name -> (settings, result). Every pair whose settings differ and whose results are equal,
    with the setting keys that differ."""
    names, out = sorted(arms), []
    fp = {n: fingerprint(arms[n][1]) for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, sb = arms[a][0], arms[b][0]
            keys = sorted(k for k in set(sa) | set(sb) if sa.get(k, "<unset>") != sb.get(k, "<unset>"))
            if keys and fp[a] == fp[b]:
                out.append({"arms": [a, b], "differ_in": keys})
    return out


def refuse_identical(arms: dict[str, tuple[dict, object]], explained: dict[frozenset, str] | None = None) -> list[dict]:
    """Raise IdenticalArms for any unexplained identical pair; return the explained ones for the record."""
    explained = explained or {}
    found = identical_pairs(arms)
    bad = [p for p in found if frozenset(p["arms"]) not in explained]
    if bad:
        raise IdenticalArms("; ".join(f"{p['arms'][0]} and {p['arms'][1]} differ in {p['differ_in']} but their "
                                      "results are identical: the parameter may never reach the engine" for p in bad))
    return [{**p, "explained": explained[frozenset(p["arms"])]} for p in found]
