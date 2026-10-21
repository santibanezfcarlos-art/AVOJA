# Webpay Plus REST: pruebas y producción

## Activación de producción tras aprobación de Transbank

El código admite producción, pero las credenciales y el cambio de ambiente se configuran **solo en Render**, nunca en el repositorio, en el panel ni en los archivos de ejemplo.

En Render → servicio → Environment → Edit configura:

| Variable | Valor |
|---|---|
| `VALHALLA_WEBPAY_MODE` | `production` |
| `VALHALLA_WEBPAY_COMMERCE_CODE` | Código productivo de 12 dígitos confirmado por Transbank |
| `VALHALLA_WEBPAY_API_KEY` | Clave privada productiva entregada por Transbank |
| `VALHALLA_PUBLIC_URL` | `https://valhallaoffice.cl` |

Espera a que el despliegue de esta versión termine antes de guardar el modo `production`. El inicio rechaza producción sin credenciales válidas o sin origen HTTPS. Una preferencia anterior de pruebas guardada en SQLite no reemplaza el ambiente productivo seleccionado en Render. Las credenciales se usan únicamente en el servidor; no se incluyen en respuestas, notas, errores ni historial de operaciones.

1. Inicia sesión con la cuenta administradora propietaria y abre Administración → Transbank.
2. Las ventas normales comienzan pausadas. Pulsa **Realizar compra de validación de $50**.
3. Acepta las normas y revisa la confirmación, que indica expresamente que es un cobro real y no acredita sesiones.
4. Paga con una tarjeta real en el formulario alojado de Transbank. La aplicación no recibe datos de tarjeta. Este paso lo realiza el propietario, no las pruebas automatizadas.
5. Vuelve a Administración → Transbank. Cuando la consulta servidor a servidor confirme el pago, aparecerá **Activar ventas reales**. Confirma esa activación para abrir las compras normales.
6. Puedes pausar nuevas ventas desde el mismo panel. La conciliación de operaciones iniciadas y los saldos existentes se conservan.

La validación es una compra separada de cero sesiones por $50, solo para el propietario y sin descuentos. Se registra como cobro real en ingresos, pero nunca crea saldo utilizable. La habilitación queda asociada al código de comercio: cambiarlo requiere otra validación. Transbank puede cobrar comisión sobre el importe. La aplicación no implementa anulaciones bancarias mediante API.

En producción se usa exclusivamente `https://webpay3g.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions` y el checkout alojado en `https://webpay3g.transbank.cl/webpayserver/initTransaction`. En integración siguen usándose host y credenciales públicas de pruebas. Los pagos y el índice de operaciones pendientes se separan por ambiente. Los antiguos pagos de integración pueden consultarse con su ambiente original, sin usar claves productivas para ellos. Ningún pago se aprueba por el simple retorno del navegador: se contrastan orden, sesión, monto y respuesta autorizada antes de acreditar una sola vez.

El retorno con `token_ws` y `TBK_TOKEN` juntos se trata como error/aborto: se identifica mediante `TBK_TOKEN`, se cotejan orden y sesión si llegan, se consulta el estado y **no se llama a commit**, incluso si ambos tokens difieren. Los timeouts sin token se buscan mediante orden y sesión, también sin commit. Los datos enviados por el navegador no cambian por sí solos el estado financiero.

Pruebas específicas sin cobros ni tráfico bancario real:

```bash
python3 -m unittest discover -s tests -p test_production_payments.py
python3 tests/run_browser.py production_browser.cjs
```

## Integración y pruebas sin cobros

Para conservar el ambiente de pruebas, usa `VALHALLA_WEBPAY_MODE=integration` y un `VALHALLA_PUBLIC_URL` HTTPS. El panel permite activar/desactivar únicamente este ambiente mientras no esté seleccionado `production` en Render. Sus credenciales son las públicas oficiales del sandbox; el código comercial guardado en el panel no se utiliza para autorizar pagos de integración.

Los usuarios compran desde Comprar horas, aceptan las normas y salen al checkout alojado. La aplicación guarda orden, sesión, monto, ambiente e intento idempotente. No permite que el navegador cambie el importe del catálogo ni acredita por un mensaje de “aprobado” del cliente. Los descuentos se calculan en servidor y se guardan como parte del precio de cada compra.

Se concilian hasta cinco pendientes por pasada en segundo plano, con recuperación de respuestas perdidas y controles contra acreditación duplicada. Un intento incierto no se reemplaza automáticamente por otro: se revisa con Transbank antes de intentar cobrar de nuevo. Pausar ventas no desactiva esta conciliación.

La reserva exige correo confirmado, saldo y documentos aprobados o una validación personal/migrada autorizada. Cancelar una reserva y devolver una sesión no devuelve dinero a la tarjeta. Los reembolsos bancarios siguen fuera del alcance de esta integración.

## Verificación

Las pruebas automatizadas emplean respuestas bancarias ficticias, sin usar la clave del comercio ni hacer cargos reales. Cubren producción e integración, destinos y credenciales separados, prueba fija de $50, autorización de propietario, apertura/pausa de ventas, rechazo, orden/monto/sesión alterados, idempotencia y retornos de error/timeout sin commit. El navegador comprueba el flujo móvil y los ingresos reales usando únicamente un banco simulado.

La compra real de $50 desde Render queda a cargo del propietario, con la versión desplegada y las credenciales configuradas. Solo esa compra confirma el funcionamiento efectivo con la cuenta productiva del comercio.

## Referencias oficiales

- [Configuración de producción](https://transbankdevelopers.cl/documentacion/como_empezar#configuracion-de-produccion)
- [Documentación Webpay](https://www.transbankdevelopers.cl/documentacion/webpay)
- [SDK oficial Python](https://github.com/TransbankDevelopers/transbank-sdk-python)
