"""Prueba de conexión de solo lectura a Gamma API y CLOB API.

No requiere private key ni credenciales de trading. Sólo valida que:
1. Gamma API responde y lista mercados activos.
2. CLOB API responde y devuelve el order book de uno de esos mercados.
"""
from __future__ import annotations

import json

import httpx
from py_clob_client_v2 import ClobClient

from polybot.config import CLOB_API_URL, GAMMA_API_URL, POLYGON_CHAIN_ID


def check_gamma(limit: int = 5) -> list[dict]:
    resp = httpx.get(
        f"{GAMMA_API_URL}/markets",
        params={"active": "true", "closed": "false", "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    markets = resp.json()
    print(f"[Gamma] OK — {len(markets)} mercados activos recibidos")
    for m in markets:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices = json.loads(m.get("outcomePrices", "[]"))
        print(f"  - {m.get('question', '?')[:60]!r} | outcomes={outcomes} prices={prices}")
    return markets


def check_clob(markets: list[dict]) -> None:
    token_id = None
    for m in markets:
        clob_ids = json.loads(m.get("clobTokenIds", "[]"))
        if clob_ids:
            token_id = clob_ids[0]
            break

    if token_id is None:
        print("[CLOB] No se encontró clobTokenIds en los mercados recibidos, se aborta.")
        return

    client = ClobClient(host=CLOB_API_URL, chain_id=POLYGON_CHAIN_ID)
    book = client.get_order_book(token_id)
    bids = book.get("bids") if isinstance(book, dict) else book.bids
    asks = book.get("asks") if isinstance(book, dict) else book.asks
    print(f"[CLOB] OK — order book para token {token_id}")
    print(f"  bids: {len(bids)} niveles | asks: {len(asks)} niveles")


if __name__ == "__main__":
    markets = check_gamma()
    check_clob(markets)
