# Webpay Plus: compra y acreditación de planes

Implementado para el **ambiente oficial de integración**, desactivado por defecto. Esta versión no admite modo producción ni credenciales comerciales: no puede realizar cargos reales. El propietario ya tiene un comercio habilitado; no necesita volver a contratarlo para preparar las pruebas.

## Flujo implementado

1. El profesional confirma su correo, elige un plan y continúa al checkout alojado por Transbank. No se ingresan tarjetas en Valhalla.
2. El servidor calcula el precio usando el catálogo; ignora importes, saldos y titulares enviados por el navegador. Guarda una orden única y un paquete pendiente con cero sesiones disponibles.
3. Crea el pago mediante la API REST oficial v1.2 y envía el token por formulario POST al checkout de integración. Solo se admite el host HTTPS oficial, sin redirecciones arbitrarias de la API.
4. El retorno GET/POST se procesa sin depender de una sesión del navegador (Webpay vuelve desde otro origen). Se busca el intento guardado y se consulta/confirma directamente con Transbank.
5. Solo `AUTHORIZED` con código de respuesta entero `0`, importe exacto, orden y sesión coincidentes permite acreditar. El cambio de estado y el saldo se guardan juntos en una transacción.
6. Un retorno repetido no acredita otra vez ni repone horas ya gastadas. El saldo siempre queda en la cuenta que inició el pago; un paquete Webpay no se puede confirmar manualmente como si fuera pago simulado.
7. Rechazo, abandono, respuesta incoherente o error de red no habilitan saldo. Una respuesta perdida después de la aprobación puede recuperarse consultando el estado, sin ejecutar otra compra.

El servidor revisa intentos pendientes en segundo plano cada 30 segundos, hasta cinco por pasada, incluso si el navegador no vuelve. El retorno normal acredita al confirmarse; en interrupciones puede tardar hasta una pasada de conciliación o más si Transbank no responde. «Consultar estado» existe en Paquetes y en Administración → Pagos. Los intentos con discrepancias requieren revisión; nunca se aprueban por una señal enviada por el navegador. No se permite otra compra mientras una anterior siga incierta.

Si la creación no devuelve token, el intento queda pendiente de revisión administrativa. No se reintenta automáticamente: se debe consultar la orden con Transbank antes de cerrar o corregir ese intento. La interfaz no ofrece un botón que invente una aprobación.

## Activar solo las pruebas en Render

Conserva primero una copia de la base: en el servicio gratuito actual un nuevo despliegue puede perder los datos locales. Hasta tener persistencia usa saldos, clientes y documentos ficticios.

En Environment:

| Variable | Valor |
| --- | --- |
| `VALHALLA_WEBPAY_MODE` | `integration` |
| `VALHALLA_PUBLIC_URL` | Origen HTTPS accesible de tu sitio, sin barra final, ruta ni parámetros |

Mientras el dominio propio esté pendiente de DNS/certificado, utiliza `https://valhalla-office-demo.onrender.com`. Tras activarlo, puede ser `https://valhallaoffice.cl`. Esta URL se usa también en los enlaces de correo. Guarda y despliega; Paquetes mostrará «Comprar en Webpay · prueba» y una advertencia explícita de que no se realizan cargos reales.

Se usan las credenciales públicas oficiales de integración de Webpay Plus. No pegues aquí la clave ni el código de tu comercio real. Usa únicamente las tarjetas y datos de prueba publicados por Transbank. Para desactivar el checkout, vuelve a `VALHALLA_WEBPAY_MODE=disabled`; las operaciones simuladas anteriores conservan su origen.

## Validación realizada y pendiente

```bash
cd /workspace/AVOJA/valhalla
python3 -m unittest discover -s tests -v
python3 tests/run_browser.py
python3 tests/run_browser.py migration_payments_browser.cjs
```

Las pruebas automatizadas usan un adaptador bancario falso aislado: cubren aprobación, rechazo, abandono, concurrencia, importe/orden/sesión alterados, respuesta perdida tras aprobar, conciliación de fondo, titularidad y repetición del retorno. El navegador recorre importación CSV, registro, reserva sin carnet y compra con acreditación única en móvil.

**No se ha completado un pago contra el sandbox oficial desde este entorno.** El intento de conectar a `webpay3gint.transbank.cl` fue bloqueado por el proxy con `Tunnel connection failed: 403 Forbidden`. Esto no es una respuesta bancaria de rechazo. La creación y aprobación real de integración deben probarse desde Render antes de considerar lista la conexión comercial. No se desactivaron TLS ni verificaciones de certificados.

La integración usa la API REST con la biblioteca estándar, sin añadir un SDK. Hosts, cabeceras, credenciales públicas y métodos se contrastaron con el SDK oficial de Transbank.

## Antes de habilitar el comercio real

- Preparar almacenamiento persistente protegido, copia/restauración probadas y despliegue que conserve usuarios, saldos, intentos y documentos. `VALHALLA_DB_PATH` permite ubicar SQLite en un disco persistente de Render; requiere contratar/configurar ese disco y montarlo con permisos para UID 10001. La aplicación actual usa un solo servicio; no compartas SQLite entre réplicas. PostgreSQL es la alternativa para escalar.
- Configurar y comprobar el correo y el dominio HTTPS.
- Completar los casos de integración oficiales con tu configuración, incluida conciliación tras cortes.
- Revisar las condiciones de tus planes, cancelaciones y reembolsos. Devolver una sesión al cancelar una reserva no devuelve dinero a una tarjeta. Esta versión no ejecuta reembolsos ni procesa cambios posteriores de Webpay sobre compras ya acreditadas; se necesita conciliación operativa para devoluciones externas.
- Con autorización expresa, preparar en una actualización separada el ambiente de producción y configurar las credenciales privadas del comercio exclusivamente en Render. `production` está rechazado por el programa actual; añadir claves comerciales por sí solo no activa cobros.

## Referencias oficiales

- [Webpay](https://www.transbankdevelopers.cl/producto/webpay)
- [Ambientes](https://www.transbankdevelopers.cl/documentacion/como_empezar#ambientes)
- [Documentación Webpay](https://www.transbankdevelopers.cl/documentacion/webpay)
- [SDK oficial Python](https://github.com/TransbankDevelopers/transbank-sdk-python), archivos `api_constants.py`, `integration_type.py`, `integration_commerce_codes.py`, `integration_api_keys.py`, `headers_builder.py` y `webpay_plus/transaction.py`.
