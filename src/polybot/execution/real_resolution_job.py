"""Resolución real de mercados para `RealPosition` (Fase 3, corrección post-incidente).

Mismo patrón que `resolution_job.py` para `SimulatedPosition` (Fase 2, parte 2)
-- ver ahí para el detalle de por qué se usa `GET /markets/{condition_id}` de
CLOB en vez de Gamma. Se agregó recién ahora porque hasta este punto
`RealPosition` no tenía ningún job de resolución propio: una posición real que
resolvía y se redimía on-chain se quedaba marcada `"abierta"` para siempre,
haciendo que la reconciliación automática (`execution/reconciliation.py`)
comparara contra un estado desactualizado y disparara el kill-switch por una
causa en realidad benigna (ver CLAUDE.md, sección Fase 3, "divergencia de
reconciliación del 2026-09-09").

Payout: `winning_shares - cost_usd`, donde `winning_shares` es la cantidad
REAL confirmada del lado que ganó (`yes_shares` o `no_shares`, ver
`persistence.models.RealPosition` y `execution.real_executor._confirmed_fill`)
y `cost_usd` es el gasto real total de ambas patas. Una única fórmula sirve
tanto para una canasta bien calzada (payout = $1 × shares del lado ganador,
igual que antes) como para una posición con leg imbalance total (`no_shares=0`,
el payout es simplemente 0 si ganó el lado sin capital) o con desbalance
residual (`status="pendiente"` con ambas patas > 0 pero desiguales) -- no hace
falta distinguir el caso por `status`, sólo usar las shares reales de cada
lado (ver CLAUDE.md, sección Fase 3, "Bug de sizing descubierto en la
auditoría de la 4ta activación", para el porqué de este cambio respecto a la
fórmula anterior, que asumía una única `shares` compartida por ambas patas).

**Auto-recuperación de "sin_confirmar" (2026-09-14)** -- ver
`attempt_auto_recovery_of_unconfirmed_positions` más abajo, y CLAUDE.md,
sección Fase 3, para la propuesta completa que motivó esto: tras 3 casos
(AS Monaco FC, Manchester United, CR Flamengo) con la MISMA huella exacta de
respuesta cruda del exchange (`status="delayed"`, `success=true`, sin
`errorMsg`, 0 trades reales confirmados incluso horas después), el proceso
de verificación que hasta ahora hacía un humano a mano (esperar, volver a
consultar `get_trades`, chequear si el mercado resolvió, backfillear con el
resultado real) se automatiza -- pero SÓLO para esa huella exacta ya
caracterizada 3/3 veces, nunca para "sin_confirmar" en general. **Cualquier
desviación de esa huella (un `errorMsg` no vacío, un `status` distinto,
cualquier campo inesperado) cae al comportamiento manual de siempre, sin
ningún intento de generalizar el patrón** -- ver `_matches_known_signature`.
El auto-recovery nunca reemplaza el rigor de verificación ya establecido
(ausencia confirmada en `get_trades`, no un atajo basado en `status`) --
sólo automatiza esperar más tiempo del que `_confirmed_fill` espera en el
momento (6s), que fue justamente la causa del incidente de Kashiwa Reysol.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polybot.config import settings
from polybot.execution import kill_switch
from polybot.execution.event_log import log_event
from polybot.execution.real_executor import _fetch_trade_totals
from polybot.execution.resolution import fetch_market_resolution
from polybot.persistence.models import RealExecutionEvent, RealPosition

logger = logging.getLogger(__name__)


async def resolve_open_real_positions(session: Session, *, fetch=fetch_market_resolution) -> None:
    # "sin_confirmar" queda deliberadamente afuera de este job: a diferencia de
    # "pendiente" (shares reales confirmadas, aunque desbalanceadas), acá al
    # menos una pata nunca se confirmó -- resolverla con esos valores sería tan
    # malo como el bug que originó el estado (ver real_executor.py, incidente
    # del 2026-09-11). Su propio camino de auto-recuperación, más estricto
    # (huella exacta + verificación extendida), vive en
    # `attempt_auto_recovery_of_unconfirmed_positions` más abajo.
    open_positions = (
        session.execute(select(RealPosition).where(RealPosition.status.in_(("abierta", "pendiente"))))
        .scalars()
        .all()
    )
    if not open_positions:
        return

    by_market: dict[str, list[RealPosition]] = {}
    for pos in open_positions:
        by_market.setdefault(pos.market_id, []).append(pos)

    now = dt.datetime.now(dt.UTC)
    for market_id, positions in by_market.items():
        result = await fetch(market_id)
        if result is None:
            continue  # error de red -- se reintenta en el próximo ciclo

        if not result.found:
            logger.warning(
                "Mercado real %s no encontrado en CLOB al chequear resolución (¿purgado/archivado?)", market_id
            )
            continue

        if not result.resolved:
            continue  # aún en curso (o cerrado sin ganador único todavía) -- nada que hacer

        for pos in positions:
            _close_real_position(session, pos, result.winning_outcome, now)

    session.commit()


def _close_real_position(session: Session, pos: RealPosition, winning_outcome: str, now: dt.datetime) -> None:
    was_uncertain = pos.status in ("pendiente", "sin_confirmar")
    winning_shares = pos.yes_shares if winning_outcome == "YES" else pos.no_shares
    realized = winning_shares - pos.cost_usd

    pos.status = "cerrada"
    pos.resolved_outcome = winning_outcome
    pos.resolved_at = now
    pos.realized_pnl = realized

    log_event(
        session,
        "position_resolved",
        "info",
        f"POSICIÓN REAL RESUELTA {pos.question[:60]} | outcome={winning_outcome} "
        f"realized_pnl={realized:.4f}{' (leg imbalance/desbalance/sin_confirmar)' if was_uncertain else ''}",
        market_id=pos.market_id,
        real_position_id=pos.id,
    )


def _matches_known_signature(raw_response: dict) -> bool:
    """Sólo la huella EXACTA ya vista 3/3 veces (AS Monaco FC, Manchester
    United, CR Flamengo) habilita la auto-recuperación. Cualquier otra forma
    -- incluso una que "parezca" similar -- cae al comportamiento manual de
    siempre, sin excepción y sin intentar generalizar el patrón: no hay
    evidencia de que otras formas de respuesta se comporten igual (ver
    CLAUDE.md, "Paso 2 -- descartado", sobre por qué no confiar en el campo
    `status` para nada más que esta combinación ya verificada repetidas veces)."""
    return (
        raw_response.get("status") == "delayed"
        and raw_response.get("success") is True
        and not raw_response.get("errorMsg")
    )


def _no_other_open_incidents(session: Session) -> bool:
    """El flag global del kill-switch sólo se levanta solo cuando NINGÚN otro
    incidente sigue abierto -- ni "pendiente" (leg imbalance/desbalance, que
    nunca se auto-recupera, ver CLAUDE.md) ni "sin_confirmar" (que sólo se
    auto-recupera si matchea la huella conocida Y ya resolvió). En la
    práctica, mientras el kill-switch esté activo `maybe_execute` bloquea
    cualquier orden real nueva, así que normalmente hay a lo sumo un
    incidente abierto por vez -- pero el chequeo es explícito igual, no
    asumido."""
    count = session.execute(
        select(func.count()).select_from(RealPosition).where(RealPosition.status.in_(("pendiente", "sin_confirmar")))
    ).scalar_one()
    return count == 0


def _recent_auto_recovery_count(session: Session, *, now: dt.datetime) -> int:
    window_start = now - dt.timedelta(hours=settings.real_auto_recovery_window_hours)
    return session.execute(
        select(func.count())
        .select_from(RealExecutionEvent)
        .where(
            RealExecutionEvent.event_type == "auto_recovered_unconfirmed_fill",
            RealExecutionEvent.occurred_at >= window_start,
        )
    ).scalar_one()


async def attempt_auto_recovery_of_unconfirmed_positions(
    session: Session,
    client,
    *,
    fetch=fetch_market_resolution,
    now: dt.datetime | None = None,
) -> None:
    """Para posiciones `"sin_confirmar"` cuya respuesta cruda capturada matchea
    la huella exacta ya caracterizada (ver `_matches_known_signature`):
    espera `REAL_UNCONFIRMED_AUTO_RECOVERY_DELAY_SECONDS` desde el intento
    original (mucho más que los ~6s de `_confirmed_fill` -- esa ventana corta
    fue la causa del incidente de Kashiwa Reysol), vuelve a consultar
    `get_trades` una vez más, y si SIGUE sin aparecer ningún trade, confirma
    con certeza que esa pata nunca tuvo capital real. Si además el mercado ya
    resolvió, backfillea la posición con la MISMA fórmula de payout que ya
    usa `_close_real_position` -- no una nueva.

    El flag global del kill-switch se levanta solo únicamente si, tras cerrar
    esta posición, no queda ningún otro incidente abierto (`_no_other_open_incidents`)
    Y no se superó `REAL_AUTO_RECOVERY_MAX_PER_WINDOW` auto-recuperaciones en
    `REAL_AUTO_RECOVERY_WINDOW_HOURS` -- si se superó, la posición se
    backfillea igual (ya se verificó con certeza), pero el kill-switch queda
    activo a propósito, con un evento que dice explícitamente que es por el
    tope alcanzado, no porque este caso puntual sea distinto de los
    anteriores (pedido explícito del usuario para no generar confusión)."""
    now = now or dt.datetime.now(dt.UTC)
    unconfirmed = (
        session.execute(select(RealPosition).where(RealPosition.status == "sin_confirmar")).scalars().all()
    )
    if not unconfirmed:
        return

    for pos in unconfirmed:
        opened_at = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=dt.UTC)
        elapsed = (now - opened_at).total_seconds()
        if elapsed < settings.real_unconfirmed_auto_recovery_delay_seconds:
            continue  # todavía no pasó suficiente tiempo desde el intento original

        already_capped = (
            session.execute(
                select(func.count())
                .select_from(RealExecutionEvent)
                .where(
                    RealExecutionEvent.real_position_id == pos.id,
                    RealExecutionEvent.event_type == "auto_recovery_capped",
                )
            ).scalar_one()
            > 0
        )
        if already_capped:
            continue  # ya se decidió (tope alcanzado) para esta posición -- no repetir el evento cada ciclo

        fill_event = (
            session.execute(
                select(RealExecutionEvent)
                .where(
                    RealExecutionEvent.real_position_id == pos.id,
                    RealExecutionEvent.event_type == "fill_not_confirmed",
                )
                .order_by(RealExecutionEvent.id.desc())
            )
            .scalars()
            .first()
        )
        if fill_event is None or not fill_event.detail:
            continue  # sin raw_response capturado -- no se puede verificar la huella, se deja manual
        raw_response = fill_event.detail.get("raw_response") or {}
        if not _matches_known_signature(raw_response):
            continue  # huella distinta a la ya caracterizada -- manual, sin generalizar

        unconfirmed_leg = "NO" if pos.no_order_id else "YES"
        order_id = pos.no_order_id if unconfirmed_leg == "NO" else pos.yes_order_id

        try:
            market_info = await asyncio.to_thread(client.get_market, pos.market_id)
        except Exception:
            logger.warning("Fallo consultando get_market para auto-recuperación de %s", pos.market_id, exc_info=True)
            continue
        token_id = next(
            (t.get("token_id") for t in market_info.get("tokens", []) if str(t.get("outcome", "")).upper() == unconfirmed_leg),
            None,
        )
        if token_id is None:
            continue

        totals = await asyncio.to_thread(_fetch_trade_totals, client, token_id, order_id)
        if totals is not None:
            # Contradice la huella ya vista 3/3 veces (nunca apareció un trade
            # tan tarde) -- fuera del alcance aprobado (sólo "confirmado cero"),
            # se deja para revisión manual con esta info nueva en vez de
            # intentar continuar el flujo de ejecución original.
            logger.warning(
                "Auto-recuperación de la posición %s encontró un trade tardío para la pata %s -- "
                "esto contradice la huella ya caracterizada, se deja para revisión manual",
                pos.id,
                unconfirmed_leg,
            )
            continue

        result = await fetch(pos.market_id)
        if result is None or not result.found or not result.resolved:
            continue  # mercado sin resolver todavía (o error de red) -- reintentar en el próximo ciclo

        if unconfirmed_leg == "YES":
            # YES nunca confirmó -- NO nunca se llegó a enviar (ver
            # `real_executor._execute_fill`) -- la posición completa no gastó
            # capital real.
            pos.yes_shares = 0.0
            pos.no_shares = 0.0
            pos.cost_usd = 0.0
        # si es NO, yes_shares/cost_usd ya reflejan el costo real de YES
        # (seteado antes de intentar NO); no_shares ya es 0.0 por default.

        recent_count = _recent_auto_recovery_count(session, now=now)
        _close_real_position(session, pos, result.winning_outcome, now)

        log_event(
            session,
            "auto_recovered_unconfirmed_fill",
            "critical",
            f"AUTO-RECUPERADO: posición {pos.id} ({pos.question[:60]}) -- pata {unconfirmed_leg} "
            f"confirmada sin capital real tras espera extendida ({elapsed:.0f}s), mercado ya resuelto "
            f"(huella conocida: status=delayed, success=true, sin errorMsg). realized_pnl={pos.realized_pnl:.4f}",
            market_id=pos.market_id,
            real_position_id=pos.id,
            detail={
                "unconfirmed_leg": unconfirmed_leg,
                "raw_response": raw_response,
                "elapsed_seconds": elapsed,
                "realized_pnl": pos.realized_pnl,
            },
        )

        if recent_count >= settings.real_auto_recovery_max_per_window:
            log_event(
                session,
                "auto_recovery_capped",
                "critical",
                f"Posición {pos.id} verificada y backfilleada con su resultado real, pero el "
                f"kill-switch NO se levanta automáticamente: se alcanzaron {recent_count} "
                f"auto-recuperaciones en las últimas {settings.real_auto_recovery_window_hours:.0f}h "
                f"(tope: {settings.real_auto_recovery_max_per_window}). Requiere revisión manual -- "
                "esto es por el tope alcanzado, NO porque este caso puntual sea distinto de los anteriores.",
                market_id=pos.market_id,
                real_position_id=pos.id,
                detail={
                    "recent_auto_recoveries": recent_count,
                    "max_per_window": settings.real_auto_recovery_max_per_window,
                    "window_hours": settings.real_auto_recovery_window_hours,
                },
            )
            continue  # no tocar el kill-switch

        if kill_switch.is_halted() and _no_other_open_incidents(session):
            kill_switch.clear()
            log_event(
                session,
                "kill_switch_auto_cleared",
                "critical",
                f"Kill-switch levantado automáticamente -- el último incidente abierto "
                f"(posición {pos.id}) quedó auto-recuperado y verificado, sin ningún otro "
                "incidente pendiente.",
                market_id=pos.market_id,
                real_position_id=pos.id,
            )

    session.commit()
