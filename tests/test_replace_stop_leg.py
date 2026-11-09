"""replace_stop_leg has no cancel fallback (2026-11-09, ruling R024). Alpaca cancels the rest of a bracket group
when one of its orders is cancelled, so the cancel + standalone stop that stood here since 2026-05-31 took the
take-profit with it. A PATCH failure must raise and touch nothing; the caller retries next cycle."""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from backend.brokers import alpaca as broker

LEG = str(uuid.uuid4())


def _api_error(status):
    return APIError('{"code": 1, "message": "x"}', http_error=MagicMock(response=MagicMock(status_code=status)))


@pytest.mark.parametrize("err", [_api_error(500), _api_error(403), RuntimeError("connection reset")])
def test_patch_failure_raises_and_cancels_nothing(err):
    client = MagicMock()
    client.replace_order_by_id.side_effect = err
    with patch.object(broker, "_client", return_value=client):
        with pytest.raises(type(err)):
            broker.replace_stop_leg(LEG, 103.95, 10, "BBB")
    client.cancel_order_by_id.assert_not_called()
    client.submit_order.assert_not_called()


def test_terminal_leg_is_order_terminal_error():
    client = MagicMock()
    client.replace_order_by_id.side_effect = _api_error(422)
    with patch.object(broker, "_client", return_value=client):
        with pytest.raises(broker.OrderTerminalError):
            broker.replace_stop_leg(LEG, 103.95, 10, "BBB")
    client.cancel_order_by_id.assert_not_called()


def test_patch_success_returns_the_new_leg():
    client = MagicMock()
    client.replace_order_by_id.return_value = MagicMock(id="new-leg")
    with patch.object(broker, "_client", return_value=client):
        assert broker.replace_stop_leg(LEG, 103.95, 10, "BBB") == "new-leg"
