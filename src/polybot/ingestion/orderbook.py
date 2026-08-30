"""Reconstrucción de order book local a partir de snapshots y updates incrementales del WS CLOB."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderBook:
    """Order book de un asset_id (un token YES o NO), como mapa precio -> tamaño."""

    asset_id: str
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)

    def apply_snapshot(self, msg: dict) -> None:
        self.bids = {float(lvl["price"]): float(lvl["size"]) for lvl in msg.get("bids", [])}
        self.asks = {float(lvl["price"]): float(lvl["size"]) for lvl in msg.get("asks", [])}

    def apply_price_change(self, change: dict) -> None:
        book = self.bids if change["side"] == "BUY" else self.asks
        price = float(change["price"])
        size = float(change["size"])
        if size == 0:
            book.pop(price, None)
        else:
            book[price] = size

    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def top_levels(self, depth: int = 5) -> dict:
        return {
            "bids": sorted(self.bids.items(), reverse=True)[:depth],
            "asks": sorted(self.asks.items())[:depth],
        }


class OrderBookStore:
    """Mantiene un OrderBook por asset_id, alimentado por mensajes crudos del WS `market`."""

    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}

    def get(self, asset_id: str) -> OrderBook | None:
        return self._books.get(asset_id)

    def handle_message(self, data: dict) -> set[str]:
        """Aplica un mensaje del WS (evento `book` o `price_change`). Devuelve los asset_ids afectados."""
        event_type = data.get("event_type")
        if event_type == "book":
            asset_id = data["asset_id"]
            book = self._books.setdefault(asset_id, OrderBook(asset_id))
            book.apply_snapshot(data)
            return {asset_id}

        if event_type == "price_change":
            affected: set[str] = set()
            for change in data.get("price_changes", []):
                asset_id = change["asset_id"]
                book = self._books.setdefault(asset_id, OrderBook(asset_id))
                book.apply_price_change(change)
                affected.add(asset_id)
            return affected

        return set()
