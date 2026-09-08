from __future__ import annotations

from polybot.execution.allowances import ensure_collateral_allowance


class FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.update_calls = 0

    def get_balance_allowance(self, params):
        return self._responses.pop(0)

    def update_balance_allowance(self, params):
        self.update_calls += 1


def test_already_operable_when_all_contracts_have_allowance():
    # Forma real de la respuesta: "allowances" (plural, dict por contrato), no
    # un campo "allowance" singular -- ver CLAUDE.md, sección Fase 3.
    client = FakeClient(
        [{"balance": "22332297", "allowances": {"0xExchangeA": "115792089237316195423570985008687907853269984665640564039457584007913129639935", "0xExchangeB": "500"}}]
    )
    assert ensure_collateral_allowance(client) is True
    assert client.update_calls == 0


def test_any_contract_at_zero_triggers_update_attempt():
    client = FakeClient(
        [
            {"balance": "22332297", "allowances": {"0xExchangeA": "500", "0xExchangeB": "0"}},
            {"balance": "22332297", "allowances": {"0xExchangeA": "500", "0xExchangeB": "500"}},
        ]
    )
    assert ensure_collateral_allowance(client) is True
    assert client.update_calls == 1


def test_stays_false_if_update_does_not_fix_it():
    client = FakeClient(
        [
            {"balance": "22332297", "allowances": {"0xExchangeA": "0", "0xExchangeB": "0"}},
            {"balance": "22332297", "allowances": {"0xExchangeA": "0", "0xExchangeB": "0"}},
        ]
    )
    assert ensure_collateral_allowance(client) is False
    assert client.update_calls == 1


def test_missing_allowances_key_treated_as_zero():
    client = FakeClient([{"balance": "0", "allowances": {}}, {"balance": "0", "allowances": {}}])
    assert ensure_collateral_allowance(client) is False
