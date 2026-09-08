from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.real_executor import RealExecutionEngine
from polybot.ingestion.gamma_discovery import MarketInfo
from polybot.ingestion.orderbook import OrderBook
from polybot.persistence.models import Base, RealPosition

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
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple] = []

    def create_and_post_market_order(self, order_args, order_type=None):
        self.calls.append((order_args.token_id, order_args.amount))
        return self._responses.pop(0)


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


def test_leg_imbalance_halts_trading_and_flags_position(request, tmp_path):
    flag = _enable_real_trading(request, tmp_path)
    session_factory = _session_factory()
    client = FakeClient(
        [
            {"transactionsHashes": ["0xyes"], "orderID": "yes-order"},
            {"transactionsHashes": [], "orderID": "no-order"},  # pata NO no llenó
        ]
    )
    engine = RealExecutionEngine(client, session_factory)

    engine.maybe_execute(SPORTS_MARKET, _book("yes", {0.40: 100.0}), _book("no", {0.50: 100.0}))

    assert kill_switch.is_halted(str(flag)) is True
    with session_factory() as session:
        positions = session.execute(select(RealPosition)).scalars().all()
        assert len(positions) == 1
        assert positions[0].status == "pendiente"
