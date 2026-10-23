# Escritura en Google Calendar — Psicología Carlos Santibáñez

## Estado al 23-10-2026

Implementado (en rama **psicologia-webpay-produccion-preparacion**):
- `google_calendar_writer.py`: obtiene disponibilidad **directamente de Google API**,
  expande instancias recurrentes, considera eventos de todo el día y eventos ocupados;
  crea eventos con ID estable a partir del UUID de la retención y cancela únicamente
  eventos que identifica como propios.
- `booking_holds.py`: opción `PSICOLOGIA_HOLDS_CALENDAR_POLICY=google_api`
  para verificar en directo (no se ha activado).
- `pilot_app.py`: muestra flags de configuración sin exponer claves ni eventos.
- `test_google_calendar_writer.py`: pruebas con Google simulado, sin escrituras reales.

**NO se ha autorizado Google Cloud, configurado credenciales ni escrito un
evento real.** Los sitios publicados y el checkout Webpay siguen sin cambios.
`PSICOLOGIA_CALENDAR_WRITE_ENABLED=false` queda configurado en Render.
Nunca cambiarlo a true hasta diseñar la vinculación de reservas pagadas,
conciliación y una prueba explícitamente autorizada en calendario de ensayo.

## Diferencia con la conexión ya instalada

La web usa `GOOGLE_CALENDAR_ICS_URL` (privado, **solo lectura**).
El backend nuevo necesita identidad con permisos de Google Calendar API.
Conectar ChatGPT a Google Calendar no otorga credenciales reutilizables a Render.

## Cómo habilitar acceso de lectura/escritura para el backend

**Paso 1.** En un proyecto propio de Google Cloud, habilitar Google Calendar API:
https://console.cloud.google.com/apis/library/calendar-json.googleapis.com

**Paso 2.** Crear una cuenta de servicio (ej.: `psicologia-calendar`) en:
https://console.cloud.google.com/iam-admin/serviceaccounts
El correo creado tendrá forma `nombre@proyecto.iam.gserviceaccount.com`.
Sin concederle permisos innecesarios de IAM en Cloud.

**Paso 3.** En esa cuenta de servicio, crear una credencial JSON desde
`Claves → Agregar clave → Crear clave nueva → JSON`, si las políticas
del proyecto lo permiten. Tratar el JSON descargado como **contraseña privada**:
no enviarlo al chat, no adjuntarlo a GitHub, no alojarlo en la web y no publicarlo.
Si Google Cloud impide crear claves de servicio, detener este recorrido y
preparar OAuth de usuario con renovación segura en backend, en lugar de
debilitar la política de organización.

**Paso 4.** En Google Calendar de la cuenta propietaria del calendario usado
por la web, buscar `Configuración y uso compartido → Compartido con` y
agregar **el correo de la cuenta de servicio**, otorgándole permiso para
**hacer cambios en eventos y ver sus detalles**.
En `Integrar calendario`, localizar el **ID del calendario**, no la URL
privada .ics ni el ID de la cuenta de servicio. Usar el calendario ya elegido
por la web; si se opta por otro calendario, hay que revisar conflictos con
el calendario original y reconciliar disponibilidad entre ambos.

**Paso 5.** Abrir **solamente** el backend piloto:
https://dashboard.render.com/web/srv-dbdrmion74is73em6nig/env

Agregar en Environment:
- `PSICOLOGIA_GOOGLE_CALENDAR_ID` = ID encontrado en Google Calendar
- `PSICOLOGIA_GOOGLE_SERVICE_ACCOUNT_JSON` = contenido completo del
  JSON privado de servicio (sin comillas añadidas, sin convertir a .ics)
- **mantener** `PSICOLOGIA_CALENDAR_WRITE_ENABLED=false`
- **mantener** `PSICOLOGIA_HOLDS_ENABLED=false`
- **mantener** `PSICOLOGIA_WEBPAY_MODE=disabled`
- **mantener** `PSICOLOGIA_CHECKOUT_MODE=disabled`
- **por ahora** `PSICOLOGIA_HOLDS_CALENDAR_POLICY=synthetic`

Guardar y desplegar. Comprobar en
https://psicologia-retenciones-piloto.onrender.com/health
que `google_api_configured=true` y `google_api_write_enabled=false`.
El flag no demuestra aún permisos efectivos ante Google. Antes de usar datos
reales será necesario verificar lectura autenticada, luego la escritura
autorizada en un calendario de prueba sin pacientes.

## Garantías preparadas

1. Los eventos nuevos no contienen nombre, RUT, correo, diagnóstico ni invitados.
2. ID de evento determinístico `psc<uuidhex>`, para evitar reservas duplicadas
   si Transbank repite el retorno.
3. Tras HTTP 409, consulta el evento y lo acepta **solo** si pertenece a la
   misma retención, servicio y horario.
4. Para eliminar, consulta primero la marca privada de propiedad del evento.
5. Solicitudes Google exclusivamente desde servidor con `calendar.events`.
6. Horarios con zona `America/Santiago`; eventos libres/transparencia no
   bloquean; eventos privados ocupados sí.
7. Si Google no responde o devuelve datos incompletos, se bloquea la
   confirmación (comportamiento seguro). En caso de pago autorizado
   y error en Calendar, marcar `PAID_UNALLOCATED` y conciliar / devolver
   antes de prometer cita (aún pendiente de implementar).
8. No hay endpoints externos para crear o borrar eventos. La API de pagos
   no llama al nuevo módulo.

## Pendientes para producción

- Permisos reales de lectura/escritura concedidos y probados con Google.
- Vincular la compra verificada en PostgreSQL (pago aprobado, monto,
  orden e idempotencia) con creación de evento.
- Actualizar atómicamente estados de reserva y pago (no se puede hacer una
  transacción distribuida con Google; usar compensación y conciliación).
- Definir cancelación y eventual devolución de dinero, según política del servicio.
- Enviar confirmación al paciente sin contenido clínico sensible.
- Validar política de privacidad y seguridad de claves/credenciales en Render.

Fuentes oficiales:
https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
https://developers.google.com/workspace/calendar/api/v3/reference/events/delete
https://support.google.com/calendar/answer/37082?hl=es

## Flujo de ensayo integrado con Transbank — 23 de octubre de 2026

Módulos:
- `demo_checkout.py`: checkout cerrado por código de acceso y correo de
  prueba autorizado; usa el **ambiente oficial de integración** de Webpay Plus.
  Retorno `/demo/return`, nunca el endpoint de cobro productivo.
- `demo_notifications.py`: correos separados para profesional y paciente
  ficticio, con invitación adjunta `.ics`, enviados por Resend solo a correo
  previamente autorizado en el servidor.
- `schema_holds.sql`: tablas `psychology_demo_payments` y
  `psychology_demo_notifications`, con estados y control de idempotencia.
- `test_demo_checkout_postgres.py`: ensayo aprobado/rechazado, comprobación
  de reintento, conflictos y calendario con escritura desactivada,
  utilizando PostgreSQL temporal real y Transbank/Google simulados.

El proyecto de pruebas vive en
https://psicologia-retenciones-piloto.onrender.com/demo
y **NO** ha sido conectado a la página pública `pscsantibanez.cl`.

Variables en Render del **servidor piloto**, no del frontend:
- `PSICOLOGIA_API_PUBLIC_URL=https://psicologia-retenciones-piloto.onrender.com`
- `PSICOLOGIA_PILOT_WEBPAY_TEST_ENABLED=true`
- `PSICOLOGIA_HOLDS_ISOLATED_PILOT=true`
- `PSICOLOGIA_HOLDS_CALENDAR_POLICY=google_api`
- `PSICOLOGIA_CALENDAR_WRITE_ENABLED=true` únicamente para eventos
  claramente titulados **PRUEBA TÉCNICA** en el calendario autorizado.
- `PSICOLOGIA_DEMO_ACCESS_CODE`: clave de ensayo, guardada solo en Render
  y facilitada al propietario, NUNCA publicada en GitHub.
- `PSICOLOGIA_PILOT_TEST_EMAIL` y `PSICOLOGIA_PILOT_PRO_EMAIL`: ambos
  **idénticos al correo del propietario** durante estas pruebas.
- `PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED=false` hasta configurar
  el proveedor de correo en Render.
- `PSICOLOGIA_PILOT_RESEND_API_KEY`: únicamente en Environment de Render,
  generada por el titular desde https://resend.com/api-keys.
- `PSICOLOGIA_WEBPAY_MODE=disabled` y
  `PSICOLOGIA_CHECKOUT_MODE=disabled`: los cobros de producción continúan
  apagados; el ensayo utiliza solamente credenciales públicas de integración.

Para habilitar los correos de ensayo, guardar la clave privada bajo
`PSICOLOGIA_PILOT_RESEND_API_KEY`, comprobar que el dominio remitente
`pscsantibanez.cl` esté validado en esa misma cuenta Resend y cambiar
`PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED=true` únicamente en el servicio
piloto. Mantener los dos destinatarios limitados al propietario.

El pago autorizado no garantiza una cita: solo pasa a `CONFIRMED`
cuando Google verifica el evento creado. Ante error conserva
`CALENDAR_PENDING` o `RECONCILIATION_REQUIRED`. Un pago rechazado
cancela el hold, sin Google ni correos. Si la misma orden retorna dos
veces, no repite Calendar ni envíos ya marcados.

**Atención:** Render PostgreSQL Free es efímero; el módulo es únicamente
un piloto funcional, no apto para pacientes o dinero real. La transición
a producción requiere persistencia con backups, controles de acceso,
registro privado de paciente y conciliación confiable.
