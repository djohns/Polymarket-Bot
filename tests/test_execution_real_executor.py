from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.real_executor import RealExecutionEngine
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.persistence.models import Base, RealExecutionEvent, RealPosition

SPORTS_MARKET = MarketInfo(
    condition_id="0xsport",
    question="¿Gana el local?",
    yes_token_id="yes",
    no_token_id="no",
    fee_rate=0.0,
    fee_exponent=1.0,
    fees_enabled=False,
    cluster_id="event-1",
    sports_market_type="moneyline",
)

SPORTS_MARKET_WITH_FEE = MarketInfo(
    condition_id="0xsportfee",
    question="¿Gana el local? (con fee)",
    yes_token_id="yesfee",
    no_token_id="nofee",
    fee_rate=0.05,
    fee_exponent=1.0,
    fees_enabled=True,
    cluster_id="event-fee",
    sports_market_type="moneyline",
)

NON_SPORTS_MARKET = MarketInfo(
    condition_id="0xlong",
    question="¿Habrá acuerdo?",
    yes_token_id="yes2",
    no_token_id="no2",
    fee_rate=0.0,
    fee_exponent=1.0,
    fees_enabled=False,
    cluster_id="event-2",
    sports_market_type=None,
)


def _book(asset_id: str, levels: dict[float, float]) -> OrderBook:
    return OrderBook(asset_id=asset_id, asks=dict(levels))


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class FakeClient:
    """`order_responses` se consumen en orden para `create_and_post_market_order`.
    `order_status_responses` (opcional, por orderID) simula `get_order` -- si un
    orderID no tiene entrada, `get_order` lanza (simula que la consulta falla).
    `trades` (opcional) simula `get_trades` -- lista de dicts con al menos
    `taker_order_id` y `status` (y opcionalmente `size`/`price` reales, usados
    por `_confirmed_fill` para el tamaño real de cada pata -- sin ellos cae al
    fallback pre-trade, igual que si `get_trades` no encontrara nada). `asks`
    (opcional, por token_id) simula `get_order_book` para el sizing en vivo de
    la pata NO -- por default un solo nivel amplio a 0.50, suficiente para no
    limitar la profundidad en los tests que no la ejercitan a propósito."""

    def __init__(
        self,
        order_responses: list[dict],
        order_status_responses: dict | None = None,
        trades: list[dict] | None = None,
        asks: dict[str, list[dict]] | None = None,
    ) -> None:
        self._order_responses = list(order_responses)
        self._order_status_responses = order_status_responses or {}
        self._trades = trades if trades is not None else []
        self._asks = asks or {}
        self.calls: list[tuple] = []
        self.get_order_calls: list[str] = []
        self.get_trades_calls: list[dict] = []
        self.get_order_book_calls: list[str] = []

    def create_and_post_market_order(self, order_args, order_type=None):
        self.calls.append((order_args.token_id, order_args.amount))
        return self._order_responses.pop(0)

    def get_order(self, order_id: str):
        self.get_order_calls.append(order_id)
        if order_id not in self._order_status_responses:
            raise RuntimeError(f"orden {order_id} no encontrada (simulado)")
        return self._order_status_responses[order_id]

    def get_trades(self, params, only_first_page=False):
        self.get_trades_calls.append({"asset_id": params.asset_id})
        return self._trades

    def get_order_book(self, token_id: str) -> dict:
        self.get_order_book_calls.append(token_id)
        return {"asks": self._asks.get(token_id, [{"price": "0.50", "size": "1000"}])}


def _enable_real_trading(request, tmp_path):
    """`Settings` es un dataclass frozen -- `monkeypatch.setattr` no puede
    reasignar sus campos. Se muta con `object.__setattr__` (bypassea el
    frozen) y se restaura al final del test vía `request.addfinalizer`."""
    overrides = {
        "real_trading_enabled": True,
        "real_max_exposure_per_market_usd": 5.0,
        "real_max_exposure_per_cluster_usd": 5.0,
        "real_capital_base_usd": 20.0,
        "real_kill_switch_flag_path": str(tmp_path / "HALT"),
    }
    old = {k: getattr(settings, k) for k in overrides}
    for k, v in overrides.items():
        object.__setattr__(settings, k, v)
    request.addfinalizer(lambda: [object.__setattr__(settings, k, v) for k, v in old.items()])
    return tmp_path / "HALT"


def test_non_sports_market_is_never_touched(request, tmp_path):
    _enable_real_trading(request, tmp_path)
    client = FakeClient([])
    engine = RealExecutionEngine(client, _session_factory())

    engine.maybe_execute(NON_SPORTS_MARKET, _book("yes2", {0.40: 100.0}), _book("no2", {0.50: 100.0}))

    assert client.calls == []


def test_real_trading_disabled_is_a_hard_noop(request, tmp_path):
    _enable_real_trading(request, tmp_path)
    object.__setattr__(settings, "real_trading_enabled", False)
    client = FakeClient([])
    engine = RealExecutionEngine(client, _session_factory())

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert client.calls == []


def test_kill_switch_blocks_execution(request, tmp_path):
    flag = _enable_real_trading(request, tmp_path)
    kill_switch.halt("prueba", str(flag))
    client = FakeClient([])
    engine = RealExecutionEngine(client, _session_factory())

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert client.calls == []


def test_successful_fill_places_both_legs_and_persists_open_position(request, tmp_path):
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ]
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert len(client.calls) == 2
    assert client.calls[0][0] == "yes"
    assert client.calls[1][0] == "no"

    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "abierta"
        assert positions[0].cost_usd <= 5.0 + 1e-9


def test_position_is_persisted_immediately_before_fill_is_known(request, tmp_path):
    """El escenario central del incidente del 2026-09-08: si el envío de la
    orden YES explota (excepción de red), antes se habría perdido el registro
    por completo. Ahora la fila ya existe (status="enviada" antes de la
    excepción, "pendiente" después) porque se persiste ANTES de llamar a
    create_and_post_market_order."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()

    class ExplodingClient(FakeClient):
        def create_and_post_market_order(self, order_args, order_type=None):
            raise RuntimeError("timeout de red simulado")

    client = ExplodingClient([])
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "pendiente"
        assert positions[0].market_id == "0xsport"


def test_tradeids_without_hashes_is_treated_as_filled(request, tmp_path):
    """Reproduce exactamente el bug del incidente: la respuesta trae `tradeIDs`
    pero `transactionsHashes` vacío (el hash todavía no se resolvió). Antes,
    esto se leía como "no llenó" y abandonaba sin la pata NO -- ahora debe
    tratarse como llenada y seguir con la pata NO."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": [], "tradeIDs": ["trade-1"], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ]
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert len(client.calls) == 2  # sí llegó a intentar la pata NO
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "abierta"


def test_ambiguous_order_status_defaults_to_filled_not_abandoned(request, tmp_path):
    """Ni transactionsHashes ni tradeIDs, y get_order también falla (inconcluso).
    La decisión conservadora es asumir llenada, no al revés -- la lección del
    incidente fue tratar la ambigüedad como "no llenó" y perder el rastro."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": [], "orderID": "yes-order"},  # get_order para "yes-order" no está configurado -> lanza
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ]
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert "yes-order" in client.get_order_calls
    assert len(client.calls) == 2
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert positions[0].status == "abierta"


def test_confirmed_filled_via_get_trades_matches_real_incident_scenario(request, tmp_path):
    """Reproduce exactamente lo observado contra el servidor real (ver
    CLAUDE.md, sección Fase 3): para una orden de mercado FOK ya ejecutada,
    get_order() devuelve None (inútil), pero get_trades() sí trae el trade
    asociado con status="CONFIRMED". _confirm_via_trades debe reconocerlo
    como llenada sin necesitar el fallback conservador de "asumir llenada"."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": [], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ],
        order_status_responses={},  # get_order("yes-order") no configurado -> lanza, igual que en la realidad
        trades=[{"taker_order_id": "yes-order", "status": "CONFIRMED"}],
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    # get_trades se llama más de una vez ahora (detección de fill + tamaño real
    # confirmado de cada pata vía `_confirmed_fill`), pero sigue consultando "yes".
    assert {c["asset_id"] for c in client.get_trades_calls} == {"yes", "no"}
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert positions[0].status == "abierta"


def test_confirmed_not_filled_via_get_trades_is_cancelled_not_abandoned(request, tmp_path):
    """FAILED_TRADE_STATUS ("FAILED") es la única lectura negativa que expone
    el SDK -- un trade con ese status significa que matcheó pero no liquidó."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [{"transactionsHashes": [], "orderID": "yes-order"}],
        trades=[{"taker_order_id": "yes-order", "status": "FAILED"}],
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert len(client.calls) == 1  # nunca se intentó la pata NO
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert positions[0].status == "cancelada"


def test_confirmed_not_filled_via_get_order_is_cancelled_not_abandoned(request, tmp_path):
    """Cuando get_order sí trae una señal clara de que no matcheó, la orden se
    marca "cancelada" (no se gastó capital) -- pero sigue quedando un registro,
    a diferencia del comportamiento anterior al incidente."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [{"transactionsHashes": [], "orderID": "yes-order"}],
        order_status_responses={"yes-order": {"status": "UNMATCHED", "size_matched": "0"}},
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert len(client.calls) == 1  # nunca se intentó la pata NO
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "cancelada"


def test_leg_imbalance_halts_trading_and_flags_position(request, tmp_path):
    flag = _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": [], "orderID": "no-order"},
        ],
        order_status_responses={"no-order": {"status": "UNMATCHED", "size_matched": "0"}},
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert kill_switch.is_halted(str(flag)) is True
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "pendiente"


def test_no_leg_sized_by_real_yes_shares_not_fixed_budget(request, tmp_path):
    """Reproduce el bug de sizing descubierto en la auditoría de la 4ta
    activación (ver CLAUDE.md, sección Fase 3): antes, la pata NO se mandaba
    con un presupuesto derivado de la estimación PRE-TRADE (`fill.shares`),
    sin importar cuánto había llenado realmente la pata YES. Acá el fill real
    de YES (vía `get_trades`, size=5.0 a precio 0.44) difiere de la estimación
    pre-trade (~5.56 shares a 0.40) -- la pata NO debe dimensionarse por las
    5.0 shares reales de YES (caminando el book de NO en vivo, get_order_book),
    no por la estimación."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": [], "orderID": "yes-order"},
            {"transactionsHashes": [], "orderID": "no-order"},
        ],
        trades=[
            {"taker_order_id": "yes-order", "status": "CONFIRMED", "size": "5.0", "price": "0.44"},
            {"taker_order_id": "no-order", "status": "CONFIRMED", "size": "5.0", "price": "0.50"},
        ],
        asks={"no": [{"price": "0.50", "size": "1000"}]},
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert len(client.calls) == 2
    no_token, no_budget = client.calls[1]
    assert no_token == "no"
    # 5.0 shares reales de YES x 0.50 (book de NO en vivo) = 2.50 -- NO la
    # estimación pre-trade (~5.56 shares x 0.50 = ~2.78).
    assert abs(no_budget - 2.5) < 1e-6

    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert positions[0].yes_shares == 5.0
        assert positions[0].status == "abierta"


def test_residual_leg_size_mismatch_beyond_threshold_halts_like_leg_imbalance(request, tmp_path):
    """Ambas patas "llenan" (transactionsHashes presente en las dos), pero con
    tamaños reales que no calzan (5.0 vs. 4.0, 20% de diferencia > el umbral
    de 2%) -- mismo riesgo que un leg imbalance total (exposición direccional
    real sin cobertura completa), así que debe tratarse igual: kill-switch +
    status="pendiente", no asumir canasta calzada."""
    flag = _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ],
        trades=[
            {"taker_order_id": "yes-order", "status": "CONFIRMED", "size": "5.0", "price": "0.40"},
            {"taker_order_id": "no-order", "status": "CONFIRMED", "size": "4.0", "price": "0.50"},
        ],
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert kill_switch.is_halted(str(flag)) is True
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.status == "pendiente"
        assert pos.yes_shares == 5.0
        assert pos.no_shares == 4.0
        assert abs(pos.leg_imbalance_pct - 0.20) < 1e-9
        assert "desbalance" in pos.notes.lower() or "no quedó calzada" in pos.notes.lower()


def test_real_cost_includes_taker_fee_not_just_price_times_shares(request, tmp_path):
    """`get_trades` devuelve el precio de ejecución SIN fee (verificado en la
    auditoría de la 4ta activación contra `usdcSize` de data-api) -- si
    `_confirmed_fill` no sumara `taker_fee`, `cost_usd` subestimaría el gasto
    real de la wallet en el fee taker efectivo (~2-5% observado), reintroduciendo
    el mismo tipo de error de registro que este módulo corrige, esta vez en el
    costo en vez de en las shares."""
    from polybot.signals.fees import taker_fee

    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    yes_shares, no_shares = 5.0, 5.0
    yes_price, no_price = 0.40, 0.50
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ],
        trades=[
            {"taker_order_id": "yes-order", "status": "CONFIRMED", "size": str(yes_shares), "price": str(yes_price)},
            {"taker_order_id": "no-order", "status": "CONFIRMED", "size": str(no_shares), "price": str(no_price)},
        ],
        asks={"nofee": [{"price": str(no_price), "size": "1000"}]},
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(
        SPORTS_MARKET_WITH_FEE, _book("yesfee", {yes_price: 100.0}), _book("nofee", {no_price: 100.0})
    )

    expected_cost = (
        yes_shares * yes_price
        + taker_fee(yes_shares, yes_price, SPORTS_MARKET_WITH_FEE)
        + no_shares * no_price
        + taker_fee(no_shares, no_price, SPORTS_MARKET_WITH_FEE)
    )
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert positions[0].status == "abierta"
        assert positions[0].cost_usd > yes_shares * yes_price + no_shares * no_price
        assert abs(positions[0].cost_usd - expected_cost) < 1e-9


def test_execution_events_are_persisted_to_db(request, tmp_path):
    """Los eventos críticos quedan en la base, no sólo en journald (ver
    incidente del 2026-09-08: journald rotó en horas y dejó el diagnóstico
    ciego)."""
    _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": ["0xno"], "orderID": "no-order"},
        ]
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    with session_factory() as session:
        events = session.execute(select(RealExecutionEvent)).scalars().all()
        event_types = {e.event_type for e in events}
        assert "order_sent" in event_types
        assert "order_filled" in event_types
        assert "position_opened" in event_types
