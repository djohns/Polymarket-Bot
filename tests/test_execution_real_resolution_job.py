from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.event_log import log_event
from polybot.execution.real_resolution_job import (
    attempt_auto_recovery_of_unconfirmed_positions,
    resolve_open_real_positions,
)
from polybot.execution.resolution import ResolutionResult
from polybot.persistence.models import Base, RealExecutionEvent, RealPosition


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _open_position(session, **overrides) -> RealPosition:
    defaults = {
        "market_id": "0xmarket",
        "cluster_id": "event-1",
        "question": "¿Test?",
        "status": "abierta",
        "yes_shares": 10.0,
        "no_shares": 10.0,
        "yes_price_avg": 0.4,
        "no_price_avg": 0.5,
        "cost_usd": 9.0,
        "fee_paid": 0.2,
        "net_pnl_expected": 0.8,
    }
    defaults.update(overrides)
    pos = RealPosition(**defaults)
    session.add(pos)
    session.commit()
    return pos


def test_resolved_basket_position_closes_with_guaranteed_payout_formula():
    """Ambas patas llenaron con el mismo tamaño (canasta calzada) -- payout
    garantizado de $1/share sin importar el resultado. `cost_usd` ya es el
    gasto real total (fee incluido, ver `real_executor._confirmed_fill`), así
    que la fórmula no resta `fee_paid` aparte."""
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "YES"
    assert pos.resolved_at is not None
    # realized = yes_shares - cost_usd = 10 - 9 = 1.0
    assert round(pos.realized_pnl, 6) == 1.0


def test_resolved_leg_imbalance_position_wins_pays_shares_minus_cost():
    """status="pendiente" (leg imbalance total) -- sólo la pata YES tiene
    capital real (`no_shares=0`). Si YES ganó, el payout es yes_shares - cost_usd."""
    session = _session()
    pos = _open_position(session, status="pendiente", no_shares=0.0, cost_usd=4.0, fee_paid=0.0)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "YES"
    assert round(pos.realized_pnl, 6) == 6.0  # 10 - 4.0


def test_resolved_leg_imbalance_position_loses_all_cost():
    session = _session()
    pos = _open_position(session, status="pendiente", no_shares=0.0, cost_usd=4.0, fee_paid=0.0)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.resolved_outcome == "NO"
    # ganó el lado que nunca tuvo shares reales (no_shares=0) -- payout 0, pérdida total del costo
    assert round(pos.realized_pnl, 6) == -4.0


def test_resolved_residual_mismatch_position_pays_winning_side_real_shares():
    """status="pendiente" por desbalance residual (no por leg imbalance total)
    -- ambas patas tienen shares reales pero distintas (ver
    `real_executor._leg_imbalance_pct`). El payout usa las shares REALES del
    lado ganador, no un promedio ni la estimación pre-trade."""
    session = _session()
    pos = _open_position(
        session,
        status="pendiente",
        yes_shares=5.0,
        no_shares=4.0,
        cost_usd=4.5,
        fee_paid=0.0,
        leg_imbalance_pct=0.20,
    )

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "cerrada"
    # ganó NO -- payout con las shares reales de NO (4.0), no las de YES (5.0)
    assert round(pos.realized_pnl, 6) == -0.5  # 4.0 - 4.5


def test_unresolved_market_leaves_position_open():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=False, resolved=False, winning_outcome=None)

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "abierta"
    assert pos.realized_pnl is None


def test_network_error_does_not_crash_and_leaves_position_untouched():
    session = _session()
    pos = _open_position(session)

    async def fake_fetch(market_id):
        return None

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    session.refresh(pos)
    assert pos.status == "abierta"


def test_no_open_positions_is_a_noop():
    session = _session()

    async def fake_fetch(market_id):
        raise AssertionError("no debería llamarse sin posiciones abiertas")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))


def test_resolution_persists_event_to_db():
    session = _session()
    _open_position(session)

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    asyncio.run(resolve_open_real_positions(session, fetch=fake_fetch))

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    assert any(e.event_type == "position_resolved" for e in events)


# ============================================================================
# Auto-recuperación de "sin_confirmar" (2026-09-14) -- ver CLAUDE.md, sección
# Fase 3, y el docstring de `attempt_auto_recovery_of_unconfirmed_positions`.
# ============================================================================

KNOWN_SIGNATURE = {
    "errorMsg": "",
    "orderID": "0xorder",
    "takingAmount": "",
    "makingAmount": "",
    "status": "delayed",
    "success": True,
}

MARKET_INFO = {
    "tokens": [
        {"outcome": "Yes", "token_id": "yestok"},
        {"outcome": "No", "token_id": "notok"},
    ]
}


@contextmanager
def _override(**kwargs):
    old = {k: getattr(settings, k) for k in kwargs}
    for k, v in kwargs.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


class FakeAutoRecoveryClient:
    """`trades` simula `get_trades` -- lista vacía por default (nunca aparece
    ningún trade, reproduciendo los 3 casos reales: AS Monaco FC, Manchester
    United, CR Flamengo)."""

    def __init__(self, market_info=MARKET_INFO, trades=None):
        self._market_info = market_info
        self._trades = trades if trades is not None else []
        self.get_market_calls: list[str] = []
        self.get_trades_calls: list[str] = []

    def get_market(self, market_id: str) -> dict:
        self.get_market_calls.append(market_id)
        return self._market_info

    def get_trades(self, params, only_first_page: bool = False):
        self.get_trades_calls.append(params.asset_id)
        return self._trades


def _unconfirmed_position(session, *, unconfirmed_leg: str = "YES", opened_at=None, **overrides) -> RealPosition:
    defaults = {
        "market_id": "0xmarket",
        "cluster_id": "event-1",
        "question": "¿Test sin confirmar?",
        "status": "sin_confirmar",
        "yes_shares": 0.0,
        "no_shares": 0.0,
        "yes_price_avg": 0.0,
        "no_price_avg": 0.0,
        "cost_usd": 0.0,
        "fee_paid": 0.0,
        "net_pnl_expected": 0.0,
        "opened_at": opened_at or dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    }
    if unconfirmed_leg == "YES":
        defaults["yes_order_id"] = "0xyes-order"
        defaults["no_order_id"] = None
    else:
        # pata NO sin confirmar -- YES ya tiene capital real confirmado desde antes
        defaults["yes_order_id"] = "0xyes-order"
        defaults["no_order_id"] = "0xno-order"
        defaults["yes_shares"] = 6.0
        defaults["yes_price_avg"] = 0.47
        defaults["cost_usd"] = 3.0
    defaults.update(overrides)
    pos = RealPosition(**defaults)
    session.add(pos)
    session.commit()
    return pos


def _add_fill_not_confirmed_event(session, pos, *, raw_response: dict) -> None:
    log_event(
        session,
        "fill_not_confirmed",
        "critical",
        "test event",
        market_id=pos.market_id,
        real_position_id=pos.id,
        detail={"raw_response": raw_response},
    )


def test_auto_recovers_yes_leg_and_clears_kill_switch_when_no_other_incidents(tmp_path):
    """Caso Manchester United/CR Flamengo: pata YES nunca confirmó, NO nunca
    se llegó a enviar -- la posición completa no gastó capital real. Huella
    conocida + mercado ya resuelto + suficiente tiempo transcurrido -> se
    backfillea con realized_pnl=$0.00 y el kill-switch se levanta solo (era
    el único incidente abierto)."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_auto_recovery_max_per_window=3,
        real_auto_recovery_window_hours=72.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.yes_shares == 0.0
    assert pos.no_shares == 0.0
    assert pos.cost_usd == 0.0
    assert pos.realized_pnl == 0.0  # nunca se gastó capital real, ganó o perdió el mercado no importa

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    event_types = {e.event_type for e in events}
    assert "auto_recovered_unconfirmed_fill" in event_types
    assert "kill_switch_auto_cleared" in event_types
    assert "auto_recovery_capped" not in event_types
    assert not flag.exists()  # el kill-switch se levantó solo


def test_auto_recovers_no_leg_pays_using_already_confirmed_yes_cost(tmp_path):
    """Caso AS Monaco FC: la pata NO nunca confirmó, pero YES ya tenía
    capital real confirmado de antes. El payout usa exactamente la misma
    fórmula que ya usa `_close_real_position` -- no una nueva."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="NO", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="YES")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_auto_recovery_max_per_window=3,
        real_auto_recovery_window_hours=72.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "cerrada"
    assert pos.yes_shares == 6.0  # sin tocar -- ya era el valor real confirmado
    assert pos.no_shares == 0.0
    assert pos.cost_usd == 3.0  # el costo real de YES, sin tocar
    # ganó YES -- payout = yes_shares - cost_usd = 6.0 - 3.0
    assert round(pos.realized_pnl, 6) == 3.0
    assert not flag.exists()


def test_signature_mismatch_leaves_position_untouched_for_manual_review(tmp_path):
    """Gate por huella exacta (pedido explícito del usuario): cualquier
    desviación -- acá un `errorMsg` no vacío -- cae al comportamiento manual
    de siempre, sin intentar generalizar el patrón."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    mismatched = {**KNOWN_SIGNATURE, "errorMsg": "something unexpected"}
    _add_fill_not_confirmed_event(session, pos, raw_response=mismatched)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        raise AssertionError("no debería siquiera consultar la resolución del mercado")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "sin_confirmar"  # intacta
    assert flag.exists()  # kill-switch sigue activo, sin tocar
    events = session.execute(select(RealExecutionEvent)).scalars().all()
    assert not any(e.event_type == "auto_recovered_unconfirmed_fill" for e in events)


def test_not_enough_elapsed_time_skips_this_cycle(tmp_path):
    """No se intenta la verificación larga antes de
    `REAL_UNCONFIRMED_AUTO_RECOVERY_DELAY_SECONDS` -- ni siquiera se consulta
    `get_trades` todavía (evita repetir el error de Kashiwa Reysol, una
    ventana demasiado corta)."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        raise AssertionError("no debería consultar la resolución todavía")

    now = opened_at + dt.timedelta(seconds=10)  # muy poco tiempo
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "sin_confirmar"
    assert client.get_trades_calls == []  # ni siquiera se llegó a chequear
    assert flag.exists()


def test_market_not_resolved_yet_leaves_position_sin_confirmar(tmp_path):
    """Huella conocida + capital real confirmado en cero, pero el mercado
    todavía no resolvió -- se deja "sin_confirmar" para reintentar en el
    próximo ciclo, sin backfillear nada todavía."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=False, resolved=False, winning_outcome=None)

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "sin_confirmar"
    assert pos.realized_pnl is None
    assert flag.exists()


def test_late_trade_found_contradicts_signature_left_for_manual_review(tmp_path):
    """Si aparece un trade tardío (nunca pasó en los 3 casos reales -- 0/3
    tuvieron un trade tardío incluso consultado horas después), esto
    contradice la huella ya caracterizada -- fuera del alcance aprobado, se
    deja para revisión manual con la info nueva en vez de intentar continuar
    el flujo de ejecución original."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(
        trades=[{"taker_order_id": "0xyes-order", "status": "CONFIRMED", "size": "5.0", "price": "0.40"}]
    )

    async def fake_fetch(market_id):
        raise AssertionError("no debería llegar a consultar la resolución")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "sin_confirmar"  # sin tocar -- no forma parte del alcance aprobado
    assert flag.exists()


def test_no_fill_not_confirmed_event_leaves_position_untouched(tmp_path):
    """Sin `raw_response` capturado (ej. una posición sin_confirmar de antes
    del Paso 1) no hay forma de verificar la huella -- se deja manual."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))
    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    # sin _add_fill_not_confirmed_event -- no hay evento con detail

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        raise AssertionError("no debería llegar a consultar la resolución")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "sin_confirmar"
    assert flag.exists()


def test_cap_reached_backfills_position_but_does_not_clear_kill_switch(tmp_path):
    """Salvaguarda del tope (pedido explícito del usuario): con
    `REAL_AUTO_RECOVERY_MAX_PER_WINDOW` ya alcanzado en la ventana móvil, la
    posición SÍ se verifica y backfillea (ya se hizo el trabajo de
    confirmarla con certeza), pero el kill-switch NO se levanta solo -- y el
    evento debe decir explícitamente que es por el tope, no porque este caso
    sea distinto."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))

    now = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    # 3 auto-recuperaciones previas ya dentro de la ventana de 72h
    for i in range(3):
        log_event(
            session,
            "auto_recovered_unconfirmed_fill",
            "critical",
            f"previo {i}",
            detail={},
        )

    opened_at = now - dt.timedelta(seconds=400)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_auto_recovery_max_per_window=3,
        real_auto_recovery_window_hours=72.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "cerrada"  # se backfilleó igual, ya verificado con certeza
    assert pos.realized_pnl == 0.0
    assert flag.exists()  # pero el kill-switch NO se levantó

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    capped = [e for e in events if e.event_type == "auto_recovery_capped"]
    assert len(capped) == 1
    assert "tope" in capped[0].message.lower()
    assert not any(e.event_type == "kill_switch_auto_cleared" for e in events)


def test_does_not_clear_kill_switch_when_other_incident_still_open(tmp_path):
    """Si queda otra posición "pendiente" o "sin_confirmar" abierta (ej. un
    leg imbalance por falla de red, que nunca se auto-recupera), el
    kill-switch NO se levanta aunque ESTA posición sí se verifique y cierre."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))

    opened_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    # otra posición real, "pendiente" por un leg imbalance de red -- nunca se auto-recupera
    other = RealPosition(
        market_id="0xother", cluster_id="event-2", question="otro incidente", status="pendiente",
        yes_shares=5.0, no_shares=0.0, yes_price_avg=0.5, no_price_avg=0.0, cost_usd=5.0,
        fee_paid=0.0, net_pnl_expected=0.0,
    )
    session.add(other)
    session.commit()

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    now = opened_at + dt.timedelta(seconds=400)
    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_auto_recovery_max_per_window=3,
        real_auto_recovery_window_hours=72.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    session.refresh(pos)
    assert pos.status == "cerrada"  # esta sí se verificó y cerró
    assert flag.exists()  # pero el flag sigue activo -- el otro incidente sigue abierto

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    assert not any(e.event_type == "kill_switch_auto_cleared" for e in events)


def test_capped_event_is_not_logged_twice_across_cycles(tmp_path):
    """El evento de "tope alcanzado" se loguea una sola vez por posición, no
    en cada ciclo del job mientras la posición siga sin resolverse el flag."""
    session = _session()
    flag = tmp_path / "HALT"
    kill_switch.halt("fill sin confirmar de prueba", str(flag))

    now = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    for i in range(3):
        log_event(session, "auto_recovered_unconfirmed_fill", "critical", f"previo {i}", detail={})

    opened_at = now - dt.timedelta(seconds=400)
    pos = _unconfirmed_position(session, unconfirmed_leg="YES", opened_at=opened_at)
    _add_fill_not_confirmed_event(session, pos, raw_response=KNOWN_SIGNATURE)

    client = FakeAutoRecoveryClient(trades=[])

    async def fake_fetch(market_id):
        return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome="NO")

    with _override(
        real_unconfirmed_auto_recovery_delay_seconds=300.0,
        real_auto_recovery_max_per_window=3,
        real_auto_recovery_window_hours=72.0,
        real_kill_switch_flag_path=str(flag),
    ):
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))
        # segundo ciclo, mismo estado -- la posición ya está "cerrada", no debería reprocesarse
        asyncio.run(attempt_auto_recovery_of_unconfirmed_positions(session, client, fetch=fake_fetch, now=now))

    events = session.execute(select(RealExecutionEvent)).scalars().all()
    capped = [e for e in events if e.event_type == "auto_recovery_capped"]
    assert len(capped) == 1
