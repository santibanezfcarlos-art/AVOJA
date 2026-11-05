# PSC — Corte PostgreSQL → SQLite (sin plan adicional)

Estado al 5 noviembre 2026: **CÓDIGO DESPLEGADO, CAMBIO DE MOTOR DESACTIVADO**.
No afirmar finalización ni borrar PostgreSQL hasta pasar todas las puertas.

## Topología y origen de datos
- API principal `psicologia-carlos-calendar-api`: único disco persistente `/var/data`; ledger Webpay y Fonasa locales en SQLite.
- Servicio `psicologia-retenciones-piloto`: no tiene disco; usa canal HMAC al servidor principal cuando se active el corte.
- PostgreSQL `psicologia-holds-piloto`: todavía utilizado por los flujos auxiliares anteriores.
- Respaldo de PostgreSQL cifrado en `/var/data/psc-postgres-backups`; copias relacionales restauradas en `/var/data/psc-postgres-recovery`.
- Nueva base funcional a sembrar al final: `/var/data/psc-state/holds.sqlite3` (no confundir con SQLite de pagos).

## Interruptores de migración (TODOS apagados actualmente)
En API principal:
- `PSC_SQLITE_SEED_EXPLICITLY_ALLOWED=true` solo para siembra final, una vez congeladas escrituras y obtenido respaldo fresco.
- `PSC_SQLITE_STATE_ENABLED=true` usa SQLite local para consultar ocupación.
- `PSC_SQLITE_BACKEND_RPC_ENABLED=true` abre endpoints HMAC limitados a operaciones Fonasa.

En servicio auxiliar:
- `PSC_FONASA_STATE_SOURCE=psc_disk`
- `PSC_FONASA_REBOOKING_MIGRATED=true` (habilita rutas Fonasa, calendario y arranque sin PG).
- `PSC_GOOGLE_OAUTH_STORAGE=psc_disk` (ya configurado en el servicio, credencial cifrada y caché local).

**No activar ningún interruptor de escritura de forma aislada.** Modo PG permanece hasta el corte completo.

## Puertas obligatorias para activar
1. Asegurar ausencia de compras/bonos en vuelo y congelar nuevos cambios a PG durante el corte.
2. Generar respaldo PostgreSQL final, comprobar SHA-256, descifrado y SQLite íntegra (8 tablas, datos y FK).
3. Comparar estado activo con Calendar, reservas Webpay y Fonasa locales; inspeccionar conflictos, enlaces sin redimir y pagos en conciliación.
4. Sembrar `holds.sqlite3` **una vez** desde el último snapshot verificado y contrastar filas. Nunca sobrescribir una base con transacciones nuevas.
5. Encender primero RPC/lectura en API principal y luego cliente auxiliar. Comprobar `health`, `busy`, disponibilidad pública y fuentes OAuth.
6. Simular dos reservas simultáneas, conflicto Fonasa, una única reprogramación, comprobación Google y recuperación tras reinicio (sin cargos ni correos a pacientes).
7. Completar compra real supervisada con Transbank cuando haya un comprador disponible y verificar banco → SQLite → Calendar/Meet → correos → boleta SII; no crear cobros sin autorización específica.
8. Retirar PostgreSQL de la configuración solo después de comprobar que ninguna ruta operativa lo necesita. Mantener respaldos y rollback reconciliados.

## Fallos seguros
- Si falta /var/data, base local o respuesta firmada: negar disponibilidad/operación; nunca ofertar un hueco no verificado.
- Reagendamiento Fonasa en dos fases: `PREPARED` reserva destino, Calendar confirma el evento, `COMPLETED` fija SQLite y redime enlace.
- Un resultado incierto de Google mantiene destino bloqueado; NO liberar automáticamente sin comprobar evento original.
- Ante divergir Calendar y SQLite, detener el corte y reconciliar. No volver a PostgreSQL si ya existen transacciones exclusivas en SQLite sin traslado explícito.

## Pruebas hechas en ramas
- CI SQLite principal: GitHub Actions run `42064491892`, success.
- CI puente Fonasa: GitHub Actions run `42064785888`, success.
- Pruebas son aisladas; no sustituyen corte completo ni compra de prueba.
