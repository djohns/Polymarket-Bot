"""Descubrimiento periódico de mercados binarios activos vía Gamma API."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from polybot.config import GAMMA_API_URL

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


@dataclass(frozen=True)
class MarketInfo:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    fee_rate: float
    fee_exponent: float
    fees_enabled: bool
    cluster_id: str


def _parse_market(raw: dict) -> MarketInfo | None:
    try:
        outcomes = json.loads(raw.get("outcomes", "[]"))
        clob_token_ids = json.loads(raw.get("clobTokenIds", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    if len(outcomes) != 2 or len(clob_token_ids) != 2:
        return None  # Fase 1 sólo cubre mercados binarios (YES/NO)

    try:
        yes_idx = outcomes.index("Yes")
        no_idx = outcomes.index("No")
    except ValueError:
        return None

    fee_schedule = raw.get("feeSchedule") or {}
    events = raw.get("events") or []
    cluster_id = str(events[0]["id"]) if events and events[0].get("id") else raw["conditionId"]

    return MarketInfo(
        condition_id=raw["conditionId"],
        question=raw.get("question", ""),
        yes_token_id=clob_token_ids[yes_idx],
        no_token_id=clob_token_ids[no_idx],
        fee_rate=float(fee_schedule.get("rate", 0.0)),
        fee_exponent=float(fee_schedule.get("exponent", 1.0)),
        fees_enabled=bool(raw.get("feesEnabled")) and bool(fee_schedule),
        cluster_id=cluster_id,
    )


def fetch_active_markets(limit: int) -> list[MarketInfo]:
    """Trae hasta `limit` mercados binarios activos, ordenados por volumen 24h desc."""
    markets: list[MarketInfo] = []
    offset = 0
    with httpx.Client(timeout=15) as client:
        while len(markets) < limit:
            resp = client.get(
                f"{GAMMA_API_URL}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": _PAGE_SIZE,
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            for raw in page:
                parsed = _parse_market(raw)
                if parsed is not None:
                    markets.append(parsed)
                if len(markets) >= limit:
                    break
            offset += _PAGE_SIZE

    logger.info("Gamma discovery: %d mercados binarios activos encontrados", len(markets))
    return markets
