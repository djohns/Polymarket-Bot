# Deploy en VPS (Oracle Cloud, Fase 1)

Instancia Always Free existente, reutilizada de un proyecto anterior (bot de
Binance testnet, ya desinstalado por completo). Detalles particulares de esta
instancia en [CLAUDE.md](../CLAUDE.md).

## Datos de la instancia

- Proveedor: Oracle Cloud Infrastructure, shape `VM.Standard.E2.1.Micro` (Always Free).
- OS: **Oracle Linux 9.7** (no Ubuntu — usa `dnf`, no `apt`).
- Usuario SSH: `opc` (no `ubuntu`).
- RAM: 498 MiB — muy limitada, ver sección de memoria abajo.
- Repo instalado en `/opt/polymarket-bot`.
- Servicio systemd: `polymarket-bot.service`.

## Conectarse

```bash
ssh -i /ruta/a/tu-clave.key opc@<IP_PUBLICA>
```

## Instalación desde cero (referencia, ya aplicado)

```bash
# Sistema (repos reducidos + swappiness alto: ver nota de memoria abajo)
sudo sysctl -w vm.swappiness=100
sudo dnf install -y --disablerepo=ol9_ksplice --disablerepo=ol9_UEKR8 \
  --disablerepo=ol9_oci_included --disablerepo=ol9_addons \
  --setopt=install_weak_deps=False --setopt=tsflags=nodocs \
  git python3.11 python3.11-pip

# Repo y entorno
sudo mkdir -p /opt/polymarket-bot && sudo chown opc:opc /opt/polymarket-bot
git clone https://github.com/djohns/Polymarket-Bot.git /opt/polymarket-bot
cd /opt/polymarket-bot
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# .env (Fase 1: defaults vacíos alcanzan, no hay credenciales de trading todavía)
cp .env.example .env
chmod 600 .env
mkdir -p data
```

## Nota de memoria (importante para esta instancia)

Con sólo 498 MiB de RAM, `dnf` se queda sin memoria y es matado por el OOM
killer (`Killed process ... (dnf)`) si se lo deja con todos los repos
habilitados y configuración por defecto. La combinación que sí funcionó:

1. `sudo sysctl -w vm.swappiness=100` (default era 60; empuja al kernel a
   usar swap antes en vez de esperar a un pico y matar el proceso).
2. Deshabilitar repos no esenciales durante la instalación
   (`ol9_ksplice`, `ol9_UEKR8`, `ol9_oci_included`, `ol9_addons` — dejar sólo
   `ol9_baseos_latest` y `ol9_appstream`), reduciendo el trabajo del resolver
   de dependencias.
3. `--setopt=install_weak_deps=False --setopt=tsflags=nodocs` para instalar
   menos paquetes/menos peso.

El bot en sí (`python -m polybot.main`, Fase 1, 100 mercados / 200 assets
suscritos) usa ~50-70 MB de RSS en producción — no es el problema; el
problema es sólo `dnf` durante la instalación.

## Servicio systemd

Definido en `/etc/systemd/system/polymarket-bot.service`:

```ini
[Unit]
Description=Polymarket Bot - Fase 1 ingesta y deteccion de senales (sin trading)
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
User=opc
Group=opc
WorkingDirectory=/opt/polymarket-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/polymarket-bot/.venv/bin/python -m polybot.main
Restart=on-failure
RestartSec=30
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/opt/polymarket-bot/data

[Install]
WantedBy=multi-user.target
```

Versionado en [`deploy/polymarket-bot.service`](../deploy/polymarket-bot.service).
Instalarlo (si se recrea desde cero):

```bash
sudo cp deploy/polymarket-bot.service /etc/systemd/system/
sudo restorecon -v /etc/systemd/system/polymarket-bot.service  # ver nota SELinux abajo
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-bot.service
```

**Nota SELinux**: si el archivo del unit se copió pasando por `/tmp` (por
ejemplo vía `scp` a `/tmp` y luego `mv`), queda con el contexto SELinux
`user_tmp_t` y systemd lo reporta como "Unit file does not exist" aunque el
archivo esté ahí. Solución: `sudo restorecon -v /etc/systemd/system/polymarket-bot.service`
antes de `enable`.

## Operación

```bash
# Ver logs en vivo
sudo journalctl -u polymarket-bot.service -f

# Ver últimas N líneas
sudo journalctl -u polymarket-bot.service -n 100 --no-pager

# Estado / memoria actual
sudo systemctl status polymarket-bot.service

# Reiniciar manualmente
sudo systemctl restart polymarket-bot.service

# Detener
sudo systemctl stop polymarket-bot.service

# Deshabilitar arranque automático
sudo systemctl disable polymarket-bot.service
```

`Restart=on-failure` + `RestartSec=30`: si el proceso crashea, systemd lo
reinicia solo a los 30s. `WantedBy=multi-user.target` + `enable`: arranca
solo si la VM se reinicia. Ambos comportamientos probados manualmente
(`kill -9` al proceso → vuelve solo).

## Actualizar el código en el VPS

```bash
cd /opt/polymarket-bot
git pull
.venv/bin/pip install -e .   # sólo si cambiaron dependencias
sudo systemctl restart polymarket-bot.service
```

## Dashboard (Fase 2, parte 3)

Reporte HTML estático (`data/dashboard.html`), regenerado cada 15 minutos por
un systemd timer — **no** es un servidor vivo (ver CLAUDE.md para por qué se
descartó Streamlit/Dash en esta instancia). Definidos en
[`deploy/polymarket-bot-dashboard.service`](../deploy/polymarket-bot-dashboard.service)
y [`deploy/polymarket-bot-dashboard.timer`](../deploy/polymarket-bot-dashboard.timer):

```bash
sudo cp deploy/polymarket-bot-dashboard.service deploy/polymarket-bot-dashboard.timer /etc/systemd/system/
sudo restorecon -v /etc/systemd/system/polymarket-bot-dashboard.*  # misma trampa SELinux que el service principal
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-bot-dashboard.timer

# Generar una corrida manual (sin esperar al timer)
sudo systemctl start polymarket-bot-dashboard.service

# Ver cuándo corrió / próxima corrida
systemctl list-timers polymarket-bot-dashboard.timer

# Logs de la última generación
sudo journalctl -u polymarket-bot-dashboard.service -n 20 --no-pager
```

Para verlo sin exponerlo a internet: `scp` el archivo
(`scp -i clave.key opc@IP:/opt/polymarket-bot/data/dashboard.html .` y abrirlo
local), o pedirle a Claude Code que lo traiga y lo publique como Artifact en
el chat del proyecto. Para verlo en tiempo real sin SCP manual, ver la
siguiente sección.

## Dashboard vía web (Fase 2, parte 4) — nginx

**URL**: `http://<IP_PUBLICA>:8090/dashboard.html` (HTTP simple, no HTTPS —
ver justificación de seguridad abajo). Usuario `polybot`, contraseña
generada al momento del setup (no versionada en el repo — pedirla a quien
hizo el deploy, o regenerarla con el comando de abajo).

**Decisiones de seguridad** (contenido no sensible — paper trading, sin
private key ni credenciales de trading — pero es un servicio nuevo expuesto
a internet, así que igual se aplicó protección básica):
- **Puerto no estándar** (8090, no 80/443) — reduce ruido de escaneo masivo
  automatizado de los puertos por defecto, aunque no es una barrera real.
- **HTTP Basic Auth** (`auth_basic` + `.htpasswd`) — la protección real. Nota
  honesta: es HTTP plano, no HTTPS, así que el usuario/contraseña viajan sin
  cifrar en la red — aceptable acá porque el contenido no es sensible y el
  objetivo es sólo evitar acceso casual/scanners, no un adversario activo en
  la red. No se justificó el costo de operar TLS (Let's Encrypt necesita un
  dominio; la instancia sólo tiene IP pública) para este nivel de riesgo.
- **Nada del filesystem expuesto salvo ese archivo exacto**: la config de
  nginx no tiene `root` de directorio — usa `alias` apuntando al único
  archivo `dashboard.html`, y cualquier otro path devuelve 404. La base
  SQLite (`polybot.db`) vive en el mismo directorio pero nunca es alcanzable
  por HTTP.
- **Puerto 80 completamente deshabilitado**: se quitó el server block por
  defecto de `nginx.conf` en vez de dejarlo con la página de bienvenida sin
  protección.

**Instalación** (paquete `nginx` vía `dnf`, mismo cuidado de memoria que el
resto de la instalación — repos reducidos + `swappiness=100`, ver arriba):

```bash
sudo sysctl -w vm.swappiness=100
sudo dnf install -y --disablerepo=ol9_ksplice --disablerepo=ol9_UEKR8 \
  --disablerepo=ol9_oci_included --disablerepo=ol9_addons \
  --setopt=install_weak_deps=False --setopt=tsflags=nodocs nginx

# Basic auth: generar contraseña + hash APR1 (sin instalar httpd-tools)
PASS=$(openssl rand -base64 18 | tr -d '=+/' | head -c 20)
HASH=$(openssl passwd -apr1 "$PASS")
echo "polybot:$HASH" | sudo tee /etc/nginx/.htpasswd >/dev/null
sudo chmod 640 /etc/nginx/.htpasswd && sudo chown root:nginx /etc/nginx/.htpasswd
echo "Contraseña generada (guardarla, no queda en ningún archivo del repo): $PASS"

# Config: deploy/nginx.conf reemplaza /etc/nginx/nginx.conf completo (quita el
# server block del puerto 80); deploy/nginx-dashboard.conf va en conf.d/.
sudo cp deploy/nginx.conf /etc/nginx/nginx.conf
sudo cp deploy/nginx-dashboard.conf /etc/nginx/conf.d/dashboard.conf
sudo restorecon -Rv /etc/nginx/nginx.conf /etc/nginx/conf.d/dashboard.conf /etc/nginx/.htpasswd
```

**SELinux** (Enforcing en esta instancia — dos ajustes no obvios, ninguno
cubierto por `restorecon` porque no son de contexto de archivo sino de
política):

```bash
# El puerto 8090 no está en la lista http_port_t por defecto (sólo 80, 81,
# 443, 488, 8008, 8009, 8443, 9000) -- sin esto nginx falla el bind con
# "Permission denied" aunque el firewall esté bien.
sudo semanage port -a -t http_port_t -p tcp 8090

# El archivo vive en /opt/polymarket-bot/data/, etiquetado usr_t (heredado del
# resto del proyecto) -- httpd_t no puede leerlo hasta reetiquetarlo. Como
# report.py reescribe el archivo in-place (mismo inodo, no lo recrea), esta
# regla persiste entre regeneraciones y sólo hace falta aplicarla una vez.
sudo semanage fcontext -a -t httpd_sys_content_t '/opt/polymarket-bot/data/dashboard.html'
sudo restorecon -v /opt/polymarket-bot/data/dashboard.html
```

**Firewall local (firewalld) + arranque**:

```bash
sudo firewall-cmd --permanent --add-port=8090/tcp
sudo firewall-cmd --reload
sudo systemctl enable --now nginx
```

**Firewall de red de Oracle Cloud (Security List / NSG) — hay que hacerlo
aparte, en la consola web, con la cuenta de OCI.** Oracle Cloud filtra a
nivel de VCN *además* del firewall del SO — abrir sólo firewalld no alcanza,
el tráfico externo nunca llega a la instancia si la Security List lo
bloquea antes. Claude Code no tiene acceso a la consola de OCI (es un login
de cuenta separado), así que este paso lo tiene que hacer el dueño de la
cuenta:

1. Consola OCI → **Networking → Virtual Cloud Networks** → la VCN de esta
   instancia (subnet con CIDR `10.0.0.0/24`, región `sa-santiago-1` — o más
   directo: **Compute → Instances → (esta instancia) → Instance details →
   pestaña "Attached VNICs" → click en la VNIC → link a la subnet**).
2. Entrar a la subnet → **Security Lists** → la lista asociada (normalmente
   "Default Security List for `<nombre VCN>`").
3. **Add Ingress Rules**:
   - Source Type: `CIDR`, Source CIDR: `0.0.0.0/0` (o restringir a una IP/red
     propia si se quiere acotar aún más — recomendado si se conoce una IP
     fija desde donde se va a mirar).
   - IP Protocol: `TCP`.
   - Destination Port Range: `8090`.
   - Description: algo como "Dashboard Polymarket Bot (Fase 2, HTTP+Basic Auth)".
4. Guardar. Los cambios de Security List aplican casi al instante, sin
   reiniciar nada en la instancia.

**Validar** (desde la propia VPS primero, sin depender de que el Security
List ya esté abierto):

```bash
curl -i http://localhost:8090/dashboard.html          # 401 sin credenciales
curl -i -u polybot:<PASSWORD> http://localhost:8090/dashboard.html   # 200
curl -i http://localhost:8090/polybot.db               # 404 -- nada más se expone
```

Y desde afuera, una vez abierta la Security List:

```bash
curl -i -u polybot:<PASSWORD> http://<IP_PUBLICA>:8090/dashboard.html
```

**Memoria**: nginx con esta config (1 worker, sin módulos extra) usa ~2MB de
RSS — no compite de forma relevante con el bot (que sigue en 40-80MB) en los
498MB totales de la instancia.

**Operación**:

```bash
sudo systemctl status nginx
sudo systemctl restart nginx
sudo nginx -t                              # validar sintaxis antes de recargar
sudo journalctl -u nginx -n 50 --no-pager
```

## Fase 3 — capital real: cifrado de la private key y passphrase

Capital real ($20 USDC), sólo arb intra-mercado en mercados deportivos
(resolución rápida) -- ver CLAUDE.md, sección "Fase 3", para el alcance
completo y las decisiones de seguridad. Esta sección es el procedimiento
paso a paso que le toca ejecutar al dueño de la cuenta (no a Claude Code):
generar la passphrase, cifrar la private key, y arrancar el servicio con
todo en su lugar.

### 1. Generar la passphrase (una sola vez)

En cualquier máquina de confianza (no hace falta que sea la VPS):

```bash
openssl rand -base64 32
```

Guardar el resultado en un gestor de contraseñas propio (1Password, Bitwarden,
etc.) -- **nunca en el repo, nunca en el `.env`, nunca en un archivo de texto
en la VPS**. Esta passphrase es la única llave que descifra la private key de
trading real; si se pierde, hay que rotarla (ver más abajo) generando una
private key nueva para la wallet.

### 2. Cifrar la private key en la VPS

Con la VPS ya con el repo actualizado (`git pull`) y las dependencias
instaladas (`pip install -e ".[dev]"`, trae `cryptography`):

```bash
cd /opt/polymarket-bot
.venv/bin/python scripts/encrypt_private_key.py
```

El script pide (con `getpass`, no queda en el historial de shell ni en `ps`):
1. La private key de la wallet dedicada a Fase 3.
2. La passphrase generada en el paso 1 (dos veces, para confirmar).
3. La ruta de salida (default `data/private_key.enc`, coincide con
   `REAL_ENCRYPTED_KEY_PATH` del `.env`).

Escribe `data/private_key.enc` con permisos `600`. Ese archivo sí puede vivir
en la VPS (está cifrado) pero nunca se commitea -- ya cae bajo el patrón
`data/` de `.gitignore`.

### 3. Exportar la passphrase al arrancar el servicio

La passphrase se lee en runtime desde la variable de entorno
`POLYMARKET_KEY_PASSPHRASE` (configurable vía `REAL_KEY_PASSPHRASE_ENV_VAR`),
separada del `.env` principal a propósito. Mecanismo elegido (y ya versionado
en `deploy/polymarket-bot.service`): un `EnvironmentFile=` de systemd que
apunta a `/etc/polymarket-bot-secret.env`, un archivo **fuera del repo**, con
permisos `600` y propietario `opc`, que sólo contiene esa variable:

```bash
sudo tee /etc/polymarket-bot-secret.env > /dev/null <<'EOF'
POLYMARKET_KEY_PASSPHRASE=<passphrase>
EOF
sudo chmod 600 /etc/polymarket-bot-secret.env
sudo chown opc:opc /etc/polymarket-bot-secret.env
```

El unit file referencia este archivo con el prefijo `-` (`EnvironmentFile=-/etc/polymarket-bot-secret.env`)
para que su ausencia no rompa el arranque en Fase 1/2 (sin capital real, sin
passphrase que exportar). Reinstalar el unit tras actualizar el repo:

```bash
cd /opt/polymarket-bot
git pull
sudo cp deploy/polymarket-bot.service /etc/systemd/system/
sudo restorecon -v /etc/systemd/system/polymarket-bot.service
sudo systemctl daemon-reload
sudo systemctl restart polymarket-bot.service
```

Se prefirió esto sobre `Environment=` fijo en el unit (que también quedaría
en disco, pero commiteado/versionado y visible a cualquiera con acceso al
repo) y sobre exportar la variable a mano en cada arranque manual
(`export POLYMARKET_KEY_PASSPHRASE=...` antes de `python -m polybot.main`,
todavía válido para pruebas puntuales sin systemd) porque un servicio que se
reinicia solo vía `Restart=on-failure` necesita que la passphrase esté
disponible en cada reinicio automático, no sólo en el primer arranque manual.

**No** guardar esta variable en `/etc/environment`, en el unit file de
systemd (commiteado en el repo), ni en ningún archivo dentro de
`/opt/polymarket-bot` -- vive únicamente en `/etc/polymarket-bot-secret.env`,
fuera del árbol del repo, sin cifrar en disco pero con permisos restrictivos
como única defensa (igual que `.env` para el resto de credenciales).

### 4. Activar Fase 3

Con `data/private_key.enc` en su lugar y la passphrase exportada, en el
`.env`:

```
REAL_TRADING_ENABLED=true
```

Al arrancar, el bot descifra la key en memoria, construye el cliente CLOB
autenticado, verifica el allowance de COLLATERAL (lo actualiza si hace falta)
y sólo entonces empieza a evaluar oportunidades reales. Si el kill-switch ya
está activo al arrancar (`data/REAL_TRADING_HALTED` existe), el motor de
ejecución real ni siquiera se construye -- se loguea y el resto del bot sigue
en modo Fase 1/2 normal.

**Antes de la primera orden real**: avisar por el chat del proyecto y esperar
confirmación explícita -- no se activa `REAL_TRADING_ENABLED=true` de forma
automática la primera vez.

### 5. Kill-switch manual

Para detener el trading real en cualquier momento sin matar el proceso
(sigue haciendo paper trading normal para todo lo demás):

```bash
touch /opt/polymarket-bot/data/REAL_TRADING_HALTED
```

Para reactivar, después de confirmar que la causa de la parada está resuelta:

```bash
rm /opt/polymarket-bot/data/REAL_TRADING_HALTED
sudo systemctl restart polymarket-bot.service   # re-exportar la passphrase si el proceso se reinició
```

El mismo archivo lo crea automáticamente el kill-switch por drawdown (balance
real bajo $15) -- en ese caso no alcanza con borrarlo sin más: primero hay que
entender por qué cayó el balance antes de reactivar.

### 6. Rotar la private key o la passphrase

Si se sospecha que la passphrase se filtró, o simplemente por higiene
periódica:

1. Generar una wallet nueva y transferirle el capital real restante (o
   generar una passphrase nueva si sólo se quiere rotar eso, reusando la
   misma wallet).
2. Volver a correr `scripts/encrypt_private_key.py` con la nueva private
   key y/o nueva passphrase, sobrescribiendo `data/private_key.enc`.
3. Actualizar la passphrase exportada (paso 3) y reiniciar el servicio.
4. Confirmar que el allowance de COLLATERAL sigue vigente para la wallet
   nueva (el bot lo verifica solo al arrancar, pero conviene confirmarlo a
   mano la primera vez tras una rotación).

## Revisar datos acumulados

La base SQLite vive en `/opt/polymarket-bot/data/polybot.db`. Para
inspeccionarla desde el VPS:

```bash
cd /opt/polymarket-bot
.venv/bin/python -c "
from polybot.persistence.db import get_session
from polybot.persistence.models import Opportunity
with get_session() as s:
    print(s.query(Opportunity).count(), 'oportunidades registradas')
"
```
