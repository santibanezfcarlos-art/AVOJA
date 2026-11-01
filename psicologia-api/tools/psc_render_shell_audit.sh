#!/usr/bin/env bash
# Solo auditoría en Shell del servicio PAGADO psicologia-carlos-calendar-api.
# NO ejecutar sobre el piloto gratuito: Render no ofrece Shell allí.
# Sin escrituras en las bases, sin secretos en historial, sin despliegue.
set -euo pipefail
umask 077
trap 'unset PSICOLOGIA_HOLDS_DATABASE_URL' EXIT

echo 'PSC - Auditoría privada de migración (solo lectura)'
if [[ -z "${RENDER:-}" ]]; then
  echo 'Esta auditoría debe ejecutarse únicamente en el Shell de Render.' >&2
  exit 1
fi
if [[ -z "${PSICOLOGIA_BILLING_DB:-}" ]]; then
  echo 'Este no parece ser el servicio principal de Psicología (sin base SQLite configurada).' >&2
  exit 1
fi
echo 'Preparando herramientas aisladas en /tmp; ninguna instalación global...'
mkdir -p /tmp/psc-migration-deps
TOOLS_URL='https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/91393851d567ffb3174f5b8667542ef9c1a301b3/psicologia-api/tools'
curl -fsSL "${TOOLS_URL}/psc_pg_audit_readonly.py" -o /tmp/psc_pg_audit_readonly.py
curl -fsSL "${TOOLS_URL}/psc_disk_preflight.py" -o /tmp/psc_disk_preflight.py
python -m pip install --quiet --disable-pip-version-check \
  --target /tmp/psc-migration-deps 'psycopg[binary]>=3.2,<4'

echo 'Introduce la URL INTERNA de psicologia-holds-piloto.'
echo 'Solo se usará en memoria. No la publiques en el chat ni en capturas.'
IFS= read -r -s -p 'URL interna PostgreSQL (entrada oculta): ' PSICOLOGIA_HOLDS_DATABASE_URL
printf '\n'
if [[ "$PSICOLOGIA_HOLDS_DATABASE_URL" != postgresql://* && "$PSICOLOGIA_HOLDS_DATABASE_URL" != postgres://* ]]; then
  echo 'URL inválida. No se ejecutó ningún cambio.' >&2
  exit 1
fi
export PSICOLOGIA_HOLDS_DATABASE_URL

echo 'Inventario de PostgreSQL (solo conteos):'
PYTHONPATH=/tmp/psc-migration-deps python /tmp/psc_pg_audit_readonly.py

echo 'Estado del disco SQLite persistente (sin datos privados):'
python /tmp/psc_disk_preflight.py

echo 'PSC_AUDIT_COMPLETED: ninguna modificación de reservas, cobros ni boletas.'
