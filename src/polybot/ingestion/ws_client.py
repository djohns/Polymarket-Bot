"""Cliente WebSocket para el canal `market` del CLOB: order books en vivo, público, sin auth.

Reconexión con backoff exponencial y heartbeat de texto plano "PING" cada 10s
(el servidor responde "PONG"; confirmado empíricamente, no documentado formalmente).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from polybot.config import CLOB_WS_MARKET_URL
from polybot.ingestion.orderbook import OrderBookStore

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 10
MAX_BACKOFF_SECONDS = 60

OnUpdate = Callable[[set[str]], Awaitable[None]]


class MarketWebSocketClient:
    def __init__(self, asset_ids: list[str], store: OrderBookStore, on_update: OnUpdate) -> None:
        self._asset_ids = asset_ids
        self._store = store
        self._on_update = on_update

    async def run(self) -> None:
        """Corre indefinidamente hasta que la tarea que la envuelve se cancele."""
        backoff = 1
        while True:
            try:
                await self._connect_and_listen()
                backoff = 1  # conexión terminó limpio, reset backoff
            except (websockets.ConnectionClosed, OSError) as exc:
                logger.warning("WS market channel desconectado (%s), reintentando en %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _connect_and_listen(self) -> None:
        async with websockets.connect(CLOB_WS_MARKET_URL, ping_interval=None) as ws:
            logger.info("WS conectado, suscribiendo %d assets", len(self._asset_ids))
            await ws.send(json.dumps({"assets_ids": self._asset_ids, "type": "market"}))

            heartbeat_task = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    await self._handle_raw(raw)
            finally:
                heartbeat_task.cancel()

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await ws.send("PING")

    async def _handle_raw(self, raw: str | bytes) -> None:
        if raw == "PONG":
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WS mensaje no-JSON ignorado: %r", raw[:200])
            return

        events = data if isinstance(data, list) else [data]
        affected: set[str] = set()
        for event in events:
            affected |= self._store.handle_message(event)

        if affected:
            await self._on_update(affected)
