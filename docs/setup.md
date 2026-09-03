# Setup del proyecto

## Requisitos
- Python 3.11+
- (Opcional, fase 1+) SQLite ya viene con Python; Postgres si se decide migrar.

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Variables de entorno

Copiar `.env.example` a `.env`. En Fase 0 pueden quedar todas vacías: sólo se usa
lectura pública de Gamma/CLOB, sin autenticación de trading.

| Variable | Uso | Requerida en Fase 0 |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | Firma EIP-712 (wallet dedicada del bot) | No |
| `CLOB_API_KEY` / `CLOB_API_SECRET` / `CLOB_API_PASSPHRASE` | Credenciales L2 CLOB (trading) | No |
| `POLYGON_RPC_URL` | RPC Polygon para lectura on-chain | No (fallback público) |
| `THE_ODDS_API_KEY` | Comparar odds deportivas vs. mercados | No (Fase 2+) |
| `DATABASE_URL` | Persistencia de oportunidades/órdenes | No (default SQLite) |
| `ARB_THRESHOLD` | Umbral bruto de arbitraje intra-mercado (Fase 1) | No (default 0.03) |
| `LONGSHOT_PRICE_LOW` / `LONGSHOT_PRICE_HIGH` | Rango de precio para señal favorito-longshot | No (default 0.15/0.85) |
| `LONGSHOT_CORRECTION` | Corrección hacia 0.50 aplicada en la señal v2 | No (default 0.05) |
| `DISCOVERY_INTERVAL_SECONDS` | Frecuencia de re-descubrimiento de mercados en Gamma | No (default 120) |
| `DISCOVERY_MARKET_LIMIT` | Nº de mercados binarios activos a seguir (por volumen 24h) | No (default 100) |
| `OPPORTUNITY_LOG_COOLDOWN_SECONDS` | Anti-flood: mínimo entre logs repetidos del mismo mercado+señal | No (default 30) |
| `ARB_CAPITAL_BASE` | Capital base simulado (USD) para sizing de arb (Fase 2) | No (default 1000.0) |
| `ARB_MAX_FRACTION_PER_TRADE` | Fracción máxima del capital base por posición de arb | No (default 0.05) |
| `ARB_MAX_EXPOSURE_PER_MARKET` | Exposición abierta máxima (fracción) por mercado | No (default 0.10) |
| `ARB_MAX_EXPOSURE_PER_CLUSTER` | Exposición abierta máxima (fracción) por cluster de eventos | No (default 0.20) |
| `KELLY_FRACTION` | Fracción de Kelly para señales de edge incierto (sin uso activo hoy) | No (default 0.25) |
| `RESOLUTION_CHECK_INTERVAL_SECONDS` | Frecuencia del job de resolución de mercados (Fase 2) | No (default 900) |
| `RESOLUTION_STALE_AFTER_DAYS` | Días sin resolver antes de marcar una posición "pendiente" (posible disputa) | No (default 7) |

## Probar la conexión de solo lectura

Valida Gamma API (listado de mercados) y CLOB API (order book), sin necesidad de
credenciales:

```bash
PYTHONPATH=src python scripts/test_connection.py
```

Salida esperada: lista de mercados activos desde Gamma y niveles de bid/ask del
order book de uno de ellos desde CLOB.

## Correr la ingesta y detección de señales (Fase 1)

Sin trading real, sólo lectura de mercado y logging de oportunidades detectadas
a SQLite (`data/polybot.db` por defecto):

```bash
python -m polybot.main
```

Corre indefinidamente: descubre mercados binarios activos (por volumen 24h),
mantiene su order book local vía WebSocket, y loguea cada arbitraje
intra-mercado (`ARB_THRESHOLD`) y señal de sesgo favorito-longshot detectada.
Desde Fase 2 parte 1, cada arbitraje neto viable también dispara un fill
simulado contra el order book real (tabla `simulated_positions`) — sin firmar
ni enviar nada. Ver CLAUDE.md, sección "Fase 2, parte 1" para el detalle del
sizing y la simulación.

## Estructura del repo

Ver [`CLAUDE.md`](../CLAUDE.md) en la raíz para arquitectura completa y decisiones
tomadas.
