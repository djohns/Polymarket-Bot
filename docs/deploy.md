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

Para verlo: `scp` el archivo (`scp -i clave.key opc@IP:/opt/polymarket-bot/data/dashboard.html .`
y abrirlo local), o pedirle a Claude Code que lo traiga y lo publique como
Artifact en el chat del proyecto.

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
