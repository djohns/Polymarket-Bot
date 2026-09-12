from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from polybot.execution.event_log import log_event
from polybot.persistence.models import Base, RealExecutionEvent


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def _session():
    factory = _session_factory()
    with factory() as session:
        yield session


def test_exc_info_persists_exception_detail_not_just_the_log():
    """Reproduce el gap de logging encontrado tras Al Ittihad/Boca Juniors: un
    `order_send_failed` con `exc_info=True` debía sobrevivir la rotación de
    journald vía el `detail` persistido, no sólo el log de Python."""
    with _session() as session:
        try:
            raise ConnectionError("boom: timeout hablando con el exchange")
        except ConnectionError:
            log_event(
                session,
                "order_send_failed",
                "critical",
                "Fallo al ENVIAR la orden NO real",
                market_id="0xabc",
                real_position_id=1,
                exc_info=True,
            )

        event = session.execute(select(RealExecutionEvent)).scalar_one()
        assert event.detail is not None
        assert event.detail["exception_type"] == "ConnectionError"
        assert "boom: timeout hablando con el exchange" in event.detail["exception_message"]
        assert "ConnectionError" in event.detail["traceback"]


def test_exc_info_merges_with_explicit_detail_without_dropping_it():
    with _session() as session:
        try:
            raise ValueError("valor inesperado")
        except ValueError:
            log_event(
                session,
                "reconciliation_divergence",
                "critical",
                "algo paso",
                detail={"actual_balance_usd": 22.76, "expected_balance_usd": 17.84},
                exc_info=True,
            )

        event = session.execute(select(RealExecutionEvent)).scalar_one()
        assert event.detail["actual_balance_usd"] == 22.76
        assert event.detail["exception_type"] == "ValueError"


def test_no_exc_info_leaves_detail_as_given():
    with _session() as session:
        log_event(session, "position_opened", "info", "todo bien", detail={"cost_usd": 5.0})
        event = session.execute(select(RealExecutionEvent)).scalar_one()
        assert event.detail == {"cost_usd": 5.0}


def test_no_exc_info_and_no_detail_is_none():
    with _session() as session:
        log_event(session, "position_opened", "info", "todo bien")
        event = session.execute(select(RealExecutionEvent)).scalar_one()
        assert event.detail is None
