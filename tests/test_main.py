from __future__ import annotations

from polybot import main as main_module
from polybot.config import settings
from polybot.execution import kill_switch
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook


class _FakeClobClient:
    """Placeholder que nunca debería recibir un intento de orden real en este
    test -- si `maybe_execute` alguna vez llegara a llamar
    `create_and_post_market_order` pese al kill-switch activo, esto lo
    delata de inmediato en vez de fallar en silencio."""

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []

    def create_and_post_market_order(self, order_args, order_type=None):
        self.calls.append((order_args.token_id, order_args.amount))
        raise AssertionError("no debería enviarse ninguna orden real con el kill-switch activo")

    def create_or_derive_api_key(self):
        return object()

    def set_api_creds(self, creds) -> None:
        pass


def _enable_real_trading_build(request, tmp_path, monkeypatch):
    """`Settings` es un dataclass frozen -- se muta con `object.__setattr__`
    (bypassea el frozen) y se restaura al final del test. Las dependencias
    pesadas de `_build_real_execution_engine` (descifrado de la private key,
    cliente CLOB real, allowance real por red) se reemplazan por fakes: este
    test verifica la lógica de construcción/gating, no la integración real
    con el exchange (ya cubierta en otros tests de `real_executor`)."""
    flag = tmp_path / "HALT"
    overrides = {
        "real_trading_enabled": True,
        "real_funder_address": "0xfunder",
        "real_kill_switch_flag_path": str(flag),
    }
    old = {k: getattr(settings, k) for k in overrides}
    for k, v in overrides.items():
        object.__setattr__(settings, k, v)
    request.addfinalizer(lambda: [object.__setattr__(settings, k, v) for k, v in old.items()])

    monkeypatch.setattr(main_module, "load_private_key", lambda *a, **k: "fake-private-key")
    monkeypatch.setattr(main_module, "ensure_collateral_allowance", lambda client: True)
    monkeypatch.setattr(main_module, "ClobClient", _FakeClobClient)
    return flag


def test_engine_is_built_even_when_already_halted_but_maybe_execute_still_blocks(request, tmp_path, monkeypatch):
    """Fix del 2026-09-14 (ver CLAUDE.md, sección Fase 3, "auto-recuperación
    de sin_confirmar"): `_build_real_execution_engine` ya NO devuelve `None`
    sólo porque el kill-switch esté activo al arrancar -- antes dejaba
    `resolution_loop` sin cliente para la auto-recuperación, exactamente
    cuando más lo necesita. `maybe_execute` sigue siendo el único gate real
    de seguridad, sin cambios: con is_halted()=True, el motor SÍ se
    construye (cliente disponible), pero ninguna orden real se envía --
    la separación es intencional, no una regresión de seguridad."""
    flag = _enable_real_trading_build(request, tmp_path, monkeypatch)
    kill_switch.halt("prueba -- kill-switch ya activo antes de construir el motor", str(flag))
    assert kill_switch.is_halted(str(flag)) is True

    engine = main_module._build_real_execution_engine()

    assert engine is not None  # el motor SÍ se construye pese al halt ya activo

    market = MarketInfo(
        condition_id="0xtest",
        question="¿Gana el local?",
        yes_token_id="yes",
        no_token_id="no",
        fee_rate=0.0,
        fee_exponent=1.0,
        fees_enabled=False,
        cluster_id="c1",
        sports_market_type="moneyline",
    )
    yes_book = OrderBook(asset_id="yes", asks={0.40: 100.0})
    no_book = OrderBook(asset_id="no", asks={0.50: 100.0})

    engine.maybe_execute(market, yes_book, no_book)  # no debe lanzar ni enviar nada

    assert engine._client.calls == []  # ninguna orden real se envió


def test_engine_is_none_when_real_trading_disabled(request, tmp_path, monkeypatch):
    """Contraparte -- sin `REAL_TRADING_ENABLED`, sigue sin construirse nada,
    con o sin kill-switch activo. El interruptor maestro no cambió."""
    _enable_real_trading_build(request, tmp_path, monkeypatch)
    object.__setattr__(settings, "real_trading_enabled", False)

    engine = main_module._build_real_execution_engine()

    assert engine is None
