# Planes con Transbank Chile

Estado: diseño de integración; no se han conectado credenciales ni habilitado cobros reales.

Usaremos Webpay Plus para compras únicas de los planes. El profesional elige el paquete y paga en la página alojada por Transbank; Valhalla no recibe ni guarda datos de tarjeta. No hace falta contratar un dominio para probar el entorno de integración: el sitio HTTPS actual permite preparar los retornos.

## Pasos

1. Preparar una base persistente (por ejemplo PostgreSQL), copias de seguridad y almacenamiento privado del carnet. La SQLite local del Render gratuito no es adecuada para conservar cobros y saldos.
2. Implementar Webpay Plus primero en el ambiente de integración con las credenciales y tarjetas de prueba oficiales. El SDK oficial Python requiere Python 3.12 o superior; el Docker actual usa 3.12.
3. Guardar un intento de pago con orden única, usuario propietario, plan, precio histórico calculado en servidor, estado y token. Nunca aceptar el precio enviado por el navegador.
4. Crear la transacción mediante `Transaction.create(buy_order, session_id, amount, return_url)` y redirigir al formulario de Webpay con su token.
5. Al volver, validar el token guardado y confirmar desde el servidor mediante `commit(token)`. Comprobar autorización, código de respuesta, importe, orden y sesión contra el intento original. El retorno no debe depender de la cookie de sesión: el navegador vuelve desde otro sitio.
6. Acreditar las sesiones una sola vez dentro de una transacción de base de datos, con restricciones de unicidad. Recargar la página o repetir un retorno no debe duplicar el saldo. No permitir confirmar manualmente órdenes reales como si fueran pagos simulados.
7. Probar aprobación, rechazo, abandono, expiración, retornos falsos y repetidos, interrupciones y conciliación con `status(token)`. Un error de red deja el pago pendiente de conciliación; no implica que no hubo cargo ni autoriza cobrar otra vez.
8. Cuando las pruebas estén aprobadas, tramitar la afiliación y credenciales comerciales con Transbank según sus requisitos vigentes. Activar producción requiere autorización expresa del propietario. No compartir claves por chat.

Los paquetes siguen siendo personales y compartidos entre los tres boxes. Pagar no reemplaza la confirmación del correo ni la aprobación manual del carnet. Cancelar una reserva y devolver una sesión es distinto de reembolsar dinero: esa política debe definirse antes de producción.

## Referencias oficiales

- [Webpay](https://www.transbankdevelopers.cl/producto/webpay)
- [Ambientes de integración y producción](https://www.transbankdevelopers.cl/documentacion/como_empezar#ambientes)
- [Documentación Webpay](https://www.transbankdevelopers.cl/documentacion/webpay)
- [SDK oficial Python](https://github.com/TransbankDevelopers/transbank-sdk-python)

Se revisaron el README y los métodos create, commit y status del SDK oficial. La documentación web fue bloqueada por el proxy de este entorno; sus condiciones comerciales deben consultarse directamente con Transbank. No se ha instalado el SDK ni implementado un endpoint de cobro en esta actualización.
