# Historial privado de boletas

La compra de prueba recoge RUT, nombre, dirección, correo y teléfono antes de abrir Webpay. El servidor asigna detalle y monto; no confía en valores enviados por el navegador. Los datos del receptor no se envían a Transbank ni se guardan en localStorage. El historial persiste en SQLite y distingue pagos aprobados, rechazados, cancelados y sin confirmar.

## Configuración en Render

Servicio `psicologia-carlos-calendar-api`, disco de 1 GB montado en `/var/data`, una instancia y un worker.

- `PSICOLOGIA_BILLING_DB=/var/data/billing.sqlite3`
- `PSICOLOGIA_BILLING_ADMIN_USER=carlos`
- `PSICOLOGIA_BILLING_ADMIN_PASSWORD`: contraseña única de al menos 16 caracteres, guardada exclusivamente en Environment de Render. No escribir su valor en el repositorio.
- Alternativamente, `PSICOLOGIA_BILLING_ADMIN_HASH` acepta un hash Werkzeug scrypt/pbkdf2 y tiene prioridad sobre la contraseña. Para generarlo localmente: `python -c 'import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass.getpass()))'`.

Guardar y desplegar después de configurar. `/api/billing/config` debe devolver `ready: true`. Si falta almacenamiento persistente o acceso privado, el nuevo formulario detiene el pago; no guarda datos en disco efímero.

## Uso

`https://psicologia-carlos-calendar-api.onrender.com/admin/boletas`

Autenticación HTTP Basic sobre HTTPS. El navegador pide usuario y contraseña. No compartir enlaces que incluyan credenciales; usar un perfil de navegador privado y cerrarlo al terminar en equipos compartidos.

Filtros por ambiente, estado, fechas, nombre, RUT y orden. Copiar campos individuales o ficha completa; exportar CSV. El formulario exige un folio y fecha para marcar una boleta realmente emitida. Repetir la misma operación es idempotente; un folio no puede pertenecer a dos compras. Se mantiene auditoría de los registros de emisión.

El ambiente actual es **integración de Transbank**. No hay cobros ni reservas reales ni emisión SII. Seleccionar “Pruebas” para ver estas compras. Las compras históricas anteriores al cambio no se pueden reconstruir sin datos que nunca fueron guardados. La página independiente `prueba-webpay.html` sigue admitiendo pruebas técnicas sin receptor y no agrega entradas al historial.

La futura integración de producción deberá llamar explícitamente al registro usando `environment='production'` y verificar la confirmación del proveedor. No basta cambiar una variable para habilitar pagos reales. No se registran ni almacenan datos de tarjetas.

## Respaldo

Descargar periódicamente el CSV desde el panel privado y guardarlo de forma segura. Para un respaldo completo consistente de SQLite, usar `sqlite3.Connection.backup()` desde la Shell de Render y transferir el archivo fuera del servidor; copiar directamente una base activa o restaurar snapshots de disco no sustituye un respaldo nativo. El CSV sirve para consulta y no es una restauración completa de la base.

## Validación

`python -m unittest discover -s psicologia-api -p test_billing.py -v`

Pruebas offline con datos ficticios y Transbank simulado: validación del receptor, persistencia, privacidad, CORS, CSRF, pago completo, separación de ambientes, folios duplicados e idempotencia. El retorno que no se puede confirmar permanece sin confirmar y requiere revisión; nunca se convierte automáticamente en boleta pendiente.
