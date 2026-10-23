# Webpay Plus — Psicología Carlos Santibáñez

**Estado: BASE TÉCNICA CONSTRUIDA Y PROBADA EN SANDBOX / SIN COBROS HABILITADOS.**

Se trabaja en la rama psicologia-webpay-sandbox. La rama publicada psicologia-carlos y el servicio Valhalla Office no se han modificado.

## Decisiones

- Prestación particular online: **$30.000 CLP** por sesión de **60 minutos**.
- Cupos: **máximo 3 sesiones por día**; agenda entre **09:00 y 20:00 Chile**, último inicio **19:00**.
- Retención de selección: **10 minutos**, con expiración validada por servidor.
- Medio de pago previsto: **Transbank Webpay Plus**, formulario alojado por Transbank, no iframe.
- Transbank y Google Calendar no deben recibir notas clínicas, diagnósticos ni motivo de consulta.
- Webpay de psicología tendrá **comercio y llaves propios**; no hereda los de Valhalla Office.

## Lo que ya existe en esta rama

Los archivos psicologia-api/webpay_gateway.py y psicologia-api/booking_api.py incluyen:

- Ambiente disabled por defecto.
- Ambiente integration con código y llave públicos oficiales para pruebas, sin cargos reales.
- production exige aprobación explícita y credenciales de psicología configuradas solo en Render.
- Creación de transacción REST Webpay Plus v1.2 para monto fijo de $30.000 CLP.
- Validación estricta del host de checkout, token, orden, sesión, monto, status=AUTHORIZED y response_code=0.
- Operaciones commit y status para que el futuro servicio de reservas las invoque de forma controlada.
- Tests sin solicitudes reales a Transbank en tests/test_webpay_gateway.py.

**El módulo contiene las rutas /api/bookings/hold, /api/bookings/checkout y /api/webpay/return,
pero están DESACTIVADAS por defecto y solo existen en la rama sandbox.**
También se agregó un formulario con botón Webpay deshabilitado a la página de esa rama.
La web pública y Valhalla Office permanecen sin cambios.

La base de datos está definida en psicologia-api/schema.sql; no se ha creado una
base productiva ni se ha ejecutado el SQL contra información real.
El ambiente requiere PSICOLOGIA_BOOKINGS_ENABLED=true, una base PostgreSQL
válida y modo Webpay integration/production; no se habilita automáticamente
por ingresar las credenciales.

## Pasos necesarios ANTES de habilitar el botón «Pagar»

1. Crear base de datos persistente **distinta de Valhalla**, y aplicar psicologia-api/schema.sql para retenciones, pagos y reservas.
   - No usar el almacenamiento local del navegador.
   - No usar SQLite en Render Free (disco efímero).
   - No usar Render Postgres Free para pacientes reales (caduca a los 30 días y no tiene backups).
2. El bloqueo por día y el control de tres cupos se implementaron y aprobaron pruebas de concurrencia con PostgreSQL efímero en GitHub Actions. Validar recuperación, expiración y casos bancarios extremos antes de producción.
3. Validar disponibilidad Google Calendar desde el servidor y cotejar el slot completo de 60 minutos.
   La fuente iCal actual puede tener retraso; para producción preferir FreeBusy/OAuth y escribir una reserva confirmada en un calendario dedicado.
4. La creación de orden e intento de pago está preparada en PostgreSQL; completar reintentos y reconciliación de operaciones ambiguas antes de producción.
5. El formulario de la rama sandbox está preparado para redirigir con POST de token_ws; **no iframe**. Requiere desplegar esa rama solo después de validar la configuración.
6. El retorno /api/webpay/return está implementado para GET/POST. Probar extremo a extremo con tarjetas de integración y completar conciliación manual/automática:
   - Con token_ws y sin TBK_TOKEN: confirmar con commit bajo bloqueo por transacción.
   - Con TBK_TOKEN (incluso si trae también token_ws) o timeout sin token: registrar anulación/timeout, nunca hacer commit.
   - Cotejar los campos persistidos: orden, sesión, importe, respuesta autorizada.
7. La confirmación en BD se implementó para una sola autorización; falta integrar correo real, registro en calendario y panel administrativo seguro.
8. Si hay autorización después de expirar el cupo, registrar paid_unallocated y gestionar reubicación o devolución; **jamás perder el pago ni confirmar una hora ocupada**.
9. **Pendiente:** conciliación periódica automática de estados dudosos, reglas de cancelación/reembolso, panel de revisión de pagos y registro completo de auditoría.
10. Antes de producción: validar contrato Webpay Plus correspondiente al comercio que recibirá los pagos, pruebas y credenciales productivas; pruebas extremo a extremo.

## Variables previstas (servidor Render, nunca frontend ni GitHub)

- PSICOLOGIA_WEBPAY_MODE=disabled (en pruebas integration; producción solo con aprobación final)
- PSICOLOGIA_API_PUBLIC_URL=https://psicologia-carlos-calendar-api.onrender.com
- PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED=false
- Para producción únicamente tras activación comercial: PSICOLOGIA_WEBPAY_COMMERCE_CODE y PSICOLOGIA_WEBPAY_API_KEY
- PSICOLOGIA_BOOKINGS_ENABLED=false (habilitar solo tras pruebas con BD).
- PSICOLOGIA_DATABASE_URL= (PostgreSQL persistente propio y respaldado).
- Base de datos de reservas: URL de conexión privada, cifrado de tokens y políticas de respaldo.

## Diagrama de estados

AVAILABLE -> HELD -> PAYMENT_PENDING -> CONFIRMED si aprobado;
HELD -> AVAILABLE si expira sin pago;
PAYMENT_PENDING -> RECONCILIATION_REQUIRED si no hay respuesta;
APPROVED_BUT_SLOT_LOST -> PAID_UNALLOCATED si el cupo caducó o existe conflicto.

## Pruebas

Ejecutar desde la carpeta psicologia-api:

    python -m unittest discover -s tests -p 'test_webpay_gateway.py' -v

Todos los tests simulan respuestas de Transbank, no hacen cobros.


## Cuando lleguen las credenciales de Transbank

En **Render > psicologia-carlos-calendar-api > Environment**, agregar las variables
PSICOLOGIA_WEBPAY_COMMERCE_CODE y PSICOLOGIA_WEBPAY_API_KEY. NO enviarlas al chat,
no escribirlas en GitHub, ni en archivos HTML. La URL de retorno técnica será
https://psicologia-carlos-calendar-api.onrender.com/api/webpay/return y el sitio
https://psicologia-carlos-demo.onrender.com

Dejar PSICOLOGIA_WEBPAY_MODE=disabled, PSICOLOGIA_BOOKINGS_ENABLED=false
y PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED=false hasta que el circuito de reserva
y conciliación haya superado pruebas completas y exista PostgreSQL con respaldo.
El solo ingreso de las claves NO empieza a cobrar.

Para pruebas con transacciones de integración usar PSICOLOGIA_WEBPAY_MODE=integration,
sin las claves reales, y una base de datos independiente de ensayos. No compartir
credenciales ni datos de tarjetas reales durante estas pruebas.

Para el encendido productivo se exigirá autorizar explícitamente:
PSICOLOGIA_WEBPAY_MODE=production, PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED=true,
PSICOLOGIA_BOOKINGS_ENABLED=true (solo tras QA). Antes se deberá desplegar
la rama validada, configurar BD propia, proteger administración y agregar un
procedimiento de conciliación y revisión de pagos.

## Pruebas automáticas

GitHub Actions: .github/workflows/psicologia-webpay-tests.yml
Incluye pruebas de autorización Webpay simulada, sintaxis Python y
retenciones concurrentes sobre PostgreSQL efímero. No llama a Transbank,
no registra pagos reales ni usa calendarios de pacientes.

## Fuentes

- https://transbankdevelopers.com/documentacion/webpay-plus
- https://transbankdevelopers.com/referencia/webpay
- https://transbankdevelopers.com/documentacion/como_empezar
- https://render.com/docs/free
