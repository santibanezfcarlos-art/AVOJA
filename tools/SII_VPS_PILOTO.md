# Piloto e-Boleta: navegador persistente en VPS

**Estado:** prueba de viabilidad exclusivamente; NO emite documentos ni modifica PSC/Render.

## Requisitos

- VPS Ubuntu 24.04 LTS, al menos 2 vCPU / 4 GB RAM para navegador y escritorio.
- Usuario Linux sin privilegios para ejecutar Playwright.
- Acceso remoto protegido por SSH, sin publicar escritorio remoto ni directorios de sesión.
- Confirmar con el proveedor que el VPS permite navegador Chromium, escritorio remoto y almacenamiento persistente.
- No ingresar una sesión del SII en servicios de terceros que no administres o no sean de confianza.

## Instalación base (en el VPS)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip xfce4 xfce4-goodies xrdp
sudo systemctl enable --now xrdp
# No exponer puerto TCP 3389 en el firewall ni en el panel del proveedor.
mkdir -p ~/psc-sii && cd ~/psc-sii
python3 -m venv .venv
source .venv/bin/activate
pip install playwright
python -m playwright install --with-deps chromium
```

Copiar solo `tools/sii_sesion_persistente.py` a `~/psc-sii`.

Configura escritorio remoto **solo a través de un túnel SSH** o la VPN privada del proveedor. Por ejemplo, desde el equipo personal:

```bash
ssh -N -L 13389:127.0.0.1:3389 USUARIO@IP_DEL_VPS
```

Conectar cliente Escritorio Remoto a `127.0.0.1:13389`. Ajustar xrdp para escuchar exclusivamente localhost y comprobarlo con `ss -lntp` antes de usarlo. Para ello, revisar `/etc/xrdp/xrdp.ini` según la versión instalada. No alojar el perfil del SII en una carpeta pública.

## Comprobación en el mismo escritorio remoto

Abrir una terminal del escritorio remoto, con sesión gráfica activa, y ejecutar:

```bash
cd ~/psc-sii
source .venv/bin/activate
python sii_sesion_persistente.py autorizar --perfil ~/psc-sii/perfil-privado
```

Iniciar sesión manualmente, esperar a ver pantalla del monto, **no pulsar EMITIR**, volver a terminal y ENTER.

Después cerrar el navegador y ejecutar:

```bash
python sii_sesion_persistente.py comprobar --perfil ~/psc-sii/perfil-privado
```

Esperado: `Sesión reutilizable: SÍ`. Si indica NO o HTTP 403, registrar solo el estado y detener la prueba; no intentar evadir controles ni replicar cookies. El inicio de sesión puede expirar y requerir una nueva autorización.

## Integración posterior con PSC

No abrir un puerto público para controlar el navegador. Cuando la prueba haya funcionado, se evaluará un *worker* interno que consulte trabajos firmados o mediante conexión saliente autenticada con Render. Antes de habilitar el botón «Emitir boleta» habrá que confirmar el flujo de autorización que admite el SII, la segunda pantalla y el tratamiento de una emisión tributaria real.

No instalar en el VPS la contraseña tributaria en scripts, comandos de shell, imágenes de Docker o GitHub.
