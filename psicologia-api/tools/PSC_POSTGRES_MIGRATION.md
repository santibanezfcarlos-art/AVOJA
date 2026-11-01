# Migración PSC — PostgreSQL gratuito a SQLite persistente

**Fecha de inicio:** 2026-11-01  
**Vencimiento confirmado:** 2026-11-22  
**Estado:** preparación/diagnóstico; SIN migración ejecutada, SIN despliegues, SIN respaldos generados todavía.

## Recursos identificados

| Recurso | Identificador Render | Rol |
|---|---|---|
| PostgreSQL gratuito `psicologia-holds-piloto` | `dpg-dbdrg6fb9u1s73b9coag-a` | Retenciones, piloto, enlaces de Fonasa y OAuth cifrado |
| Web `psicologia-retenciones-piloto` | `srv-dbdrmion74is73em6nig` | Adaptadores Google/Resend, lectura de PostgreSQL y flujos Fonasa |
| Web `psicologia-carlos-calendar-api` | `srv-dbd9umui0phs739245cg` | Agenda pública, compras, boletas, SQLite |
| Disco persistente API | `dsk-dbdrfdjrjlhs73850ds0` | 1 GB montado en `/var/data` |

**Precaución:** Render no comparte ese disco entre servicios. No basta con cambiar una URL de conexión; los adaptadores deben pasar por la API principal o migrar a su proceso.

**Ramas existentes:** el web principal despliega `psicologia-carlos`; el piloto despliega `psicologia-webpay-produccion-preparacion`. Esta rama de mantenimiento **no está conectada a Render**.

## Dependencias que no se deben romper

- `psychology_booking_holds` y `psychology_booking_days`: reservas temporales, cupos y bloqueos.
- `psychology_fonasa_bookings`: tokens hash de reagendamiento; conservar ID, token hash, estado y horario.
- `psychology_google_oauth`: tokens de actualización **cifrados**. La clave de descifrado debe continuar disponible; nunca exportarla a logs o a GitHub.
- `psychology_demo_payments`, `psychology_demo_notifications`, `psychology_reschedule_links`, `psychology_reschedule_audit`: investigar si hay filas vigentes antes de dar por obsoletos los pilotos.
- PostgreSQL y SQLite NO son intercambiables por copia de ficheros; se requiere conversión transaccional y revisión de tipos, claves y zonas horarias.

## Fase 1: inventario y backup, sin escrituras

### Precondiciones

1. Obtener acceso de consola **al interior del servicio** `psicologia-retenciones-piloto` para la base PostgreSQL, con `PSICOLOGIA_HOLDS_DATABASE_URL` configurada. La base rechaza conexiones externas de este conector porque no tiene IP permitidas.
2. Ejecutar el inventario: `python psicologia-api/tools/psc_pg_audit_readonly.py` desde raíz del proyecto, usando el código de esta rama en un entorno aislado. Salida: **conteos solamente**; no pacientes.
3. Disponer de `pg_dump` y `pg_restore` versión **18 o superior** y de espacio seguro. PostgreSQL de Render usa v18.
4. En el mismo contexto con acceso al PostgreSQL, ejecutar `python psicologia-api/tools/psc_pg_backup.py --output /ruta/privada/psc_YYYYMMDD.dump`. Debe emitir `BACKUP_VERIFIED`, bytes y SHA256.
5. Transferir el archivo a un almacén privado/cifrado **fuera del servicio gratuito** y comprobar que no se pierde al reiniciar; nunca añadir `.dump` a GitHub, correos ni páginas públicas.
6. Para el SQLite de producción, en el contexto del servicio principal con su disco montado: `python psicologia-api/tools/psc_sqlite_backup.py --output /ruta/privada/psc_YYYYMMDD.sqlite3`. Debe emitir `BACKUP_VERIFIED`; guardar la copia cifrada **fuera del mismo disco** y verificar SHA256.
7. Probar restauración en **bases aisladas**: `pg_restore` hacia una instancia temporal (sin apuntar a producción) y abrir la copia SQLite con `PRAGMA integrity_check`. Documentar verificación.
8. Exportar una instantánea consistente de los datos seleccionados: `python psicologia-api/tools/psc_pg_export_snapshot.py --output /ruta/privada/psc_snapshot.json`. El exportador valida todas las columnas y obtiene las ocho tablas en una sola transacción de solo lectura.
9. En el entorno de ensayo, convertir SIN sobrescribir el libro de compras: `python psicologia-api/tools/psc_import_snapshot_sqlite.py --input /ruta/privada/psc_snapshot.json --output /ruta/privada/psc_holds_staging.sqlite3`.
10. Ejecutar `python psicologia-api/tools/psc_verify_conversion.py --snapshot /ruta/privada/psc_snapshot.json --sqlite /ruta/privada/psc_holds_staging.sqlite3`; exigir `EXACT_RECONCILIATION_OK`. Este verificador compara incluso hashes y tokens cifrados sin mostrarlos.
11. En el servidor principal ejecutar `python psicologia-api/tools/psc_disk_preflight.py` y confirmar espacio libre, disco montado e integridad del SQLite actual.
12. Guardar los respaldos y las instantáneas que contengan datos privados en un almacén cifrado y fuera de GitHub/Render.


No hay acceso a shell remoto vía los conectores Render disponibles en esta conversación. Estos comandos **no se han ejecutado**; no declarar respaldo concluido mientras falten archivos verificables fuera de Render.

## Fase 2: diseño

- Añadir un almacenamiento SQLite transaccional en la API principal para los registros que siguen vigentes.
- Mantener claves lógicas y hashes de tokens; no regenerar enlaces Fonasa.
- Centralizar el acceso en una API autenticada; evitar conectar el piloto directamente al disco.
- Definir correspondencia de tipos PostgreSQL: UUID -> TEXT, timestamptz -> ISO8601 con zona, date -> YYYY-MM-DD, controles CHECK, llaves foráneas.
- Evitar colisiones entre `billing_orders`, `production_payments` y registros pilotos.
- Preservar la seguridad del intercambio entre servicios. No copiar contraseñas o secretos al repositorio.

## Fase 3: pruebas y corte

- Pruebas locales de migración con copia anónima o ficticia; ningún cargo/boleta real.
- Ensayar reserva simultánea para una hora y límite de tres atenciones por día.
- Ensayar reserva Fonasa, confirmación Calendar, correo y enlace de reagendar ya emitido.
- Verificar 4 servicios; comprobar que disponibilidad falla cerrada cuando una dependencia crítica está caída.
- Cierre controlado: pausa breve de nuevas reservas, instantánea final PG+SQLite, importar delta, reconciliar conteos/IDs, activar rutas nuevas y ensayar los flujos.
- Revertir solo con protocolo de reconciliación para no perder reservas realizadas después del cambio.
- **No borrar ni dejar vencer PostgreSQL** hasta que el sistema esté estable y respaldado.

## Prohibiciones de esta fase

- No editar ramas ligadas a despliegues de producción.
- No activar conectores alternativos con secretos de prueba.
- No cambiar el proveedor de pagos, los enlaces públicos ni boletas SII.
- No crear gastos nuevos.
- No abrir una red de acceso a la base públicamente para ejecutar el backup.

## Próxima barrera de avance

**Backup real verificado + prueba de restauración + inventario y reconciliación exacta de filas**. Sin estos tres elementos no pasar a escritura o migración real de datos.


## CI y límites comprobados

- Pull request borrador #42: trabajo fuera de las ramas publicadas.
- Workflow `PSC - Verify migration utilities` sin permisos de escritura y sin credenciales de producción.
- Las pruebas offline pueden comprobar la estructura, transacciones SQLite, enlaces Fonasa y datos cifrados ficticios; **NO acreditan una migración real**.
- La base PostgreSQL y el disco SQLite **siguen vivos y sin copiar** hasta obtener acceso al servidor.
- Hallazgo crítico: `google_calendar_writer.py` obtiene `psychology_google_oauth.encrypted_refresh_token` de PostgreSQL cuando el modo es OAuth. No cortar PostgreSQL hasta haber probado el inicio de sesión/autorización del calendario en la solución nueva.
