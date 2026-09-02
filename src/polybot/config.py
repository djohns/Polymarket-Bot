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
CLOB_WS_MARKET_URL = CLOB_WS_URL + "market"

POLYGON_CHAIN_ID = 137


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    private_key: str | None = os.getenv("POLYMARKET_PRIVATE_KEY")
    clob_api_key: str | None = os.getenv("CLOB_API_KEY")
    clob_api_secret: str | None = os.getenv("CLOB_API_SECRET")
    clob_api_passphrase: str | None = os.getenv("CLOB_API_PASSPHRASE")
    polygon_rpc_url: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    the_odds_api_key: str | None = os.getenv("THE_ODDS_API_KEY")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/polybot.db")

    # Fase 1: motor de señales
    arb_threshold: float = _float_env("ARB_THRESHOLD", 0.03)
    longshot_price_low: float = _float_env("LONGSHOT_PRICE_LOW", 0.15)
    longshot_price_high: float = _float_env("LONGSHOT_PRICE_HIGH", 0.85)
    longshot_correction: float = _float_env("LONGSHOT_CORRECTION", 0.05)

    # Fase 1: ingesta
    discovery_interval_seconds: int = _int_env("DISCOVERY_INTERVAL_SECONDS", 120)
    discovery_market_limit: int = _int_env("DISCOVERY_MARKET_LIMIT", 100)
    opportunity_log_cooldown_seconds: int = _int_env("OPPORTUNITY_LOG_COOLDOWN_SECONDS", 30)

    # Fase 2: simulador de ejecución + position sizing
    arb_capital_base: float = _float_env("ARB_CAPITAL_BASE", 1000.0)
    arb_max_fraction_per_trade: float = _float_env("ARB_MAX_FRACTION_PER_TRADE", 0.05)
    arb_max_exposure_per_market: float = _float_env("ARB_MAX_EXPOSURE_PER_MARKET", 0.10)
    arb_max_exposure_per_cluster: float = _float_env("ARB_MAX_EXPOSURE_PER_CLUSTER", 0.20)
    kelly_fraction: float = _float_env("KELLY_FRACTION", 0.25)


settings = Settings()
