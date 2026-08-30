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
| `THE_ODDS_API_KEY` | Comparar odds deportivas vs. mercados | No (Fase 1) |
| `DATABASE_URL` | Persistencia de oportunidades/órdenes | No (Fase 1) |

## Probar la conexión de solo lectura

Valida Gamma API (listado de mercados) y CLOB API (order book), sin necesidad de
credenciales:

```bash
PYTHONPATH=src python scripts/test_connection.py
```

Salida esperada: lista de mercados activos desde Gamma y niveles de bid/ask del
order book de uno de ellos desde CLOB.

## Estructura del repo

Ver [`CLAUDE.md`](../CLAUDE.md) en la raíz para arquitectura completa y decisiones
tomadas.
