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
    orderID no tiene entrada, `get_order` lanza (simula que la consulta falla)."""

    def __init__(self, order_responses: list[dict], order_status_responses: dict | None = None) -> None:
        self._order_responses = list(order_responses)
        self._order_status_responses = order_status_responses or {}
        self.calls: list[tuple] = []
        self.get_order_calls: list[str] = []

    def create_and_post_market_order(self, order_args, order_type=None):
        self.calls.append((order_args.token_id, order_args.amount))
        return self._order_responses.pop(0)

    def get_order(self, order_id: str):
        self.get_order_calls.append(order_id)
        if order_id not in self._order_status_responses:
            raise RuntimeError(f"orden {order_id} no encontrada (simulado)")
        return self._order_status_responses[order_id]


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
