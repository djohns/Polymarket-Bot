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
