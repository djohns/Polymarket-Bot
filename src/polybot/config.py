"""Configuración central del bot, cargada desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/"

POLYGON_CHAIN_ID = 137


@dataclass(frozen=True)
class Settings:
    private_key: str | None = os.getenv("POLYMARKET_PRIVATE_KEY")
    clob_api_key: str | None = os.getenv("CLOB_API_KEY")
    clob_api_secret: str | None = os.getenv("CLOB_API_SECRET")
    clob_api_passphrase: str | None = os.getenv("CLOB_API_PASSPHRASE")
    polygon_rpc_url: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    the_odds_api_key: str | None = os.getenv("THE_ODDS_API_KEY")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/polybot.db")


settings = Settings()
