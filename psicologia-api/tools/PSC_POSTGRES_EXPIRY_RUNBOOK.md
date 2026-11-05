# PSC: continuidad antes del vencimiento PostgreSQL
Fecha de inspección: 2026-11-05. Este documento NO indica una migración ejecutada.

## Hechos comprobados
- Render PostgreSQL `psicologia-holds-piloto`, ID `dpg-dbdrg6fb9u1s73b9coag-a`, PostgreSQL 18, plan Free, vence `2026-11-22T19:40:09Z`; estado `available` al inspeccionarlo.
- Servicio `psicologia-retenciones-piloto` ejecuta `bootstrap_holds.py` y `pilot_app.py`; registra `PSICOLOGIA_PREFLIGHT_DB: accessible` y `PSICOLOGIA_PREFLIGHT_CALENDAR: accessible` el 2026-11-05.
- Servicio público `psicologia-carlos-calendar-api` tiene disco persistente `/var/data` (1 GB), y `billing.py` escribe en SQLite mediante `PSICOLOGIA_BILLING_DB`.
- `main.py` combina disponibilidad iCal, puente firmado de retenciones y disponibilidad de pagos desde SQLite. Si el puente no está disponible, responde cerrado para evitar cupos duplicados.
- `production_fulfillment.py` vive en el servicio piloto y proporciona comprobación de calendario, confirmación, correo, reprogramación y envío de boletas ya emitidas.
- PostgreSQL guarda `psychology_booking_holds`, `psychology_demo_payments`, `psychology_google_oauth`, `psychology_fonasa_bookings` y otras tablas. **No se debe eliminar o desconectar en producción sin migración validada**.

## Respaldo
El script `tools/psc_postgres_encrypted_backup.sh` genera un dump custom con `pg_dump`, comprueba el índice de contenido, lo cifra con OpenSSL, lo descifra en temporal para verificar el recorrido y escribe SHA-256. No hace transacciones de escritura. Requiere `PSICOLOGIA_HOLDS_DATABASE_URL`, `PSC_BACKUP_PASSPHRASE` y `PSC_BACKUP_DIR` y acceso de red permitido a PostgreSQL. No guardar credenciales en Git ni logs. Restringir permisos al operador.

**Importante:** verificación de descifrado NO equivale a una restauración íntegra. Para ello restaurar en un PostgreSQL 18 aislado con `pg_restore --dbname=<DESTINO_AISLADO> --no-owner --no-acl --exit-on-error`, contrastar tablas/filas y comprobar integridad referencial. Nunca usar la URL de producción como destino.

## Bloqueos verificados
- La lista de IP permitidas del PostgreSQL está vacía. El conector Render remoto no puede consultar la base y no permite generar un dump con la herramienta de SQL de solo lectura.
- No existe una copia actualizada y restaurada de forma verificable en esta sesión. Los respaldos reportados del 1 de noviembre deben comprobarse en Windows antes de considerarlos recuperables.

## Puertas de salida para migrar
1. Respaldo actualizado y restauración completa probada en Postgres aislado, con conteos y restricciones contrastadas.
2. Inventario de fuentes de verdad: OAuth cifrado, retenciones activas, Fonasa, reservas confirmadas, pendientes de correo/Calendar; conciliación con SQLite y Google Calendar.
3. Nueva persistencia con bloqueo transaccional equivalente y soporte de las rutas `/internal/psicologia/demo/busy` y de fulfillment de producción.
4. Pruebas end-to-end de disponibilidad, concurrencia, Webpay, Fonasa, reagendamiento, Meet, notificaciones y boletas, sin cobros reales de prueba.
5. Ventana controlada, respaldo final, corte reversible, monitorización y rollback. Mantener PostgreSQL original hasta después de verificar.
6. Si la migración completa no puede verificarse antes del vencimiento, mantener PostgreSQL mediante un plan compatible **solo tras aprobación explícita del costo**.

## Alcance
Esta rama es preparación y documentación. No alterar `psicologia-carlos`, variables de Render, planes, DNS ni servicios en producción.
