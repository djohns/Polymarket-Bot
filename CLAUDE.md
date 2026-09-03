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

- **Fase 0**: scaffolding del repo, `CLAUDE.md`, conexión de solo
  lectura a Gamma/CLOB. Sin trading real, sin private key.
- **Fase 1**: ingesta WebSocket (canal `market`), motor de señales (arb
  intra-mercado v1, sesgo favorito-longshot v2), persistencia de oportunidades
  detectadas. Sin ejecutar nada real.
- **Fase 2 (actual)**: paper trading. Umbral de salida: Brier <0,20, P&L neto
  positivo, margen de arb consistente >6%.
  - **Parte 1 (hecha)**: simulador de ejecución (fills hipotéticos de arb
    contra el order book real, con slippage por profundidad) + position
    sizing ("fórmula de ganancia garantizada" para arb, Kelly fraccionado ¼
    como primitiva lista para señales futuras de edge incierto). Ver sección
    dedicada más abajo.
  - **Parte 2 (hecha)**: tracking de resolución real de mercados (cierre de
    posiciones de arb con P&L realizado) y Brier score (sólo aplica a
    longshot, no a arb — ver sección dedicada más abajo).
  - **Parte 3 (hecha)**: dashboard — reporte HTML estático regenerado por
    systemd timer, no un server vivo (Streamlit descartado por RAM). Ver
    sección dedicada más abajo.
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
    brier.py      # Brier score de calibración — sólo longshot, no arb (Fase 2, parte 2)
  risk/
    kelly.py    # Kelly fraccionado (¼) — primitiva reusable, NO usada por el arb (Fase 2)
    sizing.py   # Tope de capital para arb: "fórmula de ganancia garantizada" (Fase 2)
  execution/
    simulator.py       # Simulador de fills de arb contra el order book real, sin firmar (Fase 2)
    resolution.py       # Consulta de resolución real vía CLOB (Fase 2, parte 2)
    resolution_job.py    # Cierra posiciones resueltas + backfill de Brier (Fase 2, parte 2)
  persistence/
    models.py   # Opportunity + SimulatedPosition (Fase 2 p.1) + SignalResolution (Fase 2 p.2)
    db.py       # Engine/session SQLite
  dashboard/
    snapshot.py  # Agregados SQL -> dataclass Snapshot (Fase 2, parte 3)
    render.py    # Snapshot -> HTML autocontenido (SVG a mano, sin dependencias)
    report.py    # Job puntual: genera y escribe data/dashboard.html a disco
  config.py     # Settings desde .env, URLs de APIs, umbrales de señales
  main.py       # Runner: ingesta + señales + simulador de ejecución, sin trading real
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
- **Memory leak corregido (detectado en revisión de salud, 8.5h de RSS
  52.7MB → 103.5MB)**: `OrderBookStore` se crea una única vez en `run()` y se
  reutiliza en cada resuscripción del WS, pero nunca se purgaban los
  `OrderBook` de assets que salían del top-N por volumen cuando el set de
  mercados rotaba (rota seguido: 5 veces en 1h vista en los logs). Sin cota,
  esto habría llevado a OOM en la instancia de 498MB antes de completar la
  semana de acumulación de Fase 1. Fix: `OrderBookStore.keep_only(active_ids)`
  purga los books fuera del set activo, llamado en `run()` cada vez que el
  set de mercados cambia (después de abrir la nueva sesión WS). Validado en
  vivo en la VPS tras reiniciar el servicio: RSS subió de 58.2MB a ~91MB en
  los primeros ~55 min (llenado inicial normal de 200 order books desde
  cero, con 5 purgas confirmadas en los logs durante ese período), y luego
  quedó **plana 11 minutos seguidos** en 91.3MB antes de un incremento
  marginal (+0.9MB) — comportamiento de meseta acotada, muy distinto del
  crecimiento lineal sin techo observado antes del fix (+6.4MB/hora sobre
  8.5h sin ninguna meseta).

## Fase 2, parte 1 — simulador de ejecución + position sizing

- **Sizing de arb: NO usa Kelly (decisión explícita, confirmada con el usuario).**
  El informe técnico distingue dos reglas en la sección de modelo estadístico:
  *"baskets bloqueados (arb puro) usan fórmula de ganancia garantizada; todo lo
  demás usa cuarto de Kelly"*. El arb "long" (comprar YES+NO<$1) paga $1
  garantizado al vencimiento sin importar el resultado — no hay probabilidad
  incierta que ponderar, así que aplicarle Kelly sería forzar una fórmula que el
  propio informe dice que no corresponde. `risk/kelly.py` implementa
  `fractional_kelly(p, price, fraction)` = `(p − price) / (1 − price) × fracción`
  (Kelly estándar para apuesta binaria) como primitiva lista para cuando se
  ejecute una señal de edge incierto en una fase futura (ej. favorito-longshot),
  pero **hoy no la invoca nadie** — el arb usa en cambio
  `risk/sizing.py::max_capital_for_arb_trade`.
- **"Fórmula de ganancia garantizada" (sizing real del arb)**: no pondera por
  probabilidad — maximiza el tamaño rentable disponible en el book, acotado por
  límites de exposición (capital total, por mercado, por cluster). El tope de
  capital para una posición nueva es el mínimo entre: `ARB_CAPITAL_BASE ×
  ARB_MAX_FRACTION_PER_TRADE` (tope por trade), y el espacio restante hasta
  `ARB_CAPITAL_BASE × ARB_MAX_EXPOSURE_PER_MARKET` / `× ARB_MAX_EXPOSURE_PER_CLUSTER`
  descontando la exposición ya abierta (`SimulatedPosition.status == "abierta"`,
  sumada vía SQL agregado, nunca cargando filas a Python — ver lección de la
  sesión de análisis de cierre de Fase 1). Los límites acotan por riesgo real
  (disputa de oráculo, lock-up de capital hasta resolución, ejecución no
  atómica — ver sección de riesgos del informe), no porque el edge en sí sea
  incierto.
- **Simulación de fill con profundidad real (`execution/simulator.py`)**: camina
  los niveles ask de YES y NO en paralelo (1 share de cada uno por unidad de
  arb comprada). El precio marginal de cada libro es no decreciente al avanzar
  en profundidad, así que se sigue acumulando tamaño mientras el borde neto
  (precio marginal + fee, contra el payout de $1) siga siendo positivo, hasta
  agotar la profundidad de cualquiera de los dos books o el tope de capital —
  lo que ocurra primero. Reporta por separado: `cost_usd` (capital
  comprometido), `fee_estimate`, `slippage_estimate` (= costo real − costo al
  mejor precio, ambos ×tamaño), `gross_pnl` (= shares − cost_usd, el payout
  garantizado menos el costo) y `net_pnl` (= gross_pnl − fee_estimate).
- **P&L no se marca realizado**: por instrucción explícita del usuario, aunque
  `gross_pnl`/`net_pnl` son matemáticamente el resultado bloqueado de un basket
  YES+NO completamente lleno (determinístico salvo riesgo de oráculo/ejecución),
  `SimulatedPosition` se persiste con `status="abierta"` y `realized_pnl=NULL`.
  La confirmación de resolución real (y el cierre de la posición) se implementa
  en la parte 2 de Fase 2.
- **Criterio de "cluster correlacionado" elegido**: `MarketInfo.cluster_id` usa
  el campo `events[0].id` que Gamma ya devuelve en cada mercado (el evento
  agrupador — ej. todas las carreras de un partido de tenis, o todos los
  candidatos de una elección, comparten un mismo `events[].id`). Si un mercado
  no pertenece a ningún evento, `cluster_id` cae a su propio `condition_id`
  (cluster de tamaño 1). Es el criterio más simple disponible sin lógica nueva
  de NLP/similaridad, y se apoya en una agrupación que Polymarket ya mantiene
  editorialmente.
- **El fill se dispara con el mismo cooldown que la señal de arb** (`SignalEngine`,
  `OPPORTUNITY_LOG_COOLDOWN_SECONDS`, default 30s): evita abrir una posición
  simulada nueva en cada tick del book mientras persiste el mismo mispricing.
  Es auto-limitante además por los topes de exposición: una vez que un mercado o
  cluster llega a su límite, `max_capital_for_arb_trade` devuelve 0 y no se abren
  más posiciones ahí hasta que el cooldown expire en otro ciclo Y haya espacio
  libre (posiciones cerradas, aún no implementado en esta parte).
- **Nueva tabla `simulated_positions`** (`persistence/models.py`), separada de
  `opportunities` (Fase 1, que sigue registrando toda detección igual que antes,
  sin cambios). Fase 1 no se interrumpe: el simulador se engancha al mismo
  evento (`_check_arbitrage`), no reemplaza el logging existente.
- **Variables nuevas en `.env`** (todas con default razonable):
  `ARB_CAPITAL_BASE` (1000.0), `ARB_MAX_FRACTION_PER_TRADE` (0.05),
  `ARB_MAX_EXPOSURE_PER_MARKET` (0.10), `ARB_MAX_EXPOSURE_PER_CLUSTER` (0.20),
  `KELLY_FRACTION` (0.25, sin uso activo por ahora).

## Fase 2, parte 2 — resolución real de mercados + Brier score

- **Cómo se determina "resuelto"**: vía CLOB, `GET /markets/{condition_id}`
  (no Gamma). Gamma `/markets` **no filtra por `condition_id`/`conditionId`**
  — el parámetro se ignora en silencio y devuelve el listado paginado sin
  filtrar (verificado empíricamente); sólo permite buscar un mercado puntual
  por su `id` numérico interno, que este proyecto no guarda en ningún lado.
  CLOB en cambio expone `GET /markets/{condition_id}` directo, con `closed`
  (bool) y, por cada token de outcome, un flag `winner` (bool) — mucho más
  directo que parsear `outcomePrices` de Gamma (que en mercados legacy
  pre-2026 ni siquiera trae `umaResolutionStatus` poblado). Un mercado se
  considera resuelto cuando `closed=True` y **exactamente un** token tiene
  `winner=True`; el nombre de ese token (`"Yes"`/`"No"`, verificado contra un
  mercado real ya resuelto) es el outcome ganador. Si está `closed=True` pero
  ningún token (o más de uno) tiene `winner=True`, se trata como no resuelto
  todavía — oráculo o disputa en curso.
- **Frecuencia del job**: `RESOLUTION_CHECK_INTERVAL_SECONDS` (default 900s /
  15min). Los mercados tardan horas a días en resolver (propuesta UMA +
  ventana de disputa ~2h, o 4-6 días si escala al DVM — ver informe técnico),
  así que no hace falta pollear más seguido; se prioriza no gastar cuota de
  API ni ciclos de CPU en un proceso ya ajustado de RAM.
- **No bloquea el loop de ingesta/detección**: el job corre en el mismo
  event loop (mismo proceso, `asyncio.create_task` separado del loop de
  descubrimiento/WS), pero `execution/resolution.py` usa
  `httpx.AsyncClient` (no `httpx.Client` síncrono) — cada `await` cede el
  control, así que una consulta HTTP lenta no traba el heartbeat/reconexión
  del WebSocket como pasaría con una llamada bloqueante en el mismo hilo.
- **Scope del job**: sólo consulta mercados con `SimulatedPosition.status in
  ("abierta", "pendiente")` — un set chico, acotado por los límites de
  exposición de `risk/sizing.py` (parte 1), no toda la población de mercados
  vistos por Fase 1.
- **P&L realizado**: para arb "long", el payout es $1/share sin importar el
  outcome, así que `realized_pnl` se recalcula independientemente
  (`shares − cost_usd − fee_estimate`) en vez de copiar `net_pnl` del fill, y
  se compara contra ese `net_pnl` como chequeo de consistencia (debería
  coincidir siempre salvo bug; si difiere se loguea un WARNING). `status`
  pasa a `"cerrada"` y se guarda `resolved_outcome` + `resolved_at`.
- **Caso borde — no resuelve a tiempo / posible disputa**: si un mercado
  queda `closed=True` sin ganador único por más de `RESOLUTION_STALE_AFTER_DAYS`
  (default 7, con margen sobre los 4-6 días típicos de escalada al DVM de UMA
  citados en el informe) desde que se abrió la posición, se marca
  `status="pendiente"` y se loguea un WARNING una sola vez por posición (set
  en memoria, se resetea si el proceso reinicia — aceptable dado el volumen
  bajo). El job sigue reintentando esa posición indefinidamente, nunca la
  descarta. No se distingue "disputa activa" de "oráculo simplemente lento"
  porque CLOB no expone esa granularidad — es la limitación aceptada de usar
  la fuente más simple y confiable disponible en vez de cruzar con Gamma
  (que tampoco permite un lookup puntual, ver arriba).
- **Caso borde — mercado no encontrado (404) o error de red**: se loguea un
  WARNING y se reintenta en el próximo ciclo; nunca rompe el job ni el
  proceso completo (`resolution_loop` además envuelve todo el ciclo en
  try/except).
- **Límite reconocido, no resuelto en esta parte**: `closed=True` +
  `winner=True` en CLOB refleja que el oráculo UMA asentó el resultado (pasó
  la ventana de disputa), pero no hay verificación on-chain propia de que la
  redención efectivamente ocurrió sin reversión — el informe cita el paper
  "The Ghosts of Polymarket" (arXiv:2606.16852) sobre fills que revierten
  on-chain. Confirmar eso requeriría un listener de eventos on-chain
  (Fase 3+, capa de ejecución real); por ahora se confía en el estado que
  Polymarket expone como su propia fuente de verdad.
- **Brier score — lectura pedida explícitamente sobre si aplica a arb puro**:
  **NO aplica.** El arb "long" no tiene una predicción direccional: comprar
  YES+NO<$1 paga $1 al vencimiento sin importar qué outcome gane — es
  deliberadamente agnóstico sobre el resultado (por eso es "riskless" en
  concepto). No existe una "probabilidad implícita" que comparar contra el
  resultado real; forzar un Brier score ahí sería puntuar una predicción que
  nunca se hizo. La señal que sí produce una probabilidad implícita real es
  el sesgo favorito-longshot (`Opportunity.corrected_price`, con
  `Opportunity.outcome` — columna nueva esta parte — indicando a qué lado
  refiere). El Brier score se implementa sobre esa señal (`signals/brier.py`).
- **Cobertura del Brier de longshot es parcial por diseño, no completa**: el
  job de resolución sólo consulta activamente mercados con posiciones de
  arb — resolver también los ~350+ mercados (y creciendo) que sólo tuvieron
  señal longshot habría sido una ampliación de scope bastante más grande
  (varias veces más llamadas a CLOB por ciclo) que lo que se pidió
  explícitamente ("mercados que tienen posiciones simuladas abiertas o
  pendientes"). En cambio, cuando el job resuelve un mercado por su posición
  de arb, aprovecha esa misma consulta (ya pagada) para completar
  `SignalResolution` si ese mercado también tuvo señales longshot — a costo
  cero de API extra. Esto da cobertura real pero sesgada (sólo mercados que
  también tuvieron arb, no una muestra representativa de todo el longshot).
  Si se necesita calibración representativa de longshot antes de que se
  vuelva una señal ejecutable, ampliar la resolución a la población completa
  de mercados longshot es un trabajo aparte para una fase futura.
- **No hay tabla de Brier "acumulado"**: `signals/brier.py` calcula el score
  al vuelo (agrupado por día o semana, `brier_score_by_window`) a partir de
  `opportunities` × `signal_resolutions`, en vez de mantener una tabla
  derivada recalculada por otro job. Al volumen de datos actual (cobertura
  parcial, probablemente decenas de muestras por bastante tiempo) recalcular
  on-demand es barato y evita mantener un agregado que se puede desincronizar;
  se puede añadir una tabla persistida más adelante si el dashboard (parte 3)
  necesita servir gráficos históricos sobre mucho volumen. `scripts/brier_report.py`
  expone el reporte hoy sin esperar al dashboard.
- **Nueva columna `Opportunity.outcome`** ("YES"/"NO", sólo longshot): antes
  de esta parte, `outcome_price`/`corrected_price` no registraban a qué lado
  del mercado referían — hacía imposible saber si la "probabilidad predicha"
  correspondía a YES o a NO al cruzar contra el resultado real. Se completa
  desde `LongshotSignal.outcome`, que ya existía en el motor de señales
  (`signals/longshot.py`) pero no se persistía.
- **Bug de SQLite + SQLAlchemy encontrado y corregido**: `DateTime(timezone=True)`
  no hace round-trip del `tzinfo` en SQLite — un valor se guarda en UTC
  correctamente, pero al releerse después de que la sesión expira el objeto
  vuelve *naive* (sin tzinfo), aunque el dato en sí siga siendo UTC. Rompía
  la resta `now - pos.opened_at` en el chequeo de staleness
  (`TypeError: can't subtract offset-naive and offset-aware datetimes`).
  Fix: en `execution/resolution_job.py`, si `opened_at.tzinfo is None` se
  reetiqueta como UTC (`.replace(tzinfo=dt.UTC)`) antes de operar — no es una
  conversión real de huso horario, sólo reponer la etiqueta que SQLite perdió.
  Vale la pena tenerlo presente para cualquier código futuro que haga
  aritmética de fechas sobre columnas `DateTime(timezone=True)` releídas de
  este SQLite.
- **Variables nuevas en `.env`**: `RESOLUTION_CHECK_INTERVAL_SECONDS` (900),
  `RESOLUTION_STALE_AFTER_DAYS` (7).

## Fase 2, parte 3 — dashboard

- **Decisión: reporte HTML estático regenerado periódicamente, NO un server
  vivo (Streamlit/Dash descartado).** Justificación con el contexto de
  recursos de la VPS (498MB RAM, ya con un memory leak corregido y un susto
  de memoria por una query pesada en sesiones anteriores):
  - Streamlit por sí solo tiene un footprint base de ~80-150MB de RSS sólo
    por el framework, más lo que retenga en `session_state` — sumado al bot
    (~50-90MB ya observados en producción), es una fracción grande y
    **permanente** de 498MB para un dashboard que probablemente se mira
    unas pocas veces por semana, no continuamente.
  - Un server vivo necesita: puerto abierto (más superficie en una VM ya
    endurecida), manejo de acceso concurrente a un SQLite que no está en modo
    WAL por defecto (mismo problema de "database is locked" que ya se vio en
    la sesión de análisis de cierre de Fase 1), y otro proceso systemd
    corriendo 24/7 compitiendo por RAM con el bot en todo momento, no sólo
    cuando alguien mira el dashboard.
  - Un job puntual (`systemd.timer` + `.service` tipo `oneshot`) que corre,
    consulta agregados SQL, escribe un HTML y termina, tiene **footprint de
    RAM cero fuera de su propia ejecución** (segundos), que es exactamente
    el patrón que ya funciona bien para el job de resolución (parte 2) y
    para el bot principal.
  - El archivo resultante es HTML autocontenido (CSS inline, gráficos en SVG
    generado a mano en `dashboard/render.py`, sin JS ni librerías externas)
    — se puede abrir localmente sin conexión, sin depender de que la VPS
    esté sirviendo nada en ese momento.
- **Cómo se regenera**: `polymarket-bot-dashboard.timer` (systemd,
  `OnUnitActiveSec=15min`, `Persistent=true`) dispara
  `polymarket-bot-dashboard.service` (`Type=oneshot`), que corre
  `python -m polybot.dashboard.report` y escribe `data/dashboard.html`. Sin
  RAM persistente: mismo patrón que el resto del proyecto (no se introdujo
  ninguna herramienta nueva, sólo otro par service+timer junto al que ya
  existía).
- **Cómo se ve desde el chat del proyecto**: el timer en la VPS mantiene el
  archivo fresco, pero no hay forma de que un HTML estático en un servidor
  privado "avise" solo al chat. El flujo es: pedirle a Claude Code que traiga
  la última versión (`scp`) y la publique como Artifact — eso da una vista
  pulida en el chat bajo demanda, sin mantener nada corriendo. Alternativa
  sin depender de Claude Code: `scp` directo y abrir el archivo local.
- **Modo WAL en SQLite** (`persistence/db.py`): se agregó `PRAGMA
  journal_mode=WAL` + `PRAGMA busy_timeout=5000` en el evento `connect` del
  engine. Antes, el modo por defecto (rollback journal) causaba que
  cualquier lector concurrente con el bot escribiendo pudiera fallar con
  "database is locked" — ya pasó más de una vez en sesiones de diagnóstico
  ad-hoc. WAL permite lectores concurrentes sin bloquear al escritor (y
  viceversa), que es exactamente el patrón de este dashboard (lee cada 15
  min mientras el bot escribe constantemente). `busy_timeout` queda como red
  de seguridad adicional. Aplica a todo el proyecto desde ahora, no sólo al
  dashboard.
- **P&L no realizado es una aproximación, no mark-to-market real**: para
  posiciones abiertas/pendientes, el dashboard suma `net_pnl` (el valor
  calculado al momento del fill en Fase 2 parte 1) en vez de re-consultar el
  order book actual y recalcular con precios en vivo — el job del dashboard
  no tiene acceso al `OrderBookStore` en memoria del proceso principal (son
  procesos separados) y volver a pedir el book completo por HTTP para cada
  posición abierta habría sido una ampliación de scope no pedida. El
  dashboard lo aclara explícitamente en la propia página, no lo esconde.
- **Hit rate esperado ~100% para arb — no es un bug si lo es**: se documenta
  en el propio dashboard para que no se lea como "no hay nada interesante
  que ver": el arb paga $1/share sin importar el resultado, así que un hit
  rate bajo señalaría un error real en sizing/fees, no mala suerte.
- **Brier score reutiliza `longshot_brier_report` de la parte 2 tal cual**,
  con la misma cobertura parcial ya documentada (sólo mercados que también
  tuvieron una posición de arb resuelta) — el dashboard lo aclara en texto
  junto al gráfico, no sólo en CLAUDE.md, para que quien lo mire sin
  contexto previo no lo malinterprete como Brier completo de longshot.
- **Todas las consultas del snapshot son agregados SQL** (`COUNT`/`SUM`/
  `AVG`/`GROUP BY` con `LIMIT` en listados), nunca `.all()` sobre las tablas
  completas — mismo cuidado que en los chequeos de salud anteriores. La
  curva de equity sólo itera sobre posiciones *cerradas* (un set acotado por
  construcción, no toda la tabla).
- **Variable nueva en `.env`**: `DASHBOARD_OUTPUT_PATH` (default
  `data/dashboard.html`).

## Deploy (Fase 1) — instancia Oracle Cloud

Detalles completos en [docs/deploy.md](docs/deploy.md); resumen de lo no obvio:

- **La instancia es Oracle Linux 9.7, no Ubuntu** (se asumía Ubuntu al
  planificar). Usuario SSH es **`opc`**, no `ubuntu`. Package manager: `dnf`.
  Hostname heredado del proyecto anterior: `btc-strategy6-demo` (no se
  renombró, es sólo un hostname interno de la VM).
- **Instancia reutilizada**: tenía un bot de trading de Binance Futures
  (`btc-strategy6.service`) corriendo en modo testnet, sin posiciones
  abiertas. Se confirmó con el usuario y se borró por completo (servicio,
  unit file, `/opt/btc_strategy6_bot`, todos los backups en `/home/opc`)
  antes de instalar este proyecto.
- **RAM muy limitada (498 MiB, shape `VM.Standard.E2.1.Micro`)**: `dnf` se
  queda sin memoria y lo mata el OOM killer con la config por defecto. Fix:
  `vm.swappiness=100` (quedó aplicado a nivel runtime, no es persistente
  entre reboots — si hace falta reinstalar algo con `dnf` después de un
  reboot, volver a correr `sudo sysctl -w vm.swappiness=100` primero) +
  deshabilitar repos no esenciales (`ol9_ksplice`, `ol9_UEKR8`,
  `ol9_oci_included`, `ol9_addons`) + `--setopt=install_weak_deps=False
  --setopt=tsflags=nodocs`. El bot en sí no tiene este problema (~50-70 MB
  RSS en producción con 100 mercados/200 assets).
- **Trampa SELinux**: un unit file de systemd copiado vía `/tmp` (scp + mv)
  hereda el contexto `user_tmp_t` y systemd dice "Unit file does not exist"
  aunque el archivo exista — hace falta `restorecon` antes de `enable`.
- **Repo**: se pasó a público en GitHub para simplificar el clone desde el
  VPS (no se configuró deploy key ni PAT). Servicio: `polymarket-bot.service`,
  en `/opt/polymarket-bot`, corre como usuario `opc` (no root), `.env` con
  permisos 600, defaults vacíos de Fase 1 (sin credenciales de trading).

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
