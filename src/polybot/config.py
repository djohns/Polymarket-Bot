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
    # Bajado de 900s a 180s el 2026-09-12: es una consulta liviana de sólo
    # lectura, sin costo real de correrla más seguido, y el intervalo tiene
    # que ser coherente con REAL_RECONCILIATION_GRACE_PERIOD_SECONDS (ver
    # abajo) -- ambos gobiernan el mismo lag (cuánto tarda una posición real
    # resuelta en quedar marcada "cerrada"), así que no pueden fijarse por
    # separado sin que uno vuelva ineficaz al otro.
    resolution_check_interval_seconds: int = _int_env("RESOLUTION_CHECK_INTERVAL_SECONDS", 180)
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
    # Ventana de gracia asimétrica (2026-09-12, ver reconciliation.py y CLAUDE.md):
    # sólo se aplica a divergencia POSITIVA con una posición "pendiente" en
    # curso -- candidata a estar resolviendo justo en este momento (mismo
    # patrón benigno de Santa Fe/Al Ittihad). Divergencia negativa nunca la usa.
    # 240s (4min) para ser coherente con RESOLUTION_CHECK_INTERVAL_SECONDS
    # (180s/3min, ver arriba): con el job corriendo cada 3 min, un margen de
    # 4 min cubre el peor caso razonable (una resolución que ocurre justo
    # después de que el job arrancó su ciclo) con buffer.
    real_reconciliation_grace_period_seconds: float = _float_env("REAL_RECONCILIATION_GRACE_PERIOD_SECONDS", 240.0)
    # Punto de referencia para la reconciliación: el balance real CONFIRMADO
    # (vía get_balance_allowance) al momento `REAL_BALANCE_CHECKPOINT_AT`,
    # asumiendo que en ese momento `real_positions` ya reflejaba todo lo
    # sucedido hasta ahí (por eso importa re-fijar el par tras cada backfill/
    # incidente resuelto). Sin esto, el default (`real_capital_base_usd`,
    # sin filtro de fecha) es sólo una aproximación nominal -- en la práctica
    # casi siempre difiere un poco del balance real desde el arranque (fees
    # de depósito, redondeo, rebates), generando divergencias falsas. Los dos
    # van juntos: sin `REAL_BALANCE_CHECKPOINT_AT`, `REAL_BALANCE_CHECKPOINT_USD`
    # se ignora (no hay forma de saber qué posiciones son "desde" el
    # checkpoint sin la fecha). Ver CLAUDE.md, sección Fase 3, incidente del
    # 2026-09-08.
    real_balance_checkpoint_usd: float | None = _float_env("REAL_BALANCE_CHECKPOINT_USD", 0.0) or None
    real_balance_checkpoint_at: str | None = os.getenv("REAL_BALANCE_CHECKPOINT_AT") or None
    # Proxy wallet ("Safe Wallet") que Polymarket asigna a cuentas conectadas con wallet
    # externa (MetaMask/Rabby) -- ahí vive el pUSD real, no en la EOA firmante. Sin
    # default: es específico de cada cuenta, no tiene un valor razonable genérico.
    real_funder_address: str | None = os.getenv("REAL_FUNDER_ADDRESS")
    # POLY_1271=3 por defecto -- ver CLAUDE.md, sección Fase 3, para por qué esto NO es
    # POLY_GNOSIS_SAFE=2 pese a que la documentación general de Polymarket asocia "Safe
    # Wallet"/MetaMask con ese valor: se verificó empíricamente contra el balance real
    # on-chain de la cuenta y sólo signature_type=3 lo reflejó. Configurable por cuenta.
    real_signature_type: int = _int_env("REAL_SIGNATURE_TYPE", 3)
    # Diferencia relativa máxima tolerada entre las shares reales de YES y NO
    # de una misma posición real antes de tratarla como exposición direccional
    # residual (mismo riesgo que un leg imbalance total, distinto camino para
    # llegar ahí -- ver `execution.real_executor._leg_imbalance_pct` y CLAUDE.md,
    # sección Fase 3, "Bug de sizing descubierto en la auditoría de la 4ta
    # activación"). 2% da margen a redondeo normal de tick size sin dejar pasar
    # un desbalance real como el observado (10.6%-24.2% en las 4 posiciones
    # afectadas).
    real_leg_imbalance_threshold_pct: float = _float_env("REAL_LEG_IMBALANCE_THRESHOLD_PCT", 0.02)
    # Reintentos de get_trades en execution.real_executor._confirmed_fill antes
    # de marcar una pata "sin_confirmar" -- cubren el lag de indexación
    # transitorio del exchange confirmado en producción el 2026-09-11 (el
    # mismo trade, consultado minutos después, sí aparecía). Ver CLAUDE.md,
    # sección Fase 3, "Bug de fallback silencioso en _confirmed_fill".
    real_fill_confirm_retries: int = _int_env("REAL_FILL_CONFIRM_RETRIES", 3)
    real_fill_confirm_retry_delay_seconds: float = _float_env("REAL_FILL_CONFIRM_RETRY_DELAY_SECONDS", 2.0)
    # Piso de valor en dólares por debajo del cual el exchange rechaza una
    # orden BUY de mercado -- confirmado con un rechazo real (2026-09-13,
    # incidente Getafe/Deportivo): "invalid amount for a marketable BUY
    # order ($0.52), min size: 1". NO es el mismo campo que `min_order_size`
    # del order book (`get_order_book`, en SHARES, varía por mercado -- p.ej.
    # 5 en otro mercado consultado en vivo -- y según la doc pública de
    # Polymarket rige el tamaño de órdenes resting/límite, no la validación
    # de una orden de mercado). No se pudo confirmar con 100% de certeza si
    # "min size: 1" en el mensaje de error es realmente $1 fijo o coincide
    # por casualidad con el `min_order_size` en shares de ESE mercado
    # puntual (su book ya no existe para volver a consultarlo, el mercado
    # resolvió) -- se eligió $1 por ser la lectura más literal del propio
    # mensaje de error real observado ("amount ($0.52)... min size: 1", los
    # dos en el mismo campo dólar) y por ser un mínimo ampliamente citado
    # como constante de la plataforma Polymarket en general. Configurable
    # para poder ajustarlo sin volver a tocar código si aparece evidencia de
    # que varía por mercado como `min_order_size`. Ver CLAUDE.md, sección
    # Fase 3, "Prevención de leg imbalance por presupuesto infeasible".
    real_min_order_value_usd: float = _float_env("REAL_MIN_ORDER_VALUE_USD", 1.0)
    # Auto-recuperación de "sin_confirmar" (2026-09-14, ver CLAUDE.md sección
    # Fase 3 y execution/real_resolution_job.py) -- 3 casos ya con la MISMA
    # huella exacta (status="delayed", success=true, sin errorMsg, 0 trades
    # reales confirmados incluso horas después) justifican automatizar el
    # proceso de verificación manual ya probado, no un atajo nuevo. Tiempo
    # mínimo desde el intento original antes de dar por buena la ausencia de
    # trade -- deliberadamente mucho más largo que los ~6s de reintentos de
    # `_confirmed_fill` (esa ventana corta fue justamente la causa del
    # incidente de Kashiwa Reysol, que sí encontró el trade real minutos
    # después). 5 minutos da margen amplio sobre cualquier lag de indexación
    # ya observado.
    real_unconfirmed_auto_recovery_delay_seconds: float = _float_env(
        "REAL_UNCONFIRMED_AUTO_RECOVERY_DELAY_SECONDS", 300.0
    )
    # Tope de auto-recuperaciones en una ventana móvil -- si se alcanza, la
    # siguiente auto-recuperación posible NO levanta el kill-switch (aunque
    # la posición puntual sí se verifique y se backfillee con su resultado
    # real) -- fuerza revisión humana igual, para detectar si el patrón
    # "conocido" empezó a comportarse distinto de lo esperado. Ventana móvil
    # sobre eventos persistidos, no "desde el último restart" -- un restart
    # del proceso no debe resetear la cuenta, ya que no equivale a que un
    # humano haya revisado nada.
    real_auto_recovery_max_per_window: int = _int_env("REAL_AUTO_RECOVERY_MAX_PER_WINDOW", 3)
    real_auto_recovery_window_hours: float = _float_env("REAL_AUTO_RECOVERY_WINDOW_HOURS", 72.0)

    # Bloqueo per-mercado de "sin_confirmar" (2026-09-14, ver CLAUDE.md sección
    # Fase 3 -- auditoría del 63/66 señales elegibles perdidas por downtime en
    # 48h): un `sin_confirmar` ya no detiene TODO el sistema, sólo el mercado
    # puntual (mientras su fila siga en ese status, `maybe_execute` lo omite
    # -- ver `real_executor._market_locked_by_unconfirmed`). Pero varios
    # mercados bloqueados en paralelo dejan de ser "un caso puntual" y pasan a
    # ser una señal sistémica (mismo criterio ya usado para
    # `auto_recovery_capped`) -- por eso, si la cantidad de posiciones
    # `sin_confirmar` simultáneas alcanza este tope, la SIGUIENTE sí dispara
    # el kill-switch GLOBAL, con un mensaje explícito de que es por el tope de
    # concurrencia, no porque ese caso puntual sea distinto de los demás.
    real_max_concurrent_sin_confirmar: int = _int_env("REAL_MAX_CONCURRENT_SIN_CONFIRMAR", 2)
    # NO hay una ventana de gracia de reconciliación específica para
    # "sin_confirmar" -- se probó una (2026-09-14, 540s, ya retirada) y un
    # caso real (posición 21, "Daejeon Citizen FC", 2026-09-15) mostró que
    # el enfoque estaba mal de raíz: el partido tardó 2.55h en resolver, no
    # unos minutos, y ninguna ventana fija razonable cubre eso. En vez de
    # esperar, `reconciliation.check_balance_reconciliation_with_grace`
    # resta de la divergencia el `cost_usd` conocido de las posiciones
    # `sin_confirmar` actuales (`_sin_confirmar_committed_cost_usd`) -- si
    # eso explica toda la divergencia, no hace falta esperar nada. Ver
    # CLAUDE.md, sección Fase 3, para el post-mortem completo y por qué no
    # se debe volver a proponer una ventana más larga para esto.


settings = Settings()
