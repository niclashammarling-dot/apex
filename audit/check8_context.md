# CHECK 8 — General code health: accepted patterns and reasoning

## Bare except blocks

All except blocks in the gate pipeline log before continuing. There are no
accepted `except: pass` or `except Exception: pass` patterns — every catch
either logs and continues, logs and returns a safe default, or logs and
re-raises. Use this to calibrate severity:

**Accepted pattern — non-risk path (INFO if missing log, clean if log present):**
```python
except Exception as e:
    logger.debug(f"Gate [{ticker}]: rotation forecast unavailable — {e}")
```
Rotation forecast and gate-fail history are enrichment data. Unavailability
degrades context quality but does not affect gate enforcement. `logger.debug`
is sufficient — these are expected on data gaps.

**Accepted pattern — evaluation loop (logs and skips ticker):**
```python
except Exception as e:
    logger.error(f"Gate runner [{ticker}]: evaluation raised — {e}")
    continue
```
Per-ticker failures must not crash the whole run. `logger.error` is correct here.

**Risk-path pattern — fail-safe return (WARNING if bare, accepted if logged):**
```python
except Exception as e:
    logger.warning(f"Live gate: daily loss cap check failed — blocking new entries: {e}")
    return True   # fail-safe: block if we can't confirm safety
```
`_daily_loss_exceeded` returns True on failure — blocks trading rather than
allowing unchecked entries. This is intentional fail-safe design. Acceptable
only with a WARNING-level log so the operator sees it.

## How to reason

- A bare `except` with no logging is always a finding — at minimum INFO.
- If the bare except is in a risk-enforcement path (loss cap, block guard,
  order placement), escalate to WARNING.
- If an except logs but uses the wrong level (e.g. `logger.debug` in a
  risk path), flag as INFO — log level mismatch, not a missing log.
- Do not flag except blocks that match the accepted patterns above unless
  the logging is missing or the severity is wrong for the context.

## TODO/FIXME/HACK comments

Flag any present in the codebase. No accepted instances.

## Inconsistent return types

Flag functions that return `dict` on some paths and `None` on others if
callers do not guard with `if result is not None`. Gate lock functions
(`lock_l1`, `lock_l2`, etc.) must always return a dict — flag any path
that returns `None` unconditionally.
