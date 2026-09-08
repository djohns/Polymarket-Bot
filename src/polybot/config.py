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

    # Fase 2: tracking de resolución real + Brier score
    resolution_check_interval_seconds: int = _int_env("RESOLUTION_CHECK_INTERVAL_SECONDS", 900)
    resolution_stale_after_days: int = _int_env("RESOLUTION_STALE_AFTER_DAYS", 7)

    # Fase 2: dashboard (reporte HTML estático, regenerado por systemd timer -- no es un server vivo)
    dashboard_output_path: str = os.getenv("DASHBOARD_OUTPUT_PATH", "data/dashboard.html")

    # Fase 3: ejecución real (capital real, sólo arb en mercados de resolución rápida)
    real_trading_enabled: bool = os.getenv("REAL_TRADING_ENABLED", "false").lower() == "true"
    real_capital_base_usd: float = _float_env("REAL_CAPITAL_BASE_USD", 20.0)
    real_max_exposure_per_market_usd: float = _float_env("REAL_MAX_EXPOSURE_PER_MARKET_USD", 5.0)
    real_max_exposure_per_cluster_usd: float = _float_env("REAL_MAX_EXPOSURE_PER_CLUSTER_USD", 5.0)
    real_kill_switch_balance_floor_usd: float = _float_env("REAL_KILL_SWITCH_BALANCE_FLOOR_USD", 15.0)
    real_kill_switch_flag_path: str = os.getenv("REAL_KILL_SWITCH_FLAG_PATH", "data/REAL_TRADING_HALTED")
    real_encrypted_key_path: str = os.getenv("REAL_ENCRYPTED_KEY_PATH", "data/private_key.enc")
    real_key_passphrase_env_var: str = os.getenv("REAL_KEY_PASSPHRASE_ENV_VAR", "POLYMARKET_KEY_PASSPHRASE")
    real_balance_check_interval_seconds: int = _int_env("REAL_BALANCE_CHECK_INTERVAL_SECONDS", 300)
    # Divergencia máxima tolerada (USD) entre el balance real y lo que
    # `real_positions` implica que debería haber, antes de considerarlo una
    # señal de que algo quedó sin registrar (ver incidente del 2026-09-08).
    real_reconciliation_threshold_usd: float = _float_env("REAL_RECONCILIATION_THRESHOLD_USD", 0.50)
    # Proxy wallet ("Safe Wallet") que Polymarket asigna a cuentas conectadas con wallet
    # externa (MetaMask/Rabby) -- ahí vive el pUSD real, no en la EOA firmante. Sin
    # default: es específico de cada cuenta, no tiene un valor razonable genérico.
    real_funder_address: str | None = os.getenv("REAL_FUNDER_ADDRESS")
    # POLY_1271=3 por defecto -- ver CLAUDE.md, sección Fase 3, para por qué esto NO es
    # POLY_GNOSIS_SAFE=2 pese a que la documentación general de Polymarket asocia "Safe
    # Wallet"/MetaMask con ese valor: se verificó empíricamente contra el balance real
    # on-chain de la cuenta y sólo signature_type=3 lo reflejó. Configurable por cuenta.
    real_signature_type: int = _int_env("REAL_SIGNATURE_TYPE", 3)


settings = Settings()
