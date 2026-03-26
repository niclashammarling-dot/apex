from backend.config import LOCK1_THRESHOLD


def evaluate(signal: dict, threshold: float | None = None) -> dict:
    """
    Lock 1 — Quantitative threshold check.
    Pass condition: signal_score >= threshold (defaults to LOCK1_THRESHOLD).
    """
    effective = threshold if threshold is not None else LOCK1_THRESHOLD
    score     = signal.get("signal_score", 0.0)
    passed    = score >= effective

    return {
        "lock": 1,
        "passed": passed,
        "score": score,
        "threshold": effective,
        "reason": "pass" if passed else f"score {score:.3f} < threshold {effective}",
    }
