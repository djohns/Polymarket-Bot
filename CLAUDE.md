# CLAUDE.md — Bot de trading Polymarket

Leer este archivo una vez al empezar la sesión. Contiene todo lo ya decidido para
no volver a explorar lo resuelto. Los documentos de referencia completos están en
la raíz del repo:

- [`compass_artifact_wf-2d893325-14fa-5738-bcef-3ec971cd4899_text_markdown.md`](compass_artifact_wf-2d893325-14fa-5738-bcef-3ec971cd4899_text_markdown.md) — informe técnico (arquitectura, APIs, comisiones, edge documentado, riesgos).
- [`polymarket-bot-plan-ejecucion.md`](polymarket-bot-plan-ejecucion.md) — plan de ejecución por fases.

No pegar el contenido de esos informes en prompts ni volver a investigarlos: son
la fuente de verdad ya cerrada.

## Roles

- El **Proyecto de Claude** (chat, fuera de este repo) decide qué y por qué:
  arquitectura, estrategia, revisión de riesgo, aprobación de avance de fase.
- **Claude Code** (acá) ejecuta: escribe y prueba código según instrucciones
  puntuales, con el detalle técnico ya masticado en este archivo y en `/docs`.
- Las tareas se piden por fase, no todas juntas.

## Estrategia confirmada (no volver a evaluar otras)

1. **Arbitraje intra-mercado**: detectar cuando YES + NO ≠ $1 en el order book
   de un mismo mercado binario, con umbral configurable. Es la estrategia con
   mayor respaldo empírico (paper IMDEA, arXiv:2508.03474).
2. **Market-making pasivo con órdenes límite**: colocar como maker (GTC/GTD,
   post-only), cobrar 0% fee + rebate (15–25% de comisiones taker), sin
   competir en latencia con bots sub-100ms.

Explícitamente descartado por ahora: estrategias direccionales/momentum,
arbitraje cross-platform (Polymarket vs Kalshi) por comisiones combinadas que
matan el margen, y cualquier cosa que dependa de velocidad sub-segundo.

## Fases (ver plan de ejecución para detalle completo)

- **Fase 0 (actual)**: scaffolding del repo, `CLAUDE.md`, conexión de solo
  lectura a Gamma/CLOB. Sin trading real, sin private key.
- **Fase 1**: ingesta WebSocket (canal `market`), motor de señales (arb
  intra-mercado v1, sesgo favorito-longshot v2), persistencia de oportunidades
  detectadas. Sin ejecutar nada real.
- **Fase 2**: paper trading (fills simulados contra order book real), dashboard
  v1, Kelly fraccionado (¼) simulado. Umbral de salida: Brier <0,20, P&L neto
  positivo, margen de arb consistente >6%.
- **Fase 3**: vivo con capital mínimo (decenas–cientos de USDC). Ejecución
  real firmada, kill-switch, wallet con private key cifrada en reposo.
- **Fase 4**: escalado condicional (drawdown real ≤ 2× simulado).

Cada fase se pide explícitamente desde el chat del proyecto. No adelantar
trabajo de fases futuras sin que se pida.

## Arquitectura del repo

```
src/polybot/
  ingestion/
    gamma_discovery.py  # Polling periódico de mercados binarios activos (Fase 1)
    orderbook.py         # OrderBook/OrderBookStore: reconstrucción local desde WS (Fase 1)
    ws_client.py          # Cliente WS canal `market`, reconexión+heartbeat (Fase 1)
  signals/
    fees.py       # Estimación de fee taker desde feeSchedule por mercado (Fase 1)
    arbitrage.py  # Detección arb intra-mercado v1 (Fase 1)
    longshot.py   # Detección sesgo favorito-longshot v2 (Fase 1, sólo señal informativa)
  risk/         # Position sizing (Kelly fraccionado), límites por mercado/cluster (Fase 2+)
  execution/    # Firma EIP-712, órdenes límite, manejo de fills (Fase 3+)
  persistence/
    models.py   # Opportunity (SQLAlchemy) — oportunidades/señales detectadas (Fase 1)
    db.py       # Engine/session SQLite
  dashboard/    # Streamlit/Dash: posiciones, P&L, Brier score (Fase 2+)
  config.py     # Settings desde .env, URLs de APIs, umbrales de señales
  main.py       # Runner Fase 1: ingesta + señales + persistencia, sin ejecución
scripts/
  test_connection.py   # Prueba de solo lectura Gamma+CLOB (Fase 0)
docs/
  setup.md      # Cómo correr el proyecto
tests/
```

## NO volver a explorar esto (ya resuelto)

- **SDK de trading**: `py-clob-client-v2` (PyPI, paquete `py_clob_client_v2`),
  no `py-clob-client` (v1, para el contrato viejo). `ClobClient` se instancia
  con `host`, `chain_id` (137 = Polygon mainnet); `key`/`creds` son opcionales
  y sólo se necesitan para trading, no para lectura.
- **URLs de APIs** (ya en `src/polybot/config.py`, no hardcodear de nuevo):
  - Gamma: `https://gamma-api.polymarket.com` (pública, sin auth, para
    descubrimiento de mercados/metadata).
  - CLOB: `https://clob.polymarket.com` (lectura pública; trading requiere
    L1 EIP-712 + L2 HMAC).
  - Data: `https://data-api.polymarket.com` (historial de trades/posiciones).
  - WebSocket CLOB: `wss://ws-subscriptions-clob.polymarket.com/ws/`
    (canales `market`, `user`, `sports`, `rfq` — no mezclar con RTDS).
  - RTDS (precios cripto/comentarios): `wss://ws-live-data.polymarket.com`.
- **Formato de datos Gamma**: los campos `outcomes`, `outcomePrices` y
  `clobTokenIds` de `/markets` vienen como **strings JSON**, hay que
  `json.loads()` antes de usarlos. No son arrays nativos.
- **Formato de respuesta CLOB**: `get_order_book(token_id)` devuelve un dict
  (no un objeto con atributos) con claves `bids`/`asks`. Verificado en Fase 0
  con `scripts/test_connection.py`.
- **No hay testnet de producción**: toda prueba de la ruta de ejecución real
  cuesta dinero/gas real. Por eso Fase 0–2 son de solo lectura/simulación.
- **Colateral**: desde la migración CLOB V2 (28 abr 2026) es `pUSD`, no
  `USDC.e`. No usar documentación de trading previa a esa fecha sin verificar
  contra docs.polymarket.com.
- **Comisiones**: taker paga fee variable por categoría (fórmula y tabla en
  el informe técnico); maker paga 0% y recibe rebate. Por eso la estrategia
  prioriza órdenes límite como maker.
- **Seguridad de wallet**: private key nunca en texto plano ni en `.env`
  commiteado; en Fase 3 se cifra en reposo. `.env` está en `.gitignore`.
- **Jurisdicción**: Chile no está restringido por Polymarket (a diferencia de
  Argentina/Brasil). No re-investigar esto salvo que cambie la política de
  Polymarket.
- **Formato de mensajes del WS `market`** (verificado empíricamente en Fase 1,
  no está en la documentación oficial con este detalle):
  - Al suscribirse (`{"assets_ids": [...], "type": "market"}`) el servidor
    responde con una **lista JSON** de eventos `book` (uno por asset_id), cada
    uno con `bids`/`asks` como listas de `{"price": str, "size": str}`.
  - Los updates incrementales llegan como eventos `price_change` (a veces
    envueltos en lista, a veces objeto suelto — el cliente maneja ambos casos)
    con `price_changes: [{asset_id, price, size, side, best_bid, best_ask}]`.
    `side` es `"BUY"`/`"SELL"` (bid/ask respectivamente); `size="0"` significa
    que ese nivel de precio se vació y hay que eliminarlo del book local.
  - **Heartbeat real**: no es un frame WS ping/pong estándar. Se manda el
    string plano `"PING"` cada 10s y el servidor responde `"PONG"` como texto,
    no JSON — hay que filtrarlo antes de intentar parsear JSON.
- **Fees por mercado (mejora sobre el informe)**: Gamma devuelve `feeSchedule`
  (`{rate, exponent, takerOnly, rebateRate}`) **directamente en cada mercado**
  vía `/markets`, en vivo y por mercado — más preciso que la tabla estática de
  tasas por categoría del informe técnico (que puede estar desactualizada).
  Fórmula confirmada: `fee = shares × rate × (price × (1−price))^exponent`
  (coincide exactamente con el ejemplo del informe para crypto: `rate=0.07` →
  $1.75 por 100 shares a 50¢). `takerOnly: true` en todos los schedules
  observados, consistente con "maker paga 0%". Mercados sin `feeSchedule`
  (ej. algunos de geopolítica) no cobran fee. **Usar siempre este campo en vez
  de hardcodear tasas por categoría.**
- **Descubrimiento de mercados**: Gamma `/markets` soporta `order=volume24hr`
  y `ascending=false` para paginar por volumen desde el servidor — no hace
  falta traer todo y ordenar en cliente. Fase 1 sigue sólo mercados
  **binarios** (`outcomes` con exactamente `["Yes", "No"]`); multi-outcome y
  negRisk quedan fuera del scope hasta que se pidan explícitamente.
- **Dirección de trading del sesgo favorito-longshot (confirmada)**: se apuesta
  contra el sesgo en cada extremo, no es "corregir hacia 0,50" en términos de
  acción. Longshot caro/sobrevalorado (precio < `LONGSHOT_PRICE_LOW`) ->
  comprar el outcome contrario. Favorito barato/infravalorado (precio >
  `LONGSHOT_PRICE_HIGH`) -> comprar ese mismo outcome. El cálculo de magnitud
  (`corrected_probability`, corrección hacia 0,50) sólo estima distancia/
  severidad de la señal; la dirección de trading es un campo aparte
  (`trade_direction`) calculado con esta regla. Sigue siendo señal informativa,
  no ejecutable, hasta Fase 2.
- **Arquitectura de ingesta implementada**: `gamma_discovery.py` (polling
  periódico, `DISCOVERY_INTERVAL_SECONDS`) + `orderbook.py` (`OrderBookStore`,
  un `OrderBook` por asset_id) + `ws_client.py` (reconexión con backoff
  exponencial, máx. 60s). Si el set de mercados activos cambia entre
  descubrimientos, se cancela la tarea WS vigente y se abre una nueva sesión
  con el set actualizado — no hay resuscripción incremental sobre la misma
  conexión.

## Convenciones de código

- Python 3.11+, gestionado con `pyproject.toml` (no `requirements.txt`).
- Paquete instalable en modo editable: `pip install -e ".[dev]"`.
- Imports absolutos desde `polybot.*` (con `src/` en el path vía el propio
  paquete instalado, no `PYTHONPATH` manual salvo en scripts sueltos).
- Sin comentarios explicando qué hace el código; sólo por qué, cuando no es
  obvio (ver reglas generales del asistente).
- Tipado con type hints en funciones públicas.
- `ruff` para lint (config en `pyproject.toml`).
- No introducir abstracciones ni manejo de errores para casos que no pueden
  ocurrir en la fase actual — construir sólo lo que la fase pide.
