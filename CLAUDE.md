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
  - **Parte 4 (hecha, fuera del plan original de 3 partes)**: exposición del
    dashboard vía nginx en un puerto no estándar con HTTP Basic Auth, para
    verlo sin SCP manual. Ver sección dedicada más abajo.
- **Fase 3 (actual, hecha — pendiente de la primera orden real supervisada)**:
  vivo con capital mínimo real ($20 USDC), acotado deliberadamente a arb
  intra-mercado en mercados deportivos (resolución rápida) — todo lo demás
  sigue en paper trading dentro del mismo proceso. Ejecución real firmada,
  kill-switch manual+automático, wallet con private key cifrada en reposo.
  Ver sección dedicada más abajo.
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
    eligibility.py       # Filtro "mercado de resolución rápida" (sólo deportes) (Fase 3)
    key_management.py    # Cifrado/descifrado Fernet de la private key (Fase 3)
    kill_switch.py        # Kill-switch manual+automático por archivo flag (Fase 3)
    allowances.py          # Verifica/asegura allowance de COLLATERAL antes de operar (Fase 3)
    real_executor.py        # Ejecución real: taker en ambas patas, vía py-clob-client-v2 (Fase 3)
    real_resolution_job.py    # Cierra RealPosition al resolver, mismo patrón que resolution_job.py (Fase 3)
    event_log.py                # Persiste eventos críticos de ejecución real en la DB, no sólo journald (Fase 3)
    reconciliation.py             # Compara balance real vs. real_positions, kill-switch preventivo (Fase 3)
  persistence/
    models.py   # Opportunity + SimulatedPosition (Fase 2 p.1) + SignalResolution (Fase 2 p.2)
                # + RealPosition (Fase 3, capital real -- separada de SimulatedPosition)
    db.py       # Engine/session SQLite
  dashboard/
    snapshot.py  # Agregados SQL -> dataclass Snapshot (Fase 2, parte 3)
    render.py    # Snapshot -> HTML autocontenido (SVG a mano, sin dependencias)
    report.py    # Job puntual: genera y escribe data/dashboard.html a disco
  config.py     # Settings desde .env, URLs de APIs, umbrales de señales
  main.py       # Runner: ingesta + señales + simulador de ejecución + ejecución real (Fase 3, sólo si REAL_TRADING_ENABLED=true)
scripts/
  test_connection.py       # Prueba de solo lectura Gamma+CLOB (Fase 0)
  encrypt_private_key.py    # Setup interactivo: cifra la private key de trading real (Fase 3)
docs/
  setup.md      # Cómo correr el proyecto
  deploy.md     # Incluye el procedimiento de passphrase/cifrado de Fase 3
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

## Fase 2, parte 4 — dashboard vía web (nginx)

Detalle completo de instalación en [docs/deploy.md](docs/deploy.md); resumen
de las decisiones no obvias:

- **nginx, no una alternativa más liviana**: el pedido ya proponía nginx "o
  alternativa igual de liviana" — con 1 worker y sin módulos extra usa ~2MB
  de RSS en esta instancia, un footprint tan chico que buscar algo "más
  liviano todavía" no habría cambiado nada relevante en el presupuesto de
  498MB. No se evaluaron alternativas.
- **Seguridad elegida para esta fase — puerto no estándar (8090) + HTTP
  Basic Auth, sin TLS**: el contenido no es sensible (paper trading, sin
  private key ni credenciales), pero es un servicio nuevo expuesto a
  internet, así que se aplicó protección real igual. Basic Auth es la
  protección efectiva (puerto no estándar sólo reduce ruido de escaneo
  automatizado, no es una barrera). No se implementó TLS: Let's Encrypt
  necesita un dominio propio y la instancia sólo tiene IP pública — para el
  nivel de riesgo de esta fase (nada sensible, uso personal), el costo de
  operar certificados no se justificaba. Queda documentado como decisión
  consciente, no como omisión: si el contenido cambia de naturaleza (datos
  reales, credenciales) esto hay que revisarlo.
- **Nada del filesystem expuesto salvo el archivo exacto**: la config nginx
  no usa `root` de directorio, sólo `alias` al path completo de
  `dashboard.html`; cualquier otro path devuelve 404. La base SQLite vive en
  el mismo directorio y nunca es alcanzable por HTTP — verificado
  explícitamente (`curl .../polybot.db` → 404).
- **Puerto 80 deshabilitado por completo**, no dejado con la página de
  bienvenida por defecto de nginx sin protección — se quitó el server block
  correspondiente de `nginx.conf` en vez de sólo no abrirlo en el firewall,
  para no depender únicamente de esa capa.
- **Dos filtros de red distintos, hay que abrir ambos** (esto es
  específico de Oracle Cloud, no aplica a un VPS genérico): firewalld en el
  SO **y** la Security List/NSG de la VCN en la consola de OCI. Uno sin el
  otro no sirve — si sólo se abre firewalld, el tráfico externo ni siquiera
  llega a la instancia porque la VCN lo bloquea antes.
- **La parte de la Security List de OCI no la puede hacer Claude Code**: es
  un login de cuenta separado (consola web de Oracle Cloud), sin
  credenciales ni acceso configurado en esta instancia (se confirmó que no
  hay CLI `oci` instalado ni policy de instance principal). Esa parte
  siempre requiere que el dueño de la cuenta entre a la consola — Claude
  Code puede preparar todo lo demás (nginx, firewalld, SELinux) y dar los
  pasos exactos, pero no ejecutar ese paso.
- **Dos ajustes de SELinux no obvios** (Enforcing en esta instancia, ninguno
  cubierto por `restorecon` porque son de política, no de contexto de
  archivo):
  - El puerto 8090 no está en la lista `http_port_t` por defecto (sólo 80,
    81, 443, 488, 8008, 8009, 8443, 9000) — nginx fallaba el `bind()` con
    "Permission denied" aunque firewalld ya estuviera bien. Fix:
    `semanage port -a -t http_port_t -p tcp 8090`.
  - El archivo `dashboard.html` hereda el contexto `usr_t` del resto del
    proyecto en `/opt/polymarket-bot/data/` — `httpd_t` no puede leerlo
    hasta reetiquetarlo a `httpd_sys_content_t`
    (`semanage fcontext -a ...` + `restorecon`). Como `report.py` reescribe
    el archivo in-place (mismo inodo, `open(path, "w")` no lo recrea), esta
    regla persiste entre regeneraciones — se aplica una sola vez, no hay que
    repetirla en cada corrida del timer.
- **Contraseña de Basic Auth generada al momento del setup, no versionada**:
  `openssl rand` + `openssl passwd -apr1` (evita instalar `httpd-tools` sólo
  para tener `htpasswd`). Vive únicamente en `/etc/nginx/.htpasswd` en la
  VPS (permisos 640, `root:nginx`) y se comunicó una vez por chat a quien
  hizo el deploy — no está en el repo ni en `.env`.

## Fase 3 — ejecución real con capital mínimo

- **Alcance deliberadamente acotado a un segmento chico y verificado**: sólo
  arb intra-mercado en mercados que califican como "resolución rápida"
  (`execution/eligibility.py::is_fast_resolution_market`). Todo lo demás
  (horizonte largo, longshot, y cualquier mercado no-deportivo) sigue
  exclusivamente en paper trading dentro del mismo proceso — el simulador de
  Fase 2 no se tocó ni se desactivó para nada de eso.
- **Criterio de "resolución rápida" = deportes, vía el campo `sportsMarketType`
  de Gamma**: viene poblado (ej. `"moneyline"`) únicamente en mercados
  deportivos y en ningún otro tipo observado — se agregó como campo directo a
  `MarketInfo` (`sports_market_type`) en vez de inferir por categoría/tags/NLP,
  mismo criterio que ya se usó para `feeSchedule` en Fase 1 (preferir el campo
  que la API ya da en vivo). Es evidencia empírica de las auditorías de Fase 2:
  los mercados deportivos fueron los únicos con resolución consistente <24h;
  los de horizonte largo llevaban aún sin resolver ninguno al momento de
  construir esta fase.
- **Capital real: $20 USDC.** Tope $5 por mercado y $5 por cluster
  (`REAL_MAX_EXPOSURE_PER_MARKET_USD` / `REAL_MAX_EXPOSURE_PER_CLUSTER_USD`).
  `risk/sizing.py::max_capital_for_real_trade` es una función separada de
  `max_capital_for_arb_trade` (Fase 2): usa límites absolutos en dólares, no
  fracciones de un capital base ficticio, y además respeta un techo duro sobre
  la exposición REAL total abierta en todo momento (`REAL_CAPITAL_BASE_USD`),
  algo que Fase 2 no necesitaba porque su "capital" nunca fue real ni finito.
  Sigue sin usar Kelly, mismo argumento que en Fase 2: el arb no tiene
  incertidumbre probabilística que ponderar.
- **Ejecución taker en ambas patas, no maker (decisión explícita del usuario,
  tras plantearle el trade-off)**: un basket de arb necesita que YES y NO
  llenen esencialmente al mismo tiempo. Post-only en ambas patas dejaría leg
  risk real con dinero real (una pata llena, la otra no, antes de que la
  oportunidad desaparezca). Se decidió cruzar el spread de inmediato en las
  dos patas (`OrderType.FOK`, vía `create_and_post_market_order` de
  py-clob-client-v2 — el SDK calcula el precio marketable caminando el book
  internamente, no se construye a mano), reusando la misma lógica de
  profundidad del simulador de Fase 2 (`simulate_arbitrage_fill`, ahora acepta
  un `max_cost` inyectado en vez de sólo calcularlo, para que Fase 3 reuse
  exactamente el mismo camino de código de decisión/sizing que Fase 2 en vez
  de duplicarlo). Con $5 de tope por mercado el fee taker es marginal
  comparado con el riesgo de quedar con una sola pata abierta.
- **Leg risk residual — reconocido, no eliminado**: incluso con ambas patas
  taker, son dos llamadas HTTP separadas; no hay forma de que el exchange las
  ejecute atómicamente. Si la pata YES llena y la pata NO falla o no llena
  (`FOK` no matchea), el sistema trata esto como evento de emergencia:
  dispara el kill-switch automáticamente (no reintenta solo) y persiste la
  posición desbalanceada en `real_positions` con `status="pendiente"` para
  revisión manual (`execution/real_executor.py::_handle_leg_imbalance`). No
  hay lógica de "deshacer" la pata YES (venderla de vuelta) — se consideró
  fuera de alcance de esta fase; la posición desbalanceada queda para que el
  usuario decida qué hacer con ella.
- **Kill-switch manual: un archivo flag en disco**
  (`REAL_KILL_SWITCH_FLAG_PATH`, default `data/REAL_TRADING_HALTED`), no una
  variable de entorno. El proceso corre 24/7 bajo systemd; una env var
  requeriría reiniciar el servicio para cambiarla, y el usuario necesita poder
  detener/reanudar el trading real sin matar el proceso (que seguiría
  haciendo paper trading normal para todo lo demás). Su sola presencia
  bloquea el envío de cualquier orden real, verificado en el primer paso de
  `RealExecutionEngine.maybe_execute` y también antes de construir el motor al
  arrancar (`main.py::_build_real_execution_engine`).
- **Kill-switch automático por drawdown**: si el balance real de USDC cae bajo
  $15 (`REAL_KILL_SWITCH_BALANCE_FLOOR_USD`, 25% de drawdown sobre $20 base),
  se activa escribiendo el mismo archivo flag que el manual — no hay un
  mecanismo separado. Se verifica en un loop propio e independiente
  (`main.py::real_balance_kill_switch_loop`, cada
  `REAL_BALANCE_CHECK_INTERVAL_SECONDS`, default 300s), consultando el
  balance real vía `get_balance_allowance` del cliente CLOB. Una vez que
  dispara, el sistema NO reintenta operar solo — el archivo persiste en disco
  entre reinicios del proceso, así que un reinicio del servicio no reactiva el
  trading real por accidente. Reactivar requiere borrar el archivo a mano
  (ver docs/deploy.md) después de entender la causa.
- **Cifrado de la private key en reposo**: Fernet (`cryptography`) con la
  clave derivada en cada arranque vía PBKDF2-HMAC-SHA256 (600k iteraciones)
  a partir de una passphrase que el usuario genera y gestiona fuera del
  repo/VPS. El salt de derivación no es secreto (viaja en texto plano junto al
  archivo cifrado) — su único rol es que la misma passphrase no produzca
  siempre la misma clave derivada. La passphrase se lee en runtime desde
  `POLYMARKET_KEY_PASSPHRASE` (`REAL_KEY_PASSPHRASE_ENV_VAR`), una variable
  separada del `.env` principal que el usuario exporta a mano antes de
  arrancar el servicio — nunca queda persistida en disco sin cifrar. La key
  descifrada sólo existe en memoria del proceso vivo
  (`execution/key_management.py::load_private_key`); no se loguea ni se
  vuelve a escribir a disco en ningún punto. Procedimiento exacto paso a paso
  (generación de passphrase, cifrado, exportación, rotación) documentado en
  docs/deploy.md — pensado para que el usuario lo ejecute solo, sin depender
  de que se le vuelva a explicar.
- **`scripts/encrypt_private_key.py` es una herramienta de setup, no se invoca
  desde el proceso del bot**: usa `getpass` (la private key y la passphrase
  nunca se pasan como argumento de línea de comandos ni quedan en el
  historial de shell o en `ps`). El usuario la corre a mano una vez (o cada
  vez que rota la key/passphrase).
- **Proxy wallet ("Safe Wallet") y `signature_type` — descubierto durante la
  verificación pre go-live, no en el diseño original**: el primer intento de
  `get_balance_allowance` tras depositar pUSD real seguía devolviendo
  `balance: "0"`. Se rastreó el depósito on-chain (Polygon, lectura pública) y
  se confirmó que el pUSD no queda en la dirección que deriva la private key
  (la EOA firmante, `0x7D7e...fB4d`) sino en una proxy wallet separada que
  Polymarket le asigna a la cuenta (`0x1ed2...D119`, mostrada en la UI de
  Polymarket como "dirección de desarrollador"/dirección de depósito — con la
  advertencia explícita de la propia UI de no mandarle fondos directo, sólo
  vía el flujo de Depósito). `py-clob-client-v2` expone esto con dos
  parámetros del `ClobClient` que el diseño original de Fase 3 no pasaba:
  `funder` (la dirección que de verdad tiene los fondos) y `signature_type`
  (cómo se firma la orden en relación a esa dirección). Sin ellos, el cliente
  asume por defecto `signature_type=EOA` y `funder=<la misma EOA firmante>` —
  válido sólo para cuentas sin proxy wallet.
  - **Qué `signature_type` corresponde a esta cuenta — la documentación
    general no bastó, hubo que verificar contra el balance real**: la
    documentación oficial (docs.polymarket.com/trading/wallets-auth) distingue
    tres tipos de wallet legacy: *Proxy Wallet* — "a legacy smart wallet
    created through Magic Link or Google authentication" (`POLY_PROXY=1`) — y
    *Safe Wallet* — para cuentas que "created [an account wallet] with an
    external signer such as MetaMask or Rabby Wallet" (`POLY_GNOSIS_SAFE=2`).
    Por esa descripción, la hipótesis inicial fue `POLY_GNOSIS_SAFE=2` (esta
    cuenta se conectó con MetaMask). **Se probó y `get_balance_allowance`
    seguía devolviendo `balance: "0"` incluso con `funder` y `signature_type=2`
    ya bien configurados** — la documentación general no reflejaba el estado
    real de esta cuenta. Antes de asumir que el problema era otra cosa, se
    probaron los 4 valores del enum (`SignatureTypeV2`: `EOA=0`, `POLY_PROXY=1`,
    `POLY_GNOSIS_SAFE=2`, `POLY_1271=3`) contra el `ClobClient` real y se
    comparó cada respuesta contra el balance real de pUSD ya verificado
    independientemente on-chain vía RPC ($22.332297 en la proxy wallet,
    confirmado con `eth_call` directo al contrato de colateral). Sólo
    **`POLY_1271=3`** devolvió ese mismo número exacto (`"22332297"`, en
    unidades de 6 decimales) y, además, los 4 allowances ya en
    `2^256-1` (aprobación infinita, ya seteada de antes — no hizo falta
    llamar a `update_balance_allowance`). Los otros tres valores (incluido
    el `2` sugerido por la documentación) dieron balance y allowances en 0.
    Esto no contradice necesariamente la documentación en general — puede
    ser que la implementación específica de proxy wallet de esta cuenta (un
    minimal proxy/clon EIP-1167, confirmado vía `eth_getCode`, que valida
    firmas por EIP-1271 en vez de ser un Gnosis Safe multisig clásico) caiga
    bajo una categoría distinta a la que la documentación describe en
    términos generales para "MetaMask → Safe Wallet". El punto para el
    futuro: **no asumir el `signature_type` sólo por el método de login
    documentado — verificarlo siempre contra el balance real on-chain de la
    cuenta específica**, tal como se hizo acá, antes de dar por buena
    cualquier configuración de Fase 3 en una VPS nueva o con una wallet
    distinta.
  - **El SDK no deriva el funder solo**: `OrderBuilder.__init__` lo documenta
    explícitamente ("Address which holds funds... Used for Polymarket proxy
    wallets and other smart contract wallets") y lo toma como parámetro
    obligatorio si no es la EOA — no hay ninguna función de cómputo
    determinístico (tipo CREATE2) en `py-clob-client-v2`. La dirección tiene
    que salir de la cuenta real del usuario en polymarket.com, nunca
    adivinarse ni derivarse.
  - **Variables nuevas**: `REAL_FUNDER_ADDRESS` (sin default — específica de
    cada cuenta; si no está seteado y `REAL_TRADING_ENABLED=true`, Fase 3 no
    arranca, mismo patrón de gate que el kill-switch) y `REAL_SIGNATURE_TYPE`
    (default `3` = `POLY_1271`, ver arriba por qué). Ambos se pasan al
    construir el `ClobClient` en `main.py::_build_real_execution_engine`; de
    ahí los hereda todo lo demás (`allowances.py`, `real_executor.py`, el loop
    de balance del kill-switch) sin cambios propios, porque todos operan sobre
    la misma instancia de cliente.
- **Allowance de COLLATERAL**: se verifica/asegura al arrancar Fase 3 vía
  `get_balance_allowance` / `update_balance_allowance` de py-clob-client-v2
  (`execution/allowances.py`) — el SDK expone esto como llamada de API, no
  hace falta construir una transacción on-chain manual de `setApprovalForAll`.
  Sólo se gestiona el allowance de COLLATERAL (USDC): la estrategia sólo
  compra y nunca vende antes de la resolución, así que nunca hace falta
  transferir tokens condicionales de vuelta al exchange — no se gestiona
  allowance de CONDITIONAL. Si el allowance sigue en 0 después de intentar
  actualizarlo, Fase 3 no arranca (se loguea CRITICAL y se aborta el setup, no
  se reintenta indefinidamente).
  - **Bug real encontrado y corregido durante la verificación con balance ya
    correcto**: el código original asumía un campo `allowance` (singular) en
    la respuesta de `get_balance_allowance`, pero la forma real es
    `allowances` (plural) — un dict `{contrato: allowance}`, uno por cada
    exchange (v1, v2, neg-risk). Con la cuenta ya mostrando balance correcto
    ($22.33) y los 4 allowances ya en `2^256-1` (aprobación infinita, seteada
    de antes), `ensure_collateral_allowance` seguía devolviendo `False` — el
    `.get("allowance", 0)` nunca encontraba ese campo y siempre leía 0. Fix en
    `execution/allowances.py::_min_allowance`: toma el mínimo entre todos los
    contratos del dict `allowances` (si cualquiera de ellos está en 0, una
    orden que pase por ese exchange fallaría igual). Cubierto con tests en
    `tests/test_execution_allowances.py` (no existían antes de este bug).
- **`REAL_TRADING_ENABLED` (default false) es el interruptor maestro**: incluso
  con todo el resto del setup completo (key descifrada, allowances OK,
  kill-switch en su lugar), si esta variable no está en `true` el motor de
  ejecución real ni se construye — el proceso sigue funcionando exactamente
  como en Fase 1/2. Se prendió a mano después de que el usuario confirmó ver
  el setup completo (allowances, kill-switch probado, cifrado de clave
  validado) — no se activó de forma automática la primera vez, tal como se
  pidió explícitamente.
- **`RealPosition` (tabla `real_positions`) separada de `SimulatedPosition`**:
  mismo nivel de detalle (costo, fee, P&L esperado, timestamps) más lo que
  sólo existe para una orden real — `yes_order_id`/`no_order_id` y
  `yes_tx_hash`/`no_tx_hash` de cada pata. `realized_pnl` queda sin usar por
  ahora (nunca se marca "cerrada" automáticamente) — el job de resolución de
  Fase 2 (`resolution_job.py`) sigue operando exclusivamente sobre
  `SimulatedPosition`; extenderlo a `RealPosition` es trabajo pendiente, no
  pedido en esta sesión (mismo principio de "no adelantar fases/partes no
  pedidas" que ya se siguió en Fase 2).
- **Verificación de fill vía `transactionsHashes` en la respuesta de
  `post_order`/`create_and_post_market_order`**: heurística conservadora
  (`execution/real_executor.py::_order_filled`) documentada como pendiente de
  validar contra la respuesta real de la API la primera vez que se opere de
  verdad — no había forma de confirmarla sin credenciales de trading en esta
  sesión. Si el formato real difiere, es lo primero a ajustar durante el setup
  supervisado antes de la primera orden.
- **Latencia aceptada, no resuelta**: la ejecución real (llamadas HTTP
  síncronas de py-clob-client-v2) corre dentro del mismo callback síncrono
  `SignalEngine._check_arbitrage` que ya hacía las escrituras SQLite de Fase
  1/2 — no se convirtió a async como sí se hizo con el job de resolución de
  Fase 2 parte 2 (que si necesitaba `httpx.AsyncClient` para no bloquear el
  heartbeat del WS). El heartbeat del WS corre en una tarea separada y no se
  ve afectado, pero el procesamiento de mensajes de book entrantes sí puede
  demorarse brevemente (típicamente sub-segundo) mientras se envía una orden
  real. Se aceptó este costo en vez de reestructurar Fase 1/2 a async, dado el
  volumen bajo esperado de trades reales elegibles (sólo deportes, sólo con
  arb detectado) y que los topes de capital ($5/mercado) ya acotan el impacto
  de cualquier decisión tomada con datos levemente desactualizados.
- **Variables nuevas en `.env`**: `REAL_TRADING_ENABLED` (false),
  `REAL_CAPITAL_BASE_USD` (20.0), `REAL_MAX_EXPOSURE_PER_MARKET_USD` (5.0),
  `REAL_MAX_EXPOSURE_PER_CLUSTER_USD` (5.0),
  `REAL_KILL_SWITCH_BALANCE_FLOOR_USD` (15.0), `REAL_KILL_SWITCH_FLAG_PATH`
  (`data/REAL_TRADING_HALTED`), `REAL_ENCRYPTED_KEY_PATH`
  (`data/private_key.enc`), `REAL_KEY_PASSPHRASE_ENV_VAR`
  (`POLYMARKET_KEY_PASSPHRASE`), `REAL_BALANCE_CHECK_INTERVAL_SECONDS` (300),
  `REAL_FUNDER_ADDRESS` (sin default, específica de la cuenta),
  `REAL_SIGNATURE_TYPE` (3 = `POLY_1271`, verificado empíricamente, ver arriba).

## Incidente del 2026-09-08 y correcciones (Fase 3)

Primera activación real (`REAL_TRADING_ENABLED=true` el 2026-09-08 ~01:49
UTC). Una auditoría manual el mismo día encontró que el bot había ejecutado
capital real sin dejar ningún rastro en `real_positions`. Post-mortem
completo, causa raíz, y las 4 correcciones estructurales aplicadas antes de
considerar una segunda activación.

**Qué pasó**: entre las 11:43 y las 18:07 el bot detectó y ejecutó 4 arbs
reales en mercados deportivos elegibles (FC Seoul, Club Brugge KV, Aston
Villa FC, AEK). En los 4 casos la orden YES llenó de verdad on-chain (capital
real gastado, $10.876 en total), pero `_order_filled()` (la función que
decidía si la orden había llenado) sólo miraba el campo `transactionsHashes`
de la respuesta de `post_order` -- y ese campo se resuelve "best-effort"
(documentado en el propio docstring del SDK: *"si el hash no está disponible
todavía, el fill se puede seguir vía `tradeIDs`"*) y llegó vacío en los 4
casos aunque el fill sí había ocurrido. El código concluyó "no llenó",
abandonó sin comprar la pata NO, y -- porque el registro en `real_positions`
sólo se escribía al final del flujo completo, no al enviar la orden -- no
quedó ningún rastro en la base. El balance real cayó a $11.46 (bajo el piso
de $15), el kill-switch automático se activó correctamente por drawdown a
las 18:10:08 y detuvo todo trading real -- ese mecanismo sí funcionó como
estaba diseñado. Se descubrió recién reconstruyendo los movimientos on-chain
de la proxy wallet a mano (eventos `Transfer` de pUSD, cruzados contra los
`opportunities`/`simulated_positions` ya logueados en los mismos instantes).
2 de las 4 posiciones resolvieron YES (ganancia) y 2 NO (pérdida) -- el
resultado neto terminó siendo positivo por suerte (2 de 4 ganaron), no
porque el arb haya funcionado como estaba diseñado: fueron apuestas
direccionales reales sin cobertura, no arbitraje.

### 1. Detección de fill ya no confía en un único campo

`execution/real_executor.py::_is_order_filled` reemplaza a la vieja
`_order_filled`. Señales, de más a menos directa:
1. **`transactionsHashes` o `tradeIDs`** en la respuesta inicial de
   `post_order`/`create_and_post_market_order` (`_order_matched`) -- ambas
   son evidencia de que la orden matcheó; `tradeIDs` es justo la señal que el
   bug anterior ignoraba.
2. Si ninguna de las dos aparece pero sí hay un `orderID`, se hace una
   **consulta explícita de estado** (`_confirm_via_order_status`, vía
   `client.get_order(order_id)`), buscando `size_matched`/`sizeMatched` > 0 o
   un `status` reconocible (`MATCHED`/`FILLED` vs. `UNMATCHED`/`LIVE`/
   `CANCELED`). La forma exacta de esta respuesta no se pudo confirmar contra
   la API real en esta sesión (no había una orden real en vuelo para
   inspeccionar) -- queda pendiente de validar/ajustar durante el próximo
   setup supervisado, igual que ya se documentó para `_order_filled` original.
3. **Si esa consulta es inconclusa** (la llamada falla, o la respuesta no
   trae ninguna señal reconocible), se asume **LLENADA, no al revés**. Esta
   es la corrección central: la lección del incidente fue exactamente el
   error opuesto -- tratar una ambigüedad como "no llenó" dejó una orden real
   con capital gastado sin registrar en ningún lado. Sin `orderID` en
   absoluto (nunca se registró ni un intento de orden) sí se concluye con
   confianza que no llenó -- ahí no hay ambigüedad que resolver a favor de la
   cautela.

Cubierto en `tests/test_execution_real_executor.py`:
`test_tradeids_without_hashes_is_treated_as_filled` reproduce exactamente el
bug (tradeIDs sin hashes) y confirma que ahora sigue con la pata NO;
`test_ambiguous_order_status_defaults_to_filled_not_abandoned` cubre el caso
totalmente inconcluso; `test_confirmed_not_filled_via_get_order_is_cancelled_not_abandoned`
confirma que un "no matcheó" genuino sigue dejando registro (status
"cancelada") en vez de desaparecer sin rastro.

### 2. Registro inmediato al enviar, no al confirmar

Cada intento de orden real ahora crea (o actualiza) una fila de
`real_positions` **antes** de llamar a `create_and_post_market_order`, con
`status="enviada"`, usando los valores estimados del fill (shares, precios,
costo) como mejor aproximación disponible en ese momento. Esa misma fila se
actualiza según lo que pase después: `"cancelada"` (YES confirmado sin
llenar, nada de capital tocado), `"pendiente"` (falla de envío sin poder
confirmar el resultado, o leg imbalance -- requiere revisión manual) o
`"abierta"` (ambas patas confirmadas). El objetivo explícito es que una orden
real nunca vuelva a quedar invisible, sea cual sea la ambigüedad de la
respuesta del exchange -- ni siquiera si la llamada de red explota a mitad de
camino (`test_position_is_persisted_immediately_before_fill_is_known`).

`RealPosition.status` ahora tiene 5 valores posibles (antes 3): `"enviada"`
(default), `"cancelada"`, `"abierta"`, `"cerrada"`, `"pendiente"`. Se agregó
también `RealPosition.notes` (texto libre) para dejar contexto legible sobre
qué pasó en los casos ambiguos o reconstruidos a mano.

### 3. Logging resiliente -- persistido en la DB, no sólo journald

journald en la VPS retiene apenas ~8.8MB y rotó por completo las ~19h que
cubrían el incidente antes de que se pudiera auditar -- ni siquiera el
mensaje `FASE 3 ACTIVA` del arranque sobrevivió. Nueva tabla
`real_execution_events` (`persistence/models.py::RealExecutionEvent`):
`event_type`, `severity` ("info"/"warning"/"critical"), `message`,
`market_id`, `real_position_id`, `detail` (JSON). Único punto de entrada:
`execution/event_log.py::log_event(session, event_type, severity, message,
...)` -- siempre hace las dos cosas, loguea vía el logger de Python de
siempre (journald sigue sirviendo para tail en vivo) y persiste la fila.
`kill_switch.halt()` ahora acepta un `session` opcional y persiste el evento
`kill_switch_triggered` cuando se le pasa uno (retrocompatible: sin
`session`, se comporta exactamente igual que antes -- sólo flag + logger).
Cubierto en `tests/test_execution_real_executor.py::test_execution_events_are_persisted_to_db`
y `tests/test_execution_kill_switch.py::test_halt_with_session_persists_event`.

### 4. Reconciliación automática de balance

`execution/reconciliation.py`, corre dentro del mismo loop que ya consultaba
el balance real cada `REAL_BALANCE_CHECK_INTERVAL_SECONDS` (300s,
`main.py::real_balance_kill_switch_loop` -- se le agregó esto en vez de crear
un loop nuevo, reusando la misma llamada HTTP ya pagada).
`expected_balance_usd(session)` = un punto de referencia menos el `cost_usd`
de toda posición en `"enviada"`/`"abierta"`/`"pendiente"` (capital que salió
de la wallet y no hay certeza de que haya vuelto) más el `realized_pnl` de
las posiciones `"cerrada"`. Si el balance real diverge de eso por más de
`REAL_RECONCILIATION_THRESHOLD_USD` (default $0.50, para tolerar
fees/redondeo), se loguea CRITICAL (persistido, evento
`reconciliation_divergence`) y se activa el kill-switch preventivamente --
exactamente la señal que faltó para detectar el incidente del 2026-09-08 sin
depender de una auditoría manual. No distingue automáticamente "algo quedó
sin registrar" de "hubo actividad manual en la cuenta fuera del bot" (ambas
producen la misma divergencia) -- en cualquier caso, detener el trading real
hasta que alguien lo entienda es el comportamiento correcto.

- **El punto de referencia por defecto (`REAL_CAPITAL_BASE_USD`, sin filtro de
  fecha) generó una divergencia falsa al verificar esta misma corrección**:
  con las 4 posiciones del incidente ya backfilleadas como `"cerrada"`, el
  balance real confirmado era $22.727494 pero `expected_balance_usd` daba
  $19.55 -- una diferencia de $3.18, muy por encima del umbral. La causa: el
  capital base nominal ($20) nunca fue el balance real de arranque (el
  depósito real fue $22.33 antes de que ocurriera nada del incidente), así
  que restar/sumar posiciones contra ese número nominal no podía coincidir
  con la realidad. Fix: `REAL_BALANCE_CHECKPOINT_USD` +
  `REAL_BALANCE_CHECKPOINT_AT` (van siempre juntos) fijan el balance real
  CONFIRMADO en una fecha conocida como referencia, y `expected_balance_usd`
  sólo suma/resta posiciones con `opened_at >= checkpoint_at` -- las
  anteriores al checkpoint ya están reflejadas en ese balance observado,
  sumarlas de nuevo las contaría dos veces. Tras el backfill de este
  incidente se fijó el checkpoint al balance confirmado post-backfill
  ($22.727494, 2026-09-08 ~22:10 UTC) -- sin ninguna posición pendiente en
  ese momento, es un punto limpio para arrancar la reconciliación de cero.
  Sin el par de checkpoint seteado, se mantiene el comportamiento nominal
  (capital base, sin filtro de fecha) como fallback simple.

### Backfill retroactivo

Las 4 posiciones del incidente se registraron a mano en `real_positions`
(`scripts/backfill_incident_2026_09_08.py`, ya ejecutado en la VPS, se deja
en el repo como referencia/auditoría, no pensado para volver a correrse).
`cost_usd` es exacto desde el principio (tomado directo de los eventos
`Transfer` de pUSD on-chain). `yes_price_avg`/`shares` se estimaron
inicialmente contra el `simulated_position` más cercano en el tiempo, y se
**corrigieron a los valores exactos** una vez identificados los
`taker_order_id` reales durante la validación de `_confirm_via_trades`
(punto 1 de abajo): `get_trades(maker_address=<funder>)` expuso `price`/`size`
directo del exchange para las 4. Cambio material en 2 de las 4 -- Aston Villa
FC pasó de 0.56/5.25 estimado a **0.48/6.125 real** (`realized_pnl` de
$2.2336 a $3.1086) y AEK de 5.174 a **5.146063** shares (`realized_pnl` de
$0.5692 a $0.5409); FC Seoul y Club Brugge KV cambiaron de precio/shares pero
su `realized_pnl` no varió (pérdida total, no depende del tamaño exacto).
`cost_usd` no se tocó: sigue siendo el monto exacto transferido on-chain,
que no coincide exactamente con `price × size` del trade (la diferencia,
un par de centavos por posición, es probablemente fee de esa pata --
no se pudo reconciliar con certeza y no afecta el balance real observado,
que es lo que finalmente importa). Cada fila registra el cambio en `notes`.

### Punto 1 validado contra el servidor real -- `_confirm_via_order_status` no servía, `_confirm_via_trades` sí

Con las 4 órdenes reales del incidente ya identificables (`get_trades(maker_address=<funder>)`
trae sus 4 trades, cada uno con `taker_order_id`), se pudo probar
`client.get_order(taker_order_id)` contra el servidor real por primera vez:
**devolvió `None` en las 4**, no una excepción, no un dict con
`size_matched`/`status` -- directamente `None`. La hipótesis original (que
`get_order` traería un status reconocible) no se sostuvo: para una orden de
mercado FOK ya ejecutada y liquidada, `get_order` parece servir sólo
órdenes resting/abiertas, no el registro post-hoc de una ya completada.

La señal que sí funcionó: **`get_trades(asset_id=token_id)`**, filtrando
client-side por `taker_order_id == order_id` -- las 4 aparecieron ahí con
`status: "CONFIRMED"`. Se agregó `_confirm_via_trades` como el fallback real
(antes de `_confirm_via_order_status`, que se deja como intento adicional de
bajo costo aunque no aportó nada en la práctica) en `_is_order_filled`. La
única lectura negativa reconocida es `status == "FAILED"` (la constante que
el propio SDK expone, `constants.FAILED_TRADE_STATUS`) -- cualquier otro
status (`CONFIRMED`, `MATCHED`, `MINED`, `RETRYING`, ...) se lee como "el
trade existe, matcheó", aunque su liquidación on-chain siga en curso.
Cubierto en `tests/test_execution_real_executor.py`:
`test_confirmed_filled_via_get_trades_matches_real_incident_scenario`
reproduce exactamente lo observado (get_order inútil, get_trades con
`CONFIRMED`); `test_confirmed_not_filled_via_get_trades_is_cancelled_not_abandoned`
cubre la única lectura negativa (`FAILED`).

Con esto, la cadena de señales de `_is_order_filled` queda: (1)
`transactionsHashes`/`tradeIDs` en la respuesta inicial, (2) `get_trades`
filtrado por `taker_order_id` (validado contra el servidor real), (3)
`get_order` (no validado como útil, pero inofensivo dejarlo), (4) si todo lo
anterior es inconcluso, asumir llenada -- la corrección central del
incidente, ya no depende de que (2)/(3) funcionen para seguir siendo segura.

### Primera ejecución real post-fix (2026-09-08 23:30 UTC) — éxito completo

Con las 4 correcciones desplegadas, se reactivó `REAL_TRADING_ENABLED` y la
primera oportunidad real detectada ("Will Independiente Santa Fe win on
2026-09-08?") se ejecutó de punta a punta correctamente: `EJECUCIÓN REAL` →
`Orden YES real llenó... enviando pata NO` (confirmado vía `_confirm_via_trades`,
no `transactionsHashes` -- validando la corrección #1 en un caso real, no sólo
en el test) → `Posición real abierta | cost_usd=5.00`. Ambas patas cubiertas,
tope de $5 respetado, todo registrado en `real_positions` +
`real_execution_events`. A diferencia del incidente original, esta vez el
sistema hizo exactamente lo que se diseñó para hacer.

### Divergencia de reconciliación del 2026-09-09 — causa benigna, hueco real cerrado

~2.5h después de abrirse, la posición de Independiente Santa Fe resolvió y se
redimió on-chain (confirmado con certeza vía
`ConditionalTokens.payoutDenominator`/`payoutNumerators` -- ganó "No" -- y
`balanceOf` en 0 para ambos tokens condicionales en la proxy wallet), pero
`RealPosition` no tenía ningún job que se enterara de esto y la siguiera
marcando `"abierta"`. La reconciliación automática (ya anda cada 5 min, ver
arriba) comparó el balance real (ya de vuelta en ~$22.73) contra lo que
`real_positions` todavía decía que estaba comprometido (~$17.73, con los $5
de esa posición contados como "afuera") y disparó el kill-switch
correctamente -- la causa de fondo era benigna (dinero real que ya había
vuelto), pero el sistema no tenía forma de saberlo sin este job, así que
detener el trading real ante la duda fue el comportamiento correcto.

- **Nuevo job: `execution/real_resolution_job.py::resolve_open_real_positions`**,
  mismo patrón que `resolution_job.py` para `SimulatedPosition` (Fase 2, parte
  2) -- mismo `fetch_market_resolution()` de CLOB, mismo `main.py::resolution_loop`
  (se le agregó esta llamada, no un loop nuevo). Dos tipos de payout al
  resolver, según el estado de la posición:
  - `status="abierta"` (ambas patas llenaron -- basket completo): payout
    garantizado de $1/share sin importar el resultado, `realized =
    shares - cost_usd - fee_paid` -- idéntico a `SimulatedPosition`.
  - `status="pendiente"` (leg imbalance -- sólo YES tiene capital real):
    el payout depende del resultado real: `shares - cost_usd` si ganó YES,
    `-cost_usd` (pérdida total) si no.
  No hace verificación on-chain propia (mismo límite ya documentado para
  `SimulatedPosition`, "The Ghosts of Polymarket") -- para la posición de
  Santa Fe específicamente, el CLOB REST (`closed`) todavía no reflejaba la
  resolución al momento de escribir esto pese a que el oráculo on-chain ya
  la había resuelto -- se cerró esa fila a mano con los valores confirmados
  on-chain (ver abajo), no vía el job automático. El job seguirá sirviendo
  para las próximas posiciones una vez que CLOB REST se ponga al día, que es
  la fuente que ya se usa en todo el resto del proyecto.
- **La reconciliación (`reconciliation.py::expected_balance_usd`) ya excluía
  correctamente las posiciones `"cerrada"` del "comprometido"** (sólo cuenta
  `"enviada"`/`"abierta"`/`"pendiente"`, y suma `realized_pnl` de las
  `"cerrada"` por separado) -- no hizo falta ningún cambio ahí, sólo que
  `RealPosition` tuviera un job que efectivamente marcara `"cerrada"` cuando
  correspondía. Confirmado con un test dedicado
  (`test_resolved_position_via_job_does_not_cause_false_divergence`).
- **La posición de Santa Fe se cerró manualmente** (no vía el job, por el
  desfasaje de CLOB REST explicado arriba) usando certeza on-chain, no
  inferencia: `payoutDenominator=1` (condición resuelta), `payoutNumerators=[0,1]`
  (ganó "No"), balance CTF de ambos tokens en 0 (ya redimido). `realized_pnl`
  se calculó con el flujo de caja real on-chain (-$0.0024: salieron $5.0813 al
  abrir, entraron $5.0789 al redimir) en vez de la fórmula
  `shares-cost_usd-fee_paid` (que hubiera dado +$0.0594) -- el costo real de
  ejecución no coincidió exactamente con la estimación pre-trade. Verificado:
  con esta fila en `"cerrada"`, `expected_balance_usd` da $22.725094 contra un
  balance real on-chain de $22.725191 -- la divergencia desaparece sin tocar
  el checkpoint, exactamente como se pidió confirmar.
- **Variables**: ninguna nueva -- el job reusa `RESOLUTION_CHECK_INTERVAL_SECONDS`
  ya existente.

### Tercera activación (2026-09-09) — 2 trades reales correctos, luego falso positivo del kill-switch de drawdown

Con las 4 correcciones y el job de resolución de `RealPosition` ya en
producción, la 3ra activación ejecutó 2 arbs reales de punta a punta sin
ningún problema: HJK Helsinki (`realized_pnl=+$0.1499`) y FC Barcelona vs.
Feyenoord BTTS (`realized_pnl=+$0.0269`), ambos cerrados automáticamente por
`real_resolution_job.py` **sin intervención manual** -- a diferencia del caso
Santa Fe (2026-09-08), que sí había necesitado un cierre a mano. Es la
primera confirmación de que esa corrección funciona sola en producción, no
sólo en tests.

Sin embargo, ~1 minuto después de abrir la segunda posición (2 posiciones
reales de $5 abiertas a la vez), el kill-switch automático de drawdown se
disparó: `balance real $12.54 por debajo del piso $15.00`. El trading real
quedó detenido **7.5 horas sin causa real** hasta que una auditoría (pedida
explícitamente en modo solo-lectura) lo detectó.

**Causa raíz -- confusión entre "balance líquido" y "equity"**:
`check_balance_kill_switch` comparaba el balance líquido crudo de la wallet
contra el piso de $15, pero ese balance baja por diseño cada vez que hay una
posición real abierta (el capital sigue existiendo, sólo está temporalmente
fuera de la wallet hasta que la posición resuelve) -- no es una pérdida.
Con capital real de ~$22.33 y tope de $5/mercado, bastaba con 2 posiciones
concurrentes ($10 comprometidos) para cruzar el piso de $15 sin ninguna
pérdida real. La reconciliación (que si sabe distinguir "comprometido" de
"perdido", ver `expected_balance_usd`) no se vio afectada por este bug -- de
hecho generó 2 divergencias transitorias por el mismo motivo de siempre
(lag entre resolución on-chain y CLOB REST) que se autoresolvieron solas
cuando el job de resolución cerró ambas posiciones, sin necesitar el fix de
abajo.

**Fix -- el piso de drawdown ahora evalúa equity, no balance líquido**:
`kill_switch.py::check_balance_kill_switch` calcula
`equity = balance_líquido + committed_capital_usd(session)`, donde
`committed_capital_usd` suma `cost_usd` de todas las `RealPosition` en
`"enviada"`/`"abierta"`/`"pendiente"` -- y compara *eso* contra el piso. La
función ahora requiere `session` (antes era opcional; el único caller en
producción, `main.py::real_balance_kill_switch_loop`, ya lo pasaba siempre).
Cubierto con
`tests/test_execution_kill_switch.py::test_two_concurrent_open_positions_do_not_trigger_false_positive`
(reproduce exactamente el escenario del 2026-09-09: balance líquido $12.54 +
2×$5 comprometido = equity $22.54, no dispara) y
`test_real_drawdown_with_open_positions_still_triggers` (una pérdida real
genuina con posiciones abiertas sigue disparando: balance $8.00 + $5.00
comprometido = equity $13.00, bajo el piso).

**Piso de drawdown vs. reconciliación -- documentado como dos mecanismos
separados** (ver también el docstring de `kill_switch.py`): el piso evalúa
"¿se perdió plata de verdad?" (equity vs. un umbral fijo); la reconciliación
evalúa "¿el balance real coincide con lo que la DB dice que debería haber?"
(cualquier divergencia, no sólo hacia abajo). Comparten el mismo archivo
flag para detener el trading, pero no se fusionan en una sola función --
resuelven preguntas distintas y mezclarlas habría ocultado cuál de las dos
disparó en cada caso.

### Pendiente para la próxima activación

- Repetir el checklist de verificación pre-go-live completo (los mismos 7
  puntos de la primera vez) antes de volver a pedir luz verde -- no se activa
  `REAL_TRADING_ENABLED` de nuevo sin ese chequeo ni sin confirmación
  explícita del usuario. Esta sería la cuarta activación.

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
