# Informe técnico: Construcción de un bot de trading para Polymarket (2026)

## TL;DR
- **Es técnicamente viable construir el bot con Claude Code** usando la API oficial de Polymarket (Gamma + CLOB + Data + WebSockets) y el SDK `py-clob-client`, pero la evidencia académica y on-chain de 2024–2026 es contundente: un estudio de investigadores de la Universidad de Toronto, HEC Montréal y ESSEC Business School (2,4 millones de usuarios, USD 67 mil millones de volumen) halló que **el 68,8% de los usuarios perdió dinero desde 2022, mientras el 1% superior capturó el 76,5% de todas las ganancias** y el 0,1% superior concentró más de la mitad de los beneficios de la plataforma. El edge real para un operador con capital pequeño NO está en la predicción sino en la estructura: arbitraje intra-mercado (YES+NO≠$1), provisión de liquidez como maker (0% de comisión + rebate) y sesgo favorito-longshot.
- **Chile no está en la lista de países restringidos de Polymarket** y no bloquea IPs chilenas (a diferencia de Argentina y Brasil, bloqueados por resoluciones judiciales/regulatorias en marzo–abril de 2026). El trading cripto es legal bajo la Ley Fintech 21.521, pero los mercados de predicción operan en un área gris no regulada. El riesgo regulatorio es principalmente de plataforma/mercado, no personal.
- **Expectativa realista: rentabilidad baja pero positiva es posible sólo con disciplina de arbitraje/market-making y Kelly fraccionado (¼ o menos)**. El market-making documenta 0,5–2% mensual con drawdown <1%; las estrategias direccionales/momentum prometen más pero con drawdowns de -20% en días. Empezar con paper trading y capital que se pueda perder por completo.

## Key Findings

### 1. Cómo funciona Polymarket
- Polymarket es un mercado de predicción no-custodial sobre Polygon PoS. Usa un **CLOB híbrido**: emparejamiento de órdenes off-chain (rápido) con liquidación atómica on-chain vía un contrato Exchange. Las órdenes son mensajes firmados EIP-712; el usuario nunca cede la custodia de los fondos. El *maker* es la wallet que posee el colateral; el *signer* es la clave que firma (pueden ser distintas mediante delegación).
- Los resultados se tokenizan con el **Conditional Token Framework (CTF)** de Gnosis: cada mercado binario tiene un token YES y uno NO (ERC-1155). Un par YES+NO siempre está respaldado por exactamente $1.00 y en equilibrio YES + NO = $1. Para recuperar colateral antes de la resolución hay dos vías: *merge* de sets completos o *redeem* tras resolución.
- **Tipos de mercado**: binarios (YES/NO), multi-outcome negRisk (mutuamente excluyentes, sólo uno resuelve YES, suman ~$1; ej. ganador de elección), multi-outcome no-negRisk (varios pueden resolver YES; ej. "¿con quién se reunirá Trump?"), y escalares/direccionales (umbrales up/down y bracketed por rangos, típicamente negRisk cuando sólo un rango puede contener el valor final). Los negRisk permiten "convertir" (vía NegRiskAdapter) un NO en YES de todos los demás outcomes + USDC.
- **Resolución vía UMA Optimistic Oracle**: alguien propone un resultado con un bono (históricamente ~$750 en USDC.e); hay ventana de disputa de ~2 horas; si no se disputa, resuelve y el proponente recupera el bono más una recompensa. Disputas escalan al Data Verification Mechanism (DVM) de UMA y tardan 4–6 días. Polymarket también usa Chainlink para mercados de precios cripto en tiempo real, y su Markets Team redacta las reglas. Los ganadores redimen a $1.00 por share; también se puede vender a ~$0,999 para liquidez casi instantánea.
- **Migración CLOB V2 (28 de abril de 2026)**: cambió aproximadamente la mitad de los campos del order-struct y el colateral pasó de USDC.e a **pUSD** (respaldado por USDC). Los mercados multi-outcome negRisk liquidan por un contrato NegRisk CTF Exchange separado. Tutoriales anteriores a abril de 2026 quedan obsoletos.

### 2. Comisiones (cambio importante de 2026)
- Polymarket dejó de ser gratis en 2026. Las comisiones son **por categoría al taker**, máximas cerca de 50¢ (máxima incertidumbre) y decrecientes simétricamente hacia 1¢/99¢. Según la fórmula documentada, para crypto: `fee = shares × 0.07 × price × (1 − price)`, con un máximo de $1,75 por 100 shares en un mercado a 50¢. Tasas por categoría reportadas (marzo 2026): crypto ~1,8%, economía ~1,5%, cultura/clima ~1,25%, política/finanzas/tech ~1,0%, deportes ~0,75%. **Geopolítica/world events: 0% (fee-free)**.
- **Makers pagan 0% y reciben 15–25% de las comisiones taker como rebate diario en pUSD** (Programa de Maker Rebates). Un maker que llena a mid, sin capturar spread, queda neto positivo sólo por el rebate. Esto es estructuralmente favorable para market-making y arbitraje con órdenes límite.
- Costos totales: un round-trip con órdenes market a mid corre ~8–10% all-in; con órdenes límite como maker baja a ~2–4%. Gas en Polygon: ~$0,01–0,05 por transacción (pagado en POL). Retiros/bridge de USDC: $5–30. Depósitos con tarjeta: 3,5–4,5% (evitar; usar USDC vía red Polygon). Los depósitos/retiros de pUSD no tienen comisión directa.

### 3. Dónde hay edge documentado
Síntesis de literatura académica (20+ papers, 2006–2026) y análisis on-chain:

- **Arbitraje intra-mercado en Polymarket** (Saguillo, Ghafouri, Kiffer & Suarez-Tangil, IMDEA Networks Institute, 2025; arXiv:2508.03474): cuando YES+NO se desvía de $1. Es la estrategia con mayor respaldo empírico específico a Polymarket (detalle cuantitativo en la sección Details). Riesgo-libre en concepto.
- **Sesgo favorito-longshot**: los longshots (precios bajos) están sistemáticamente sobrevalorados y los favoritos (precios altos) infravalorados. Edge 2–5%, "confianza muy alta", replicado en décadas de estudios (Buhagiar/Cortis/Newall 2018; Ottaviani/Sorensen 2009; Franck/Verbeek/Nuesch 2010). Se explota comprando NO en longshots caros ($0,05–$0,15) y YES en favoritos baratos ($0,75–$0,92). El paper de microestructura de Dubach (2026) confirma un "longshot spread premium": el spread se ensancha en los outcomes de baja probabilidad.
- **Provisión de liquidez / underwriting** (Palumbo 2026, sobre Kalshi NFL): los LP pasivos terminan con exposición direccional terminal y cobran por asumirla; agregado positivo (~$29M en una temporada NFL) pero con drawdowns semanales fuertes. Conceptualmente, proveer liquidez en event contracts es *underwriting*, no market-making clásico.
- **Hedge underpricing** (3–15% en posiciones pareadas), **dynamic hedging** (lock-in de ganancia por movimiento de odds, sin vista sobre el outcome), **flujo de informados cerca de resolución** (movimientos >5pp en la última hora predicen el resultado), y **horizonte largo** (los mercados baten encuestas el 74% de las veces, con ventaja máxima >100 días antes de una elección).
- **Arbitraje cross-market/cross-platform** (Polymarket vs Kalshi): 1–5% por oportunidad, pero las comisiones combinadas (~5%: Polymarket ~2% sobre ganancias + Kalshi hasta 3% taker) matan spreads <6%. La condición de rentabilidad tras fees escala con 1/(1−τ)² por leg.

### 4. Realidad de rentabilidad (evitar promesas irreales)
Múltiples análisis independientes convergen en un panorama de alta concentración:
- Estudio académico (Toronto/HEC Montréal/ESSEC, 2,4M usuarios, $67B volumen): **68,8% de usuarios perdió dinero desde 2022; el top 1% capturó el 76,5% de las ganancias; el top 0,1% concentró más de la mitad de los beneficios totales**.
- Análisis on-chain de 95 millones de transacciones (abril 2024–diciembre 2025): sólo el **0,51% de las wallets** logró beneficios >$1.000.
- Análisis de DeFi Oasis (dic 2025, 1,73M direcciones): ~70% perdió dinero; el top 0,04% capturó el 70% de $3.700 millones en beneficios. Metodología posterior de Andrey Sergeenkov (The Defiant, datos a abril 2026, 2,5M wallets) que captura splits/merges eleva la tasa de pérdida al **84,1%** (menos del 16% de direcciones con retorno positivo).
- Bloomberg (wallets activas desde 2025): **aproximadamente el 5% de wallets tipo bot generó el 75% del volumen; 823 wallets netearon más de $100.000 cada una**. El profesor Joshua Della Vedova (University of San Diego) señaló que los retail "eligieron el resultado ganador más a menudo que los bots. Perdieron igual porque entraron en posiciones más tarde, a peores precios."
- La ventana de arbitraje retail colapsó de ~12,3s (2024) a ~2,7s (2026); el 73% es capturado por bots sub-100ms.

**Conclusión estratégica**: el edge de un bot retail nuevo NO es la velocidad (ya perdida ante bots profesionales) ni la mejor predicción, sino disciplina estructural: market-making paciente con rebate, arbitraje en mercados nuevos/ilíquidos que los bots grandes no priorizan, y explotación sistemática del sesgo favorito-longshot con posiciones pequeñas y numerosas.

## Details

### El paper de arbitraje (evidencia primaria)
Saguillo et al. (IMDEA Networks, arXiv:2508.03474) analizaron **86.620.143 transacciones on-chain** sobre mercados resueltos entre el **1 de abril de 2024 y el 1 de abril de 2025**:
- **Beneficio total extraído por arbitraje: ~$40 millones** (cifra precisa del paper: $39.587.585,02, bajo el supuesto de un umbral ε = $1 de ganancia por trade). La cuenta individual más rentable extrajo **$2.009.631,76 en 4.049 transacciones** (~$496 por transacción). Un solo trade destacado ganó $58.983,36 comprando YES y NO a <$0,02 cada uno.
- **Dataset**: 8.659 mercados de condición única y 1.578 mercados negRisk (8.559 condiciones), totalizando **17.218 condiciones**.
- **Prevalencia**: 7.051 condiciones tuvieron al menos una oportunidad de arbitraje (dentro de sus parámetros, umbral ≥$0,05 por dólar). De los 1.578 mercados negRisk, 662 tuvieron al menos una oportunidad.
- **Magnitud por dólar**: la mediana de la suma YES+NO fue ~$0,60 en todos los temas (es decir, ~$0,40 de ganancia potencial por dólar en esa métrica). En negRisk, la oportunidad máxima promedio fue ~40 centavos por dólar.
- **Por categoría** (tensión clave): **Sports** tiene la mayor *frecuencia* de oportunidades pero está sub-explotado en mercados multi-condición; **Politics** (elección EE.UU. 2024) tiene las oportunidades *más grandes y lucrativas* y la mayor *extracción realizada* en rebalancing; **Crypto** tiene los mayores *outliers* por condición. Sólo ~1% de las oportunidades estimadas de la elección EE.UU. fueron explotadas.

### Microestructura (paper de Dubach, arXiv:2604.24366)
Análisis de 30 mil millones de eventos de order book en 52 días (feb–abr 2026), panel de 600 mercados:
- **Spread**: la mediana del spread cotizado completo es ~400 bps en el rango central [0,4–0,6] y sube a **1.300–1.800 bps para mercados por debajo de 0,10** (confirma el longshot premium). Es un orden de magnitud más ancho que en equities líquidos.
- **Profundidad**: contrario al mito, la liquidez no está concentrada en el top-of-book; la mediana del ratio L1/top-10 es 0,137 (cercana al benchmark uniforme de 0,10), es decir, está distribuida en capas más profundas.
- **Composición del panel**: Crypto 58%, Sports 24%. La inferencia de dirección de trade desde el feed público coincide con la verdad on-chain sólo ~59% (relevante: no confíe ciegamente en la clasificación buy/sell del feed).

### Arquitectura técnica recomendada
**APIs oficiales (base: docs.polymarket.com):**
- **Gamma API** (`gamma-api.polymarket.com`): descubrimiento de mercados/eventos, metadata, `clobTokenIds`. Pública, sin auth. Ojo: `outcomePrices`, `outcomes` y `clobTokenIds` vienen como string JSON, hay que parsearlos.
- **CLOB API** (`clob.polymarket.com`): order book en vivo, precios, midpoint, spread, timeseries, colocación/gestión de órdenes. Lectura pública; trading requiere auth de dos niveles: **L1** (firma EIP-712 con la private key para derivar credenciales) y **L2** (HMAC con api_key/secret/passphrase en headers).
- **Data API** (`data-api.polymarket.com`): historial de trades, posiciones, leaderboard (antes en host separado, ahora consolidado). Pública.
- **Relayer API** (`relayer-v2.polymarket.com`): transacciones sin necesidad de tener POL para gas (trading gasless).
- **Bridge API** (`bridge.polymarket.com`): fondeo y retiro con activos soportados.
- **WebSockets**: dos sistemas en hosts distintos. **CLOB** (`wss://ws-subscriptions-clob.polymarket.com/ws/`) con canales `market` (público, order book; se suscribe con `assets_ids`), `user` (auth, tus órdenes/fills; se suscribe con `markets`/condition_id), `sports` (scores en vivo), `rfq`. **RTDS** (`wss://ws-live-data.polymarket.com`) para precios cripto (Binance/Chainlink) y comentarios. Heartbeat: PING cada 10s (market/user), 5s (sports/RTDS). No mezclar los dos sistemas (error común).

**Rate limits (verificados junio 2026, sin cambios tras CLOB V2):** REST general 15.000/10s; CLOB general 9.000/10s; Gamma ~4.000/10s; Data ~1.000/10s. Endpoints de trading con doble tier (ambos aplican simultáneamente): `POST /order` 3.500/10s burst y 36.000/10min sostenido (~60/s promedio); `DELETE /order` 3.000/10s y 30.000/10min; batch `POST /orders` y `DELETE /orders` 1.000/10s y 15.000/10min. Cloudflare hace throttling (encola/retrasa), a veces devuelve 429 sin headers. **Usar WebSockets para datos en vivo, no polling.**

**SDKs disponibles:**
- `py-clob-client` (Python, oficial) — maneja la aprobación del contrato CTF. Existe `py-clob-client-v2` para el nuevo contrato V2 (chain_id 137 mainnet, 80002 Amoy testnet).
- `@polymarket/clob-client` (TypeScript), `rs-clob-client` (Rust).
- `python-order-utils` / `clob-order-utils` para firma EIP-712 de bajo nivel.
- `real-time-data-client` (WebSocket con reconexión incorporada).
- **No existe testnet/sandbox de producción**: la primera prueba real de la ruta completa de trading es con fondos reales. Planificar en torno a esto (empezar con montos mínimos).

**Trampas conocidas:**
- Órdenes market (FOK/FAK) tienen precisión decimal más estricta que límite (GTC/GTD): maker amount máx 2 decimales, taker máx 4. Un bug de redondeo del SDK genera rechazos silenciosos.
- Post-only (`GTC`/`GTD`) garantiza status maker pero se rechaza si cruza el spread; incompatible con FOK/FAK.
- Errores comunes: 401 (auth/headers L1 inválidos), payload inválido por campos V1 residuales, precio bajo tick mínimo, tamaño bajo mínimo, balance/allowance insuficiente, 425 "Too Early" durante reinicios del cutover V2, 503 cancel-only durante incidentes, bans de direcciones y jurisdicciones close-only.
- Hay que ejecutar `setAllowances()` una vez para aprobar el CTF Exchange antes de operar.

**Arquitectura de sistema sugerida (para Claude Code):**
1. **Capa de ingesta de datos**: WebSocket market channel para order books en vivo + Gamma para descubrimiento periódico de mercados + RTDS para precios cripto. Reconstrucción de order book local (snapshot REST + updates incrementales) con reconexión por backoff exponencial.
2. **Capa de señales/modelo**: motor de detección de oportunidades — arb intra-mercado (VWAP de YES+NO desviándose de $1 por >2–5¢), sesgo favorito-longshot, corrección de extremos, y comparación con odds externas para deportes.
3. **Capa de gestión de riesgo**: position sizing (Kelly fraccionado), límites de exposición por mercado y por cluster correlacionado, "stop-loss lógico" (salida por invalidación de la tesis, no por precio arbitrario), y verificación de que el edge sobrevive comisiones+spread antes de disparar.
4. **Capa de ejecución**: firma EIP-712, colocación preferente de órdenes límite (maker, 0% fee + rebate), manejo de fills vía user channel, ejecución atómica cuidadosa en arb multi-leg.
5. **Persistencia**: base de datos (PostgreSQL/SQLite) registrando cada orden, fill, P&L realizado/no realizado, y métricas de calibración.
6. **Dashboard**: visualización (Streamlit/Dash/Grafana) de posiciones, P&L, drawdown, curva de equity, Brier score en el tiempo, oportunidades activas y latencia de ejecución.
7. **Infraestructura**: VPS 24/7 (fuera de jurisdicciones bloqueadas a nivel de order-placement — EE.UU./UK/varios de la UE), firewall, SSH con llaves, HTTPS/WSS, y confirmación on-chain vía listener de eventos `OrderFilled` en el CTF Exchange.

### Seguridad de wallet/claves
- **Nunca** hard-codear la private key ni guardarla en texto plano. Variables de entorno (.env) son el mínimo aceptable, pero pueden filtrarse en logs/outputs de debug; mejor cifrado en reposo (p. ej. ChaCha20-Poly1305 o AWS KMS) con descifrado en runtime; para escala, HSM que firma sin exponer la clave.
- Usar una wallet dedicada ("hot") con **sólo el capital de trading**; el grueso de los fondos en cold storage. Rotación periódica de claves. Considerar wallet proxy/smart-contract (Gnosis Safe) con signer delegado.
- La private key se usa **localmente** para firmar; nunca se transmite a Polymarket (sólo la firma va en la petición). Las credenciales L2 (api_key/secret/passphrase) autentican por separado — usar el user channel sólo desde el servidor, nunca en cliente.
- HTTPS/WSS/TLS para toda comunicación. VPS endurecido, puertos innecesarios cerrados, monitoreo de actividad y logins anómalos.

### APIs y fuentes de datos gratuitas
- **Polymarket Gamma/CLOB/Data**: gratis, sin key para lectura. Cubren ~80% de los casos de uso.
- **Datos on-chain de Polygon**: RPC público (`polygon-rpc.com`) o Chainstack/QuickNode (planes gratuitos con límites). **Envio HyperSync** para stream de eventos sin throttling de RPC (recomendado tras la deprecación del subgraph de Goldsky en abril 2026).
- **Dune Analytics**: dashboards comunitarios de Polymarket (SQL); plan gratuito con límites. Goldsky, Allium, CryptoHouse (ClickHouse) para datos on-chain adicionales.
- **Odds deportivas** (para comparar con mercados deportivos de Polymarket): The Odds API (plan gratuito, ~40 casas soft, sin Pinnacle), Odds-API.io (100 req/hora, 500/día, 2 casas recreativas), SportsGameOdds (free tier), SharpAPI (free tier 12 req/min, 2 casas con delay de 60s). **Las casas sharp (Pinnacle) y exchanges (Betfair) requieren plan pago** — importante porque el line de Pinnacle es el más informativo para detectar mispricing.
- **Datos macro**: FRED (ya conocido por el usuario), gratis.
- **Comparables prediction markets**: Kalshi API, Manifold, Metaculus para probabilidades cross-market.
- **Sentiment/noticias**: fuentes gratuitas limitadas; considerar upgrade pago si el edge lo justifica (baja prioridad frente a arbitraje estructural).

### Modelo estadístico y gestión de riesgo
- **Calibración**: Brier score (0=perfecto; 0,25=azar en binario; superforecasters humanos ~0,15–0,20; sabiduría de masas en prediction markets ~0,12–0,18). Complementar con log-loss, Expected Calibration Error (ECE), Maximum Calibration Error (MCE) y Brier Skill Score (mejora sobre base rate). El bot debe registrar predicción vs resultado para monitorear calibración continuamente y detener estrategias si el Brier se degrada por encima de ~0,20.
- **Kelly fraccionado**: la literatura (Matej et al. 2021; Thorp) recomienda Kelly adaptativo con control de riesgo, y hay trabajo reciente que formaliza el Kelly como evaluación bayesiana de modelos time-updating (arXiv:2602.09982). Nota conceptual (Wolfers/Zitzewitz; Manski): los precios de prediction markets son creencias medias, no probabilidades exactas, y bajo aversión al riesgo (CRRA>1) están sesgados hacia los extremos — aplicar una corrección de 3–8% hacia 0,50 para contratos <$0,15 o >$0,85. Regla práctica de arbitrajistas: baskets bloqueados (arb puro) usan fórmula de ganancia garantizada; todo lo demás usa **cuarto de Kelly**; cualquier trade cuyo edge desaparece tras comisiones recibe cero.
- **Sizing para múltiples operaciones diarias de bajo capital**: muchas apuestas pequeñas e independientes convierten un edge ruidoso en confiable (ley de grandes números). El favorito-longshot y la corrección de extremos son ideales para alta frecuencia y posición pequeña.
- **Diversificación**: capar exposición total entre pares de arbitraje abiertos, NO sólo cada par individual. Si cinco baskets dependen del mismo mercado/venue, es una sola apuesta concentrada; dimensionar el cluster correlacionado como una posición única. Diversificar entre eventos no correlacionados.
- **Expectativas realistas por estrategia** (de guías de trading; tratar como estimaciones optimistas, no garantías): market-making 0,5–2% mensual con <1% drawdown; correlación/mean-reversion 3–8% mensual; momentum/AI 15–30% mensual pero con -20% drawdown en días.

### Riesgos específicos de la plataforma
- **Oracle/resolución disputada**: UMA es un oráculo optimista basado en votación económica, no en prueba criptográfica. En el ataque de gobernanza del 24–25 de marzo de 2025 sobre el mercado "Will Ukraine agree to Trump's mineral deal before April?", un único actor con el 25% del poder de voto emitió 5 millones de tokens UMA en tres cuentas; las odds pasaron de 9% a 100% y el mercado resolvió "Yes" pese a no existir acuerdo oficial. Leer siempre las reglas de resolución, no sólo el título.
- **Liquidez**: order books delgados; posiciones grandes mueven el precio. El spread suele ser el mayor costo real (400 bps en el centro, hasta 1.800 bps en longshots).
- **Gas Polygon**: bajo pero no cero; relevante para arbitraje multi-leg de bajo margen.
- **Smart contracts**: aunque auditados (ChainSecurity auditó el NegRiskAdapter en abril 2024), hay riesgo de bugs; el paper "The Ghosts of Polymarket" (arXiv:2606.16852) documenta fills reportados off-chain que revierten on-chain.
- **Slippage y ejecución no-atómica**: arb multi-leg puede dejar exposición desnuda si sólo se llena una pata; el arb "long" (comprar YES+NO <$1) bloquea capital hasta la resolución, mientras el "short" (vía Split) se cierra de inmediato.
- **Riesgo de capital lock-up**: el colateral en tokens de outcome no resueltos queda inmovilizado hasta el settlement salvo merge/convert (arXiv:2605.31431).

### Riesgo regulatorio para Chile
- **Chile NO está en la lista de restringidos de Polymarket**; no bloquea IPs chilenas ni tiene modo close-only. Contraste con Argentina (bloqueada por fallo del Juzgado de Buenos Aires de la jueza Susana Parada en marzo de 2026, tras denuncia de LOTBA y la Cámara Argentina de Casinos por operar como "sistema de apuestas encubierto" sin verificación de identidad/edad) y Brasil (bloqueado en abril de 2026 por el Conselho Monetário Nacional junto a otros 26 sitios de predicción, por incumplir la regulación de apuestas).
- El trading cripto es legal en Chile bajo la **Ley Fintech 21.521** (enero 2023); los proveedores de servicios cripto debían registrarse ante la CMF antes de febrero de 2025. Chile tiene la mayor penetración de internet de la región (94,5%) y un ecosistema de exchanges maduro.
- El juego online sin autorización es ilegal (fallo de la Corte Suprema de 2025), pero **ningún regulador ha clasificado específicamente los mercados de predicción**. Operan en área gris no regulada. El riesgo práctico es que Chile siga el precedente de Argentina/Brasil/Colombia y los reclasifique como apuestas ilegales.
- Contexto EE.UU. (relevante si se opera desde/hacia allí): la CFTC clasificó formalmente los prediction markets como derivados (marzo 2026), reabrió el acceso de EE.UU. vía la exchange regulada QCX/Polymarket US (KYC completo), y emitió advertencia sobre insider trading (Regla 180.1) en event contracts (febrero 2026). El order-placement vía CLOB internacional sigue bloqueado geográficamente para EE.UU./UK/varios de la UE.

## Recommendations

**Fase 0 — Validación (antes de escribir código de ejecución):**
1. Confirmar acceso legal y práctico desde Chile; leer los Términos de Servicio y la lista oficial de restricciones geográficas de Polymarket (cambian rápido).
2. Definir 1–2 estrategias con edge documentado y bajo requerimiento de infraestructura de latencia: **arbitraje intra-mercado (YES+NO)** y **market-making pasivo con órdenes límite** (cobra rebate, 0% fee). No competir en latencia con bots sub-100ms.
3. Descartar de entrada toda estrategia cuyo edge no sobreviva comisiones + spread (usar la fórmula de fee por categoría y el spread mediano por decil de precio).

**Fase 1 — Paper trading / backtesting:**
4. Construir primero la capa de ingesta (WebSocket) y de detección de señales. Backtestear con datos históricos (Data API + Dune + eventos on-chain vía HyperSync). Recordar que el order book histórico NO está on-chain (sólo trades ejecutados).
5. Ejecutar paper trading en vivo (señales reales, órdenes simuladas) por 4–8 semanas. Medir Brier score, hit rate y P&L teórico neto de comisiones/spread/gas.
6. **Umbral para pasar a vivo**: calibración estable (Brier <0,20), P&L simulado positivo neto de todos los costos, y detección de arb con margen consistente >6% (para cubrir ~5% de comisiones combinadas en cross-platform, o el spread relevante en intra-mercado).

**Fase 2 — Vivo con capital mínimo:**
7. Capital inicial que se pueda perder por completo. Dado que no hay testnet, empezar con el mínimo operativo (decenas–cientos de USDC) para validar la ruta de firma/ejecución/allowances con dinero real antes de escalar.
8. Sizing: cuarto de Kelly o menos. Límite de exposición por mercado y por cluster correlacionado.
9. "Stop-loss lógico": salir cuando la tesis (mispricing) se invalida, no por movimiento de precio arbitrario. Para arb puro, mantener hasta resolución o cerrar vía merge/venta.
10. Wallet dedicada, private key cifrada (no en texto plano), VPS endurecido, alertas de anomalías y de disputas de oracle en los mercados con posición abierta.

**Fase 3 — Escalado condicional:**
11. Escalar sólo si el Sharpe/Calmar out-of-sample se mantiene y el drawdown real coincide con el esperado. **Benchmark de corte: si el drawdown real supera 2× el simulado, detener y re-evaluar.** Si el Brier score se degrada sostenidamente por encima de 0,20, apagar la estrategia direccional.

**Checklist antes de ir en vivo:** acceso legal confirmado; SDK v2 instalado y contrato CTF aprobado (`setAllowances`); allowances seteadas; manejo de errores 429/401/425/rechazos implementado; reconexión WebSocket con backoff probada; logging completo en base de datos; dashboard funcional; límites de riesgo (Kelly, exposición por cluster) codificados y probados en paper; plan de contingencia ante disputa de oracle y ante incidente 503 cancel-only.

## Caveats
- **Muchas cifras de rentabilidad por estrategia provienen de blogs/guías de trading, no de fuentes revisadas por pares**; los win rates y retornos mensuales por estrategia deben tratarse como estimaciones optimistas o promocionales. Las cifras robustas son las on-chain y académicas: 68,8% de usuarios perdió dinero (estudio Toronto/HEC/ESSEC), 0,51% de wallets con >$1.000 de ganancia, top 1% capturó 76,5% de las ganancias, y los ~$40M de arbitraje documentados por IMDEA.
- El paisaje regulatorio cambia rápido; verificar la lista de países restringidos de Polymarket y el estatus legal en Chile inmediatamente antes de operar. El precedente regional (Argentina, Brasil, Colombia) sugiere riesgo de reclasificación futura.
- La migración a CLOB V2 (28 abril 2026) y el cambio de USDC.e a pUSD hacen que mucha documentación de terceros esté obsoleta; **priorizar siempre docs.polymarket.com** sobre tutoriales.
- El edge de arbitraje está mayormente capturado por bots profesionales sub-100ms; un bot retail nuevo compite en desventaja de latencia. El nicho realista es market-making paciente (rebate) y arbitraje en mercados nuevos/ilíquidos que los bots grandes no priorizan.
- No hay testnet: todo test de la ruta de ejecución cuesta dinero real y gas. La inferencia de dirección de trade desde el feed público sólo acierta ~59%.
- Este informe es informativo y técnico; **no constituye asesoría financiera ni legal**. Trading con riesgo sustancial de pérdida total.