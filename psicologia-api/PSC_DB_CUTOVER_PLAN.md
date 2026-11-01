# PSC — plan técnico de corte PostgreSQL → SQLite persistente

**Estado:** proyecto aislado, no publicado. PostgreSQL continúa siendo necesario.  
**Vencimiento Render:** 2026-11-22.  
**Ramas de trabajo:** PR #42 (exportación y ensayos desde piloto), PR #43 (API principal).

## Recursos y límites

- `psicologia-carlos-calendar-api` es el único servicio con disco persistente `/var/data` (1 GB).
- `psicologia-retenciones-piloto` es un servicio independiente sin acceso directo al disco.
- Una copia de PostgreSQL fue convertida a SQLite y reconciliada: **132 filas, 8 tablas**.
- Existe copia cifrada externa validada por SHA-256 en equipo Windows del propietario.
- El ensayo de restauración en una base temporal finalizó `PSC_ISOLATED_RESTORE_OK`.
- No hay prueba de restauración **del archivo cifrado en Windows**, ni `pg_dump` nativo validado.

## Dependencias reales que requieren adaptación

| Función | Fuente actual | Cambio necesario |
|---|---|---|
| Ocupación de retenciones `/internal/psicologia/demo/busy` | PostgreSQL en servicio piloto + Google Calendar | Mover lectura y escritura transaccional de holds a API principal. **No omitir Google**. |
| Google OAuth | `psychology_google_oauth` en PostgreSQL | Recuperar refresh cifrado con la misma clave Fernet y ejecutar prueba Google real sin escribir eventos. |
| Reagendamiento Fonasa | `psychology_fonasa_bookings` + `psychology_booking_holds` | Mantener hash del enlace, clave generadora, cupos, bloqueo, canje único y URL pública anterior. |
| Reagendamiento piloto | `psychology_reschedule_links/audit` | Conservar estados y reintentos; no restablecer enlaces usados. |
| Reservas particulares reales | `production_payments` en SQLite principal | **No tocar**, se usan como fuente independiente de reservas confirmadas y pagos. |
| Bonos Fonasa e intake | `fonasa_intake` en SQLite principal | Conciliar por identificador; evitar doble entrada de una misma reserva. |
| Transbank y boletas SII | SQLite principal | Mantener lógica, estados y folios; sin cargos en pruebas. |

## Por qué el endpoint nuevo NO sirve aún como reemplazo

`/internal/psc/migration/holds-only` en PR #43:
- está deshabilitado sin `PSC_MIGRATION_SHADOW_READ_ENABLED=true`;
- requiere clave HMAC separada y archivo SQLite persistente validado;
- devuelve solamente retenciones, sin ocupación Google Calendar;
- no contiene operaciones de escritura, generación de Meet ni confirmación de pagos.

No conectar a la disponibilidad de pacientes hasta incorporar un proveedor único que combine reservas, retenciones y calendarios con política **fail-closed** y una operación atómica de creación.

## Reglas de la conversión definitiva

1. Crear un archivo SQLite específico para registros de holds/reagendamientos, no mezclar directamente con `billing_orders`. Debe residir en el mismo disco pagado.
2. Convertir `UUID` a `TEXT` y `timestamptz` a ISO8601 UTC con zona explícita; validar unicidad y llaves foráneas.
3. Activar `PRAGMA foreign_keys=ON`, `busy_timeout` y transacciones `BEGIN IMMEDIATE` para reservas simultáneas.
4. Rediseñar los handlers del piloto para usar RPC HMAC con API principal, o trasladar sus funciones al mismo proceso. No usar montaje de disco compartido.
5. Cuando Google Calendar no responde, no ofrecer horas libres.
6. Cuando la base no responde, no confirmar una cita, cobrar ni enviar una confirmación falsa.
7. Reagendamiento: validar enlace usado/caducidad antes de mover Calendar; mantener compensación ante fallo de escritura, y conciliación manual si Google ya confirmó y SQLite falló.
8. Mantener mismo ID del evento Calendar, hashes, nombres y correos existentes; no exponer datos personales en logs de migración.
9. Usar feature flags, una ventana corta de pausa de reservas, recuento y checksum de último delta, pruebas de extremo a extremo y reversión documentada.
10. No apagar PostgreSQL hasta pasar prueba real de Calendar, 4 flujos de reserva y antiguos enlaces.

## Próximas verificaciones (sin cambios en producción)

1. Ejecutar `psc_render_parity_runner.py` en Shell pagada para comparar ocupación PostgreSQL original con SQLite de ensayo, a la misma hora de captura.
2. Comprobar si `pg_dump` / `pg_restore` versión compatible están disponibles, generar respaldo nativo y ensayar restauración en instancia aislada.
3. Verificar localmente la contraseña y extracción del respaldo cifrado en **equipo Windows**, sin compartir la contraseña.
4. Preparar adaptadores de escritura y autenticación de Calendar + Fonasa en ramas aisladas, pruebas de concurrencia.
5. Planificar corte real solo después de superar todos los controles anteriores.

**Ninguna acción en este documento autoriza modificar recursos de Valhalla Office ni la emisión tributaria real.**
