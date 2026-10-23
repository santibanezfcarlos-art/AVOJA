# Webpay Plus — Psicología Carlos Santibáñez

**Estado: BASE TÉCNICA EN DESARROLLO / SIN COBROS HABILITADOS.**

Se trabaja en la rama psicologia-webpay-sandbox. La rama publicada psicologia-carlos y el servicio Valhalla Office no se han modificado.

## Decisiones

- Prestación particular online: **$30.000 CLP** por sesión de **60 minutos**.
- Cupos: **máximo 3 sesiones por día**; agenda entre **09:00 y 20:00 Chile**, último inicio **19:00**.
- Retención de selección: **10 minutos**, con expiración validada por servidor.
- Medio de pago previsto: **Transbank Webpay Plus**, formulario alojado por Transbank, no iframe.
- Transbank y Google Calendar no deben recibir notas clínicas, diagnósticos ni motivo de consulta.
- Webpay de psicología tendrá **comercio y llaves propios**; no hereda los de Valhalla Office.

## Lo que ya existe en esta rama

El archivo psicologia-api/webpay_gateway.py incluye:

- Ambiente disabled por defecto.
- Ambiente integration con código y llave públicos oficiales para pruebas, sin cargos reales.
- production exige aprobación explícita y credenciales de psicología configuradas solo en Render.
- Creación de transacción REST Webpay Plus v1.2 para monto fijo de $30.000 CLP.
- Validación estricta del host de checkout, token, orden, sesión, monto, status=AUTHORIZED y response_code=0.
- Operaciones commit y status para que el futuro servicio de reservas las invoque de forma controlada.
- Tests sin solicitudes reales a Transbank en tests/test_webpay_gateway.py.

**Este módulo NO está conectado a botones públicos ni expone rutas de compra/retorno.**
No debe invocarse hasta tener la base de datos y el flujo completo.

## Pasos necesarios ANTES de habilitar el botón «Pagar»

1. Base de datos persistente **distinta de Valhalla**, con registro atómico de retenciones, pagos y reservas.
   - No usar el almacenamiento local del navegador.
   - No usar SQLite en Render Free (disco efímero).
   - No usar Render Postgres Free para pacientes reales (caduca a los 30 días y no tiene backups).
2. Reservar un cupo en transacción **antes** de crear pago, con bloqueo por día para evitar pasar de 3 sesiones y bloqueo único por fecha/hora.
3. Validar disponibilidad Google Calendar desde el servidor y cotejar el slot completo de 60 minutos.
   La fuente iCal actual puede tener retraso; para producción preferir FreeBusy/OAuth y escribir una reserva confirmada en un calendario dedicado.
4. Crear orden e intento de pago idempotentes en BD y llamar al método create_for_existing_hold.
5. Redirigir a Webpay mediante POST de token_ws; **no iframe**.
6. Retorno /api/webpay/return, manejar GET/POST:
   - Con token_ws y sin TBK_TOKEN: confirmar con commit bajo bloqueo por transacción.
   - Con TBK_TOKEN (incluso si trae también token_ws) o timeout sin token: registrar anulación/timeout, nunca hacer commit.
   - Cotejar los campos persistidos: orden, sesión, importe, respuesta autorizada.
7. Confirmar la reserva **una sola vez**. Actualizar disponibilidad y enviar correo sólo tras confirmación.
8. Si hay autorización después de expirar el cupo, registrar paid_unallocated y gestionar reubicación o devolución; **jamás perder el pago ni confirmar una hora ocupada**.
9. Conciliación periódica de estados dudosos, reglas de cancelación/reembolso y registro de auditoría.
10. Antes de producción: validar contrato Webpay Plus correspondiente al comercio que recibirá los pagos, pruebas y credenciales productivas; pruebas extremo a extremo.

## Variables previstas (servidor Render, nunca frontend ni GitHub)

- PSICOLOGIA_WEBPAY_MODE=disabled (solo cambiar en pruebas a integration)
- PSICOLOGIA_API_PUBLIC_URL=https://psicologia-carlos-calendar-api.onrender.com
- PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED=false
- Para producción únicamente tras activación comercial: PSICOLOGIA_WEBPAY_COMMERCE_CODE y PSICOLOGIA_WEBPAY_API_KEY
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

## Fuentes

- https://transbankdevelopers.com/documentacion/webpay-plus
- https://transbankdevelopers.com/referencia/webpay
- https://transbankdevelopers.com/documentacion/como_empezar
- https://render.com/docs/free
