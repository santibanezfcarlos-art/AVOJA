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
