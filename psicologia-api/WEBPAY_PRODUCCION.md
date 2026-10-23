# Psicología — preparación Webpay Plus de producción

**Estado: código preparatorio en rama separada. NO se cobra dinero real.**
Sitio principal: https://pscsantibanez.cl
API de pruebas: https://psicologia-carlos-calendar-api.onrender.com
Módulo preparatorio: `psicologia-api/webpay_gateway.py` (no registrado en Flask)

## Referencia del sistema existente

Se revisó `valhalla/payments.py` y `valhalla/TRANSBANK.md` en la rama
`valhalla-demo` de este mismo repositorio. El patrón que reutilizamos es:
- Producción e integración con credenciales y hosts distintos.
- Precio definido por el servidor; orden, sesión y monto contrastados contra una orden persistente.
- Las credenciales privadas solo están en Render; nunca en repositorio, HTML ni JavaScript.
- La vuelta desde Transbank no acredita nada por sí sola.
- Estado de conciliación para cortes de Internet, reintentos y pagos tardíos.
- Confirmación idempotente, para no duplicar reservas ni cobrar/acreditar dos veces.
- Posibilidad de pausar ventas sin perder el historial bancario.

**Diferencia de negocio**: Valhalla acredita paquetes de horas. Psicología ofrece reservas
para una fecha, hora y duración concretas y exige comprobar la disponibilidad de Google Calendar.

## Código de comercio recibido

El correo confirma que existe un **código comercial productivo**, pero no aporta
por sí solo una clave de API privada ni confirma que la validación bancaria haya terminado.
No copiar el código, ni especialmente la llave privada, a GitHub o a un mensaje de chat.
Guardar ambos exclusivamente en Environment de Render cuando corresponda.

## Variables previstas (NO CONFIGURAR AÚN EN EL SERVICIO ACTIVO)

- `PSICOLOGIA_CHECKOUT_MODE=disabled` (valor por omisión)
- `PSICOLOGIA_PRODUCTION_APPROVED=false` hasta terminar validación de Transbank
- `PSICOLOGIA_PRODUCTION_COMMERCE_CODE`: código de 12 dígitos entregado al comercio
- `PSICOLOGIA_PRODUCTION_API_KEY`: llave privada de producción obtenida de Transbank
- `PSICOLOGIA_API_PUBLIC_URL=https://psicologia-carlos-calendar-api.onrender.com`

Estas variables son **distintas** de `PSICOLOGIA_WEBPAY_MODE` y de la clave de pruebas
del componente `webpay_demo.py`; esa demostración debe seguir siendo simulada.

## Bloqueadores antes de aceptar pacientes

1. **BD duradera propia:** PostgreSQL de Psicología o SQLite con disco persistente
   exclusivo. Nunca usar la BD de Valhalla. Tabla de horas retenidas, pagos e intentos
   y estado de cada orden. Bloqueos transaccionales para evitar dobles reservas.
2. **Retención real de fecha y hora:** máximo 3 atenciones por día,
   duración 45/90/45 minutos, solapamientos, expiración de la retención y estados
   HELD / PAYMENT_PENDING / CONFIRMED / REJECTED / RECONCILIATION_REQUIRED /
   PAID_UNALLOCATED.
3. **Google Calendar con escritura autenticada:** hoy la API consulta el enlace
   privado iCal y **solo lee** ocupaciones. No puede crear eventos ni confirmar
   citas. Se necesita integración autorizada para crear/cancelar eventos.
4. **Conciliación:** confirmar pago con Transbank desde el backend,
   cotejando código de comercio, orden, sesión, monto, estado `AUTHORIZED`
   y `response_code=0`, y marcar una sola vez el intento. En caso de
   pago autorizado sin cupo verificable, dejar `PAID_UNALLOCATED` para
   devolución/gestión humana: nunca prometer hora inexistente.
5. **Identidad y correos:** confirmación de paciente, mensajes enviados por
   `contacto@pscsantibanez.cl`, boleta electrónica real únicamente
   después de completar el flujo tributario con el SII.
6. **Pruebas y habilitación:** probar transacciones autorizadas, rechazadas,
   anuladas, retornos duplicados y cierres del navegador en integración.
   Validar con Transbank el paso a producción y las credenciales;
   activar cobros solo tras revisión, con autorización explícita del titular.

## Pruebas sin operaciones bancarias

```bash
cd psicologia-api
python -m unittest test_webpay_gateway.py
```

Estas pruebas usan diccionarios ficticios. No llaman a Transbank.


## Piloto de retención PostgreSQL (incorporado en esta rama, apagado)

Se implementaron `booking_holds.py`, `schema_holds.sql` y
`test_booking_holds.py`. Registro aislado en Flask con estos endpoints:

- `GET /api/holds/pilot/config`: muestra si el piloto está habilitado.
- `POST /api/holds/pilot/create`: retiene una fecha/hora durante **600 segundos**;
  exige una clave administrativa HTTP `X-Pilot-Key`, que **jamás** debe
  exponerse en el navegador de los pacientes. Body JSON:
  `{"service":"psicoterapia","date":"AAAA-MM-DD","time":"HH:MM"}`.
- `POST /api/holds/pilot/cancel`: recibe `hold_id` y `cancel_secret`
  más `X-Pilot-Key`, libera una retención propia que aún esté activa.

**No está habilitado para pacientes.** Los registros de prueba no contienen
nombre, RUT, correo, historial médico ni datos de tarjetas. Los filtros
de fechas/servicios y los precios se deciden en el servidor. El lock
`psychology_booking_days FOR UPDATE` serializa reservas simultáneas;
se verifica un límite de tres holds vigentes por día y los solapamientos.
Los estados HELD expiran a 10 minutos sin depender de un proceso de fondo,
porque las lecturas y escrituras consultan `expires_at` en SQL.

Nunca se crean eventos en Google Calendar ni se confirma un pago real.
La consulta de iCal es de solo lectura y puede tener retraso incluso
descargándola fresca: **falta integración de escritura para producción**.

Requisitos para utilizarlo en **un entorno aislado de pruebas**:
1. PostgreSQL exclusivo de Psicología, sin conexión a Valhalla.
2. Aplicar `schema_holds.sql` mediante un cliente SQL autenticado antes
   de iniciar pruebas; este módulo **no** ejecuta migraciones al arrancar.
3. Configurar variables privadas:
   `PSICOLOGIA_HOLDS_DATABASE_URL`, `PSICOLOGIA_HOLDS_PILOT_KEY`
   (mínimo 32 caracteres) y `PSICOLOGIA_HOLDS_ENABLED=true`.
   Predeterminado: deshabilitado.
4. Ejecutar pruebas locales: `python -m unittest test_booking_holds test_webpay_gateway`.
5. Verificar con PostgreSQL real que las solicitudes simultáneas no
   permiten duplicados, que la retención expira y que la cancelación se
   aplica correctamente. Las pruebas unitarias escritas no reemplazan
   esta prueba de concurrencia real.

Render Postgres Free está pensado para pruebas y caduca a los 30 días,
sin respaldo automático. No almacenar reservas de pacientes allí.

## Qué NO hace el nuevo módulo

No registra rutas Flask, no reemplaza `webpay_demo.py`, no cambia la web,
no crea reservas, no emite boletas, no escribe Google Calendar y **no hace cobros**.
Solo encapsula el futuro cliente seguro de Webpay Plus.

## Verificación técnica realizada el 23 de octubre de 2026

Se creó en Render PostgreSQL **psicologia-holds-piloto** (plan Free,
región Virginia, ID `dpg-dbdrg6fb9u1s73b9coag-a`). Está disponible,
pero **NO está vinculado** al sitio principal ni al backend público.
Su fecha de expiración informada por Render es el **22 de noviembre de 2026**.
No almacenar pacientes ni pagos en esa instancia efímera.

GitHub Actions, rama `psicologia-webpay-produccion-preparacion`,
ejecución `40013841627`: **20 pruebas superadas**, entre ellas 6
ejecutadas contra un contenedor PostgreSQL 18 real (sin Transbank),
verificando 2 solicitudes simultáneas a una misma hora, límite de 3,
expiración y cancelación con secreto. Estas pruebas no han utilizado
ni inicializado el PostgreSQL de Render.

**Pendiente de configuración privada por el propietario en Render:**
obtener la URL interna del nuevo PostgreSQL, ponerla solo en el
entorno de un backend de pruebas separado, migrar el esquema y preparar
la clave piloto. La conexión externa de Render está bloqueada mediante
allowlist vacía; no abrirla públicamente para facilitar la configuración.
Se recomienda no utilizar la instancia Free para producción ni
modificar las credenciales del backend publicado.
