"""
Live position protection health (2026-10-18, CHECK 92).

Every open live position must be protected by a resting STOP leg at Alpaca that
sits below the last trade. Two failure shapes were silent until this:

  - A split or other corporate action: Alpaca does not adjust resting legs, so
    the stop lands at or above the price and is no longer a protection.
  - Both bracket legs filled in a fast market: the account ends up short.

The ratchet's leg lookup and GTC legs were checked against the code on 10-18
and are not risks as built (raw note 2026-10-18-apex-tp-sl-document-checked-
against-code). One evaluator, two readers: the nightly host audit (CHECK 92)
and the first live exit cycle of each session, so a split effective at the open
is caught in the first cycle rather than the next night.

Price is the position's `current_price` from Alpaca (its last trade), never a
daily close. A broker failure is a finding ("did not evaluate"), never silence.
"""
from __future__ import annotations

from typing import Callable


def evaluate(positions: list[dict], open_trades: list[dict],
             fetch_order: Callable[[str], dict]) -> list[dict]:
    """[{severity, ticker, what}] for every protection fault; [] when all hold.

    positions:   broker.get_positions() rows (ticker, qty, side, current_price)
    open_trades: live_trades rows at outcome 'OPEN' (ticker, alpaca_order_id)
    fetch_order: broker.get_order_by_id
    """
    # The one enumeration of terminal statuses (live_trades_tracker, "exactly one place").
    from backend.live_trades_tracker import _TERMINAL_ORDER_STATUSES as TERMINAL

    out: list[dict] = []
    held: dict[str, dict] = {}
    for p in positions:
        qty = p.get("qty") or 0.0
        if qty < 0 or (p.get("side") or "").lower() == "short":
            out.append({"severity": "CRITICAL", "ticker": p["ticker"],
                        "what": f"short position (qty {qty:g}): both bracket legs filled, or an oversell"})
        held[p["ticker"]] = p

    for t in open_trades:
        ticker = t["ticker"]
        p = held.get(ticker)
        if p is None or (p.get("qty") or 0.0) <= 0:
            continue    # vanished or short: CHECK 66 / reconciliation and the line above own it
        try:
            order = fetch_order(t["alpaca_order_id"])
        except Exception as e:
            out.append({"severity": "WARNING", "ticker": ticker,
                        "what": f"did not evaluate: bracket {t['alpaca_order_id']} not fetched — {e}"})
            continue
        stops = [leg for leg in (order.get("legs") or [])
                 if "stop" in (leg.get("order_type") or "").lower()
                 and leg.get("status") not in TERMINAL]
        if not stops:
            out.append({"severity": "CRITICAL", "ticker": ticker,
                        "what": f"no resting STOP leg on order {t['alpaca_order_id']}: the position is unprotected"})
            continue
        stop, price = stops[0].get("stop_price"), p.get("current_price")
        if stop is None or price is None:
            out.append({"severity": "WARNING", "ticker": ticker,
                        "what": f"did not evaluate: stop {stop} / last trade {price} missing"})
        elif stop >= price:
            out.append({"severity": "CRITICAL", "ticker": ticker,
                        "what": f"stop {stop:.2f} at or above the last trade {price:.2f}: "
                                f"a split or corporate action, or a mis-levelled leg"})
    return out
