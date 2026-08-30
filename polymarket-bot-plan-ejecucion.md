# Plan de ejecución — Bot de trading Polymarket

## Roles
- **Este chat (Proyecto)** = centro de decisiones, arquitectura, revisión de riesgo, memoria del proyecto.
- **Claude Code** = ejecutor. Recibe instrucciones puntuales generadas acá, escribe/prueba código en el repo.
- Regla de oro: acá se decide **qué** y **por qué**; en Claude Code se resuelve **cómo**, con el detalle técnico ya masticado para no gastar tokens re-explorando.

## Optimización de tokens (aplica a todo el proyecto)
1. Un solo `CLAUDE.md` en el repo con: arquitectura, decisiones tomadas, convenciones, y **lo que NO volver a explorar** (ya resuelto). Claude Code lo lee una vez por sesión, no en cada prompt.
2. Especificaciones técnicas (endpoints, rate limits, fórmulas, checklist) se escriben una sola vez como archivos de referencia (`/docs`) — Claude Code las lee, no las regeneramos por chat.
3. En este chat: pido resultados/decisiones, no vuelco de código completo salvo que necesite revisar algo puntual.
4. Tareas grandes a Claude Code se dan **por fase, no todas juntas** — evita contexto gigante innecesario.
5. Nada de repetir el informe de investigación en prompts a Claude Code: se referencia el archivo, no se pega el texto.

## Fase 0 — Setup y validación (1 semana)
**Acá:**
- Confirmar decisión de estrategia inicial: **arbitraje intra-mercado + market-making pasivo** (mayor evidencia, menor requerimiento de latencia).
- Definir estructura de repo y `CLAUDE.md`.

**Tú (fuera de Claude):**
- Crear wallet dedicada (nueva, sólo para el bot) — Polygon, con Metamask o similar.
- Obtener credenciales:
  - **Polymarket API keys (L1/L2)**: se derivan firmando con la private key vía `py-clob-client` — no hay "registro" previo, se generan en el primer setup.
  - **The Odds API** (odds deportivas, free tier) → cuenta en the-odds-api.com.
  - **RPC Polygon**: cuenta gratuita en Chainstack o QuickNode (o usar `polygon-rpc.com` público como fallback).
  - Fondear la wallet con USDC en Polygon — monto mínimo de prueba (ver Fase 2).

**Claude Code:**
- Scaffolding del repo (backend Python + frontend dashboard).
- Setup `py-clob-client-v2`, conexión de prueba a Gamma/CLOB (solo lectura, sin trading).

## Fase 1 — Ingesta y detección de señales (2–3 semanas)
**Acá:** reviso arquitectura de detección antes de que se programe (evitar iterar en Claude Code a ciegas).

**Claude Code construye:**
1. Capa de ingesta: WebSocket CLOB (`market` channel) + Gamma para descubrimiento de mercados + reconstrucción de order book local.
2. Motor de señales v1: detector de arbitraje intra-mercado (YES+NO ≠ $1, umbral configurable).
3. Motor de señales v2: sesgo favorito-longshot (comparar precio vs. probabilidad implícita corregida).
4. Persistencia: SQLite/Postgres con cada oportunidad detectada, timestamp, spread, fees estimadas.

**Salida de la fase:** log de oportunidades detectadas (sin ejecutar nada real) por ≥1 semana corrida.

## Fase 2 — Paper trading (4–8 semanas)
**Acá:** reviso métricas semanales (Brier score, hit rate, P&L simulado neto de fees/spread/gas). Decido si se ajusta el modelo o se avanza.

**Claude Code:**
- Simulador de ejecución (fills hipotéticos contra el order book real, sin firmar transacciones).
- Dashboard v1: posiciones simuladas, P&L, curva de equity, Brier score en el tiempo.
- Position sizing con Kelly fraccionado (¼) ya integrado, aunque sea simulado.

**Umbral de salida de fase (definido en el informe):** Brier <0,20, P&L simulado neto positivo, margen de arb consistente >6%.

## Fase 3 — Vivo con capital mínimo (2–4 semanas)
**Acá:** apruebo explícitamente el paso a capital real. Reviso checklist de seguridad antes de dar luz verde.

**Claude Code:**
- Capa de ejecución real: firma EIP-712, órdenes límite (maker preferente), manejo de fills vía `user` channel.
- Gestión de riesgo activa: límites por mercado/cluster, kill-switch manual y automático (por drawdown).
- Wallet: private key cifrada en reposo (no `.env` plano), fuera de repo/logs.

**Capital inicial:** montos pequeños que puedas perder por completo (decenas–cientos de USDC), suficiente para validar la ruta real sin exposición relevante.

## Fase 4 — Escalado condicional
Sólo si: drawdown real ≤ 2× el simulado, Brier score estable, comisiones no erosionan el edge. Escalar capital gradualmente, no de golpe.

## APIs a conseguir (resumen accionable)
| API | Uso | Costo | Acción |
|---|---|---|---|
| Polymarket Gamma/CLOB/Data | Core del bot | Gratis | Se deriva credenciales L1/L2 al programar (no requiere registro previo) |
| The Odds API | Comparar odds deportivas vs. mercados Polymarket | Free tier | Crear cuenta, obtener key |
| RPC Polygon (Chainstack/QuickNode) | Lectura on-chain, fallback | Free tier | Crear cuenta |
| Envio HyperSync (opcional, fase posterior) | Stream de eventos on-chain sin throttling | Gratis | Sólo si se necesita en Fase 4+ |

## Próximo paso inmediato
Cuando muevas esto a Proyecto: primer prompt a Claude Code = scaffolding de Fase 0 + `CLAUDE.md`, referenciando este plan y el informe de investigación como archivos adjuntos (no pegados en el prompt).
