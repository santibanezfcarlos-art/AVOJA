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

## Qué NO hace el nuevo módulo

No registra rutas Flask, no reemplaza `webpay_demo.py`, no cambia la web,
no crea reservas, no emite boletas, no escribe Google Calendar y **no hace cobros**.
Solo encapsula el futuro cliente seguro de Webpay Plus.
