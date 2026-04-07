# CHECK 1 — Result-dict sync hazard: design and resolved state

## What to look for

A sync hazard occurs when:
1. A result dict is constructed with `gate_decision` derived from `outcome`, AND
2. `outcome` is mutated on the result dict AFTER construction, AND
3. The DB write reads `result["gate_decision"]` — which is now stale

## Current resolved state of `_gate_result`

`_gate_result` is a **pure dict constructor** — `outcome` is passed in as a
parameter and both `"outcome"` and `"gate_decision"` are set atomically in the
same return statement. There is no mutation path:

```
def _gate_result(signal, l1, l2, l_leading, l3, outcome, lm=None):
    return {
        ...
        "outcome":       outcome,
        "gate_decision": outcome if outcome != "TRADE_QUEUED" else "TRADE_EXECUTED",
        ...
    }
```

The caller determines `outcome` before calling `_gate_result`, passes it in,
and the returned dict is never mutated before the DB write. This pattern is clean.

## How to reason

Flag only if you observe ALL THREE of these in the current code:
1. A result dict is built (via `_gate_result` or inline) with `gate_decision` derived from `outcome`
2. The dict is subsequently mutated: `result["outcome"] = something_new`
3. A DB insert/update follows that reads `result["gate_decision"]` without re-deriving it

Flagging the structural shape of `_gate_result` alone — without observing a mutation
path after construction — is a false positive. The function's signature guarantees
atomicity by taking `outcome` as a parameter.

## What would be a real finding

If you see code like:
```python
result = _gate_result(signal, l1, l2, l_leading, l3, "TRADE_QUEUED")
result["outcome"] = "TRADE_FAILED"        # mutation
insert_live_gate_result(result)           # reads stale gate_decision
```
That is the hazard. Flag it.
