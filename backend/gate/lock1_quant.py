from backend.config import LOCK1_THRESHOLD


def evaluate(signal: dict) -> dict:
    """
    Lock 1 — Quantitative threshold check.
    Pass condition: signal_score >= 0.65
    """
    score   = signal.get("signal_score", 0.0)
    passed  = score >= LOCK1_THRESHOLD

    return {
        "lock": 1,
        "passed": passed,
        "score": score,
        "threshold": LOCK1_THRESHOLD,
        "reason": "pass" if passed else f"score {score:.3f} < threshold {LOCK1_THRESHOLD}",
    }
