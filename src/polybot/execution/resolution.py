"""Fase 2, parte 2: consulta de resolución real de un mercado.

Usa CLOB `GET /markets/{condition_id}`, que ya devuelve `closed` y, por cada
token de outcome, un flag `winner` (bool) — la señal más directa y confiable
encontrada. Gamma no sirve para esto: su `/markets` no filtra por
`condition_id`/`conditionId` (el parámetro se ignora en silencio y devuelve el
listado paginado sin filtrar, verificado empíricamente), y sólo permite buscar
un mercado puntual por su `id` numérico interno, que no guardamos en ningún
lado — usar CLOB evita esa vuelta.

Async porque este job corre en el mismo event loop que la ingesta WS de Fase 1:
una llamada HTTP síncrona acá bloquearía el heartbeat/reconexión del WebSocket
mientras dura la consulta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from polybot.config import CLOB_API_URL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolutionResult:
    found: bool
    closed: bool
    resolved: bool
    winning_outcome: str | None  # "YES" | "NO"


_UNRESOLVED = ResolutionResult(found=True, closed=False, resolved=False, winning_outcome=None)
_CLOSED_NO_WINNER = ResolutionResult(found=True, closed=True, resolved=False, winning_outcome=None)
_NOT_FOUND = ResolutionResult(found=False, closed=False, resolved=False, winning_outcome=None)


async def fetch_market_resolution(condition_id: str) -> ResolutionResult | None:
    """None ante error de red/respuesta inesperada -- se reintenta en el próximo ciclo, nunca rompe el job."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CLOB_API_URL}/markets/{condition_id}")
    except httpx.HTTPError:
        logger.warning("Error de red consultando resolución de %s", condition_id)
        return None

    if resp.status_code == 404:
        return _NOT_FOUND

    try:
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("Respuesta inesperada de CLOB consultando resolución de %s", condition_id)
        return None

    return _parse_resolution(data)


def _parse_resolution(data: dict) -> ResolutionResult:
    if not data.get("closed"):
        return _UNRESOLVED

    tokens = data.get("tokens") or []
    winners = [t for t in tokens if t.get("winner")]
    if len(winners) != 1:
        # Cerrado pero sin un ganador único todavía: oráculo/disputa aún en curso.
        return _CLOSED_NO_WINNER

    outcome_name = str(winners[0].get("outcome", "")).strip().upper()
    if outcome_name not in ("YES", "NO"):
        return _CLOSED_NO_WINNER

    return ResolutionResult(found=True, closed=True, resolved=True, winning_outcome=outcome_name)
