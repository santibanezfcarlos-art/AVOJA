# PSC Santibáñez — preparación DTE 41

Estado al 24-10-2026: preparado en la rama `psicologia-dte41-preparacion` del repositorio existente. NO se habilita producción ni se modifica Render. No se ha contratado proveedor, certificado ni efectuado trámites ante el SII. No se han emitido boletas tributarias reales.

## Revisión de la plataforma

- `psicologia-carlos`: frontend, consulta de Calendar, administración, SQLite privado en `PSICOLOGIA_BILLING_DB`, datos del receptor y Webpay de integración. El disco debe estar montado en `/var/data` en Render.
- `psicologia-webpay-produccion-preparacion`: adaptador `webpay_gateway.py`, piloto PostgreSQL, escritura Calendar/Meet y reagendamiento. `pilot_app.py` todavía declara pagos productivos desactivados. La documentación histórica de esta rama contiene pasos ya superados; no usarla como evidencia del estado actual de Render.
- El flujo público usa `webpay_demo_bridge.py` hacia `psicologia-retenciones-piloto`. Esta integración no cambia ese puente, las reservas, Calendar, Meet ni reagendamiento.
- No existe aún un checkout productivo conectado al nuevo módulo. Copiamos el adaptador Webpay preparatorio desde la rama existente, sin activar sus rutas.
- Render no pudo inspeccionarse: el conector exige confirmar el workspace `My Workspace` (`tea-db830elg1s2s738r7glg`). No se alteraron variables, planes, discos ni despliegues.

## Alternativas verificadas en fuentes oficiales

Precios publicados consultados el 24-10-2026; confirmar cotización antes de contratar.

| Proveedor | Precio publicado | Alcance y consideración |
|---|---|---|
| OpenFactura / Haulmer | $360.000 + IVA/año; equivalente $30.000 + IVA/mes, pago anual | API ilimitada, una razón social, habilitación/certificación y migración incluidas. Firma de 1 año $11.790 + IVA adicional. Total inicial publicado $371.790 + IVA ($442.430 aprox. con IVA). |
| SimpleAPI DTE | Gratis 500 peticiones/mes; Básico 5 UF + IVA/año, 10.000 peticiones/mes | Menor suscripción; cada llamada consume peticiones, no equivalen a boletas. Generación/envío/consulta y folios requieren orquestación propia. Publica certificación de boleta a 2 UF + IVA y certificado anual $11.990 + IVA; confirmar específicamente set 41 y responsabilidades. |
| LibreDTE Plus | $40.000 + IVA/mes, 2.000 documentos emitidos/recibidos; adicional $15 + IVA | API incluida; certificación se cobra aparte. Prueba de 10 días con documentos temporales sin validez tributaria. |
| SII gratuito | Sin suscripción | Alternativa manual. No sustituye una integración automatizada de mercado y exige revisar compatibilidad/migración de sistema. |

**Recomendación técnica: OpenFactura**, por incluir habilitación y tener sandbox operativo, PDF, consulta de estado e idempotencia documentados. No es la suscripción más barata: SimpleAPI gana en precio base, pero deja más mantenimiento y operación a nuestro backend. La confiabilidad operativa del proveedor no se ha auditado: pedir condiciones de soporte, disponibilidad y recuperación antes de contratar.

Fuentes:
- https://www.openfactura.cl/ (precios, API y certificación)
- https://www.openfactura.cl/factura-electronica/api/
- https://docsapi-openfactura.haulmer.com/ (DTE 41, sandbox con CAF simulado, Idempotency-Key de 24h, consultas por token)
- https://www.simpleapi.cl/Precios
- https://www.simpleapi.cl/Productos/SimpleAPI
- https://www.libredte.cl/faq/activacion-precios-y-terminos/que-incluye-el-registro-de-contribuyentes-para-probar-libredte

## Requisitos tributarios pendientes de verificar para ESTA EIRL

La exención de salud ambulatoria no acredita que toda asesoría laboral/organizacional de una EIRL sea exenta. Validar cada prestación con el contador y antecedentes del SII. `PSC_DTE_EXEMPT_SERVICES` queda vacío por defecto: no se presume exención. No se usa el RUT personal como RUT emisor de la empresa.

Antes de producción:
1. Obtener RUT y razón social exactos de la EIRL, giro, domicilio, comuna y sucursal cuando corresponda; verificar inicio de actividades, actividades vigentes y clasificación de cada servicio.
2. Verificar calidad/habilitación del contribuyente como emisor de boletas electrónicas de mercado, específicamente tipo 41; completar certificación/migración si corresponde. Confirmar quién gestiona CAF, folios, envío y reportes exigibles vigentes.
3. Certificado digital vigente del representante/firmante autorizado por la EIRL y permisos correspondientes. Entregar certificado y contraseña al canal seguro del proveedor, nunca GitHub ni frontend. En Render guardar solo la API key cuando el proveedor custodie la firma.
4. Revisar la declaración de modelo de emisión cuando se reciben pagos electrónicos, conciliando boleta y comprobante de pago para no duplicar ventas registradas. No asumir que el voucher resuelve automáticamente el tratamiento de una operación exenta.
5. Definir rectificaciones/devoluciones: un reagendamiento NO genera otra boleta; una devolución puede requerir nota de crédito (DTE 61). Esta versión no automatiza notas de crédito.

Fuentes SII:
- https://www.sii.cl/servicios_online/3532-.html
- https://www.sii.cl/servicios_online/1039-1184.html
- https://www.sii.cl/servicios_online/1039-menu_certificacion-1184.html
- https://www.sii.cl/servicios_online/1039-certificado_digital-1182.html
- https://www.sii.cl/normativa_legislacion/resoluciones/2020/reso74.pdf
- https://www.sii.cl/preguntas_frecuentes/iva_serv_profesionales/001_400_8305.htm
- https://www.sii.cl/normativa_legislacion/circulares/2022/circu50.pdf

No se consultó la carpeta tributaria ni una sesión autenticada del SII; estos requisitos generales NO son una certificación de la situación individual.

## Flujo implementado y límites

1. Futuro checkout: guardar receptor y orden mediante `billing.prepare`, token mediante `billing.bind_token`, sesión mediante `dte.bind_session`, antes de redirigir al banco.
2. Tras el commit bancario, llamar `dte.confirm_production_payment(order, token)`. Reconsulta Transbank por HTTPS y exige ambiente production, AUTHORIZED, response_code entero 0, orden/sesión/monto exactos y token vinculado. No recibe un indicador de pago del navegador. Documentación: https://www.transbankdevelopers.cl/documentacion/webpay-plus
3. Persiste pago verificado e intención durable de emisión. Si falta configuración tributaria, el pago sigue registrado; el worker puede recuperar la cola luego, sin cobrar nuevamente. El futuro checkout debe manejar ese fallo como pendiente de facturación, nunca como pago rechazado.
4. Una fila por orden, clave idempotente estable, payload congelado, lease de 300 segundos y bloqueo SQLite evitan ejecuciones concurrentes. Emisión independiente del calendario.
5. POST a OpenFactura; persistir token antes de consultar JSON/PDF/XML/estado. Respuesta OF-06 recupera token existente. Con token, todos los reintentos son GET. Sin token, backoff 60s a 3600s; a las 23 horas del primer intento se detiene en conciliación. Nunca se cambia la clave para forzar otra emisión.
6. Aceptación del proveedor no equivale a aceptación del SII: estados pendiente, aceptado, aceptado con reparos y rechazado diferenciados. Rechazado no se reemite automáticamente.
7. PDF privado y folio se consultan en administración. En sandbox el folio no se escribe como folio tributario de la compra. No se llama al proveedor desde JavaScript.
8. Correo productivo con PDF adjunto mediante SMTP SSL. El resultado SMTP no demuestra lectura ni entrega final. Ante timeout/resultado incierto pasa a revisión; reenvío manual explícito no genera DTE nuevo. Un proceso interrumpido en `sending` requiere verificar el correo antes de recuperar su estado administrativamente; no se reenvía a ciegas.
9. Simulaciones usan emisor público de desarrollo y receptor ficticio, sin compartir datos de pacientes ni enviar correos.

## Preparación para Render (no aplicada)

Usar el servicio API existente que monta el SQLite de facturación. NO crear otro proyecto, NO montar la misma base en un worker de otro servicio y NO usar la base de reservas del piloto para estos documentos.

Inicio opcional del mismo servicio, conservando el bind actual:

```sh
gunicorn -c dte_gunicorn.py main:app --bind 0.0.0.0:$PORT
```

El hook de Gunicorn inicia el worker después del fork. Importar la app no inicia hilos nuevos. El modo por defecto es disabled. Revisar comando/root directory reales en Render antes de cambiarlo. Respaldar SQLite usando su API de backup (no copiar solo el archivo .db mientras WAL está activo); verificar restauración antes de habilitar producción. PDF y XML se guardan en la BD privada: vigilar espacio en disco y retención.

Variables nuevas:

| Variable | Valor inicial / finalidad |
|---|---|
| PSC_DTE_MODE | disabled; integration solo con pruebas preparadas; production tras revisión |
| PSC_DTE_SANDBOX_API_KEY | Clave pública de desarrollo obtenida de documentación oficial |
| PSC_DTE_API_KEY | Secreto productivo de la EIRL; no reutilizar key sandbox |
| PSC_DTE_PRODUCTION_APPROVED | false |
| PSC_DTE_EXEMPT_SERVICES | Vacío; códigos separados por coma, solo tras validar exención |
| PSC_DTE_ISSUER_JSON | Objeto con RUTEmisor, RznSocEmisor, GiroEmisor, DirOrigen, CmnaOrigen y CdgSIISucur si aplica |
| PSC_DTE_EMAIL_ENABLED | false |
| PSC_DTE_SMTP_HOST / PORT | Servidor SMTP SSL / 465 |
| PSC_DTE_SMTP_USER / PASSWORD / FROM | Credenciales en Render y remitente autorizado |

Conservar las variables existentes de reservas/Calendar/Meet y las puertas de producción de Webpay. No activar pagos reales solo porque el módulo DTE esté presente.

## Pruebas

```sh
cd psicologia-api
python -m unittest -v test_dte test_billing test_admin test_webpay_demo_bridge tests.test_calendar_refresh
# Opt-in separado: exclusivamente sandbox del proveedor y receptor ficticio.
python smoke_dte_sandbox.py --run-sandbox
```

Prueba externa efectuada el 24-10-2026: API de desarrollo generó DTE 41, folio SIMULADO 267777, PDF de 147.592 bytes y consulta JSON con tipo 41. Usa CAF simulado según el proveedor; no es boleta tributaria real. No se envió correo ni se crearon reservas durante esta prueba.

Pruebas locales cubren concurrencia, callbacks repetidos, monto/sesión/orden inválidos, timeout, ventana de idempotencia vencida, recuperación por token, persistencia de pago ante configuración incompleta, no emisión manual duplicada, PDF privado, rechazo SII y entrega de correo separada. Se actualizaron tres pruebas antiguas que fallaban también en el commit base: esperaban precios bloqueados y omitían fecha/hora/consentimiento/puente de reserva.

## Pendientes antes de activar

- Confirmar workspace y auditar servicios/despliegues/almacenamiento actuales en Render.
- Elegir/contratar proveedor y certificado; validar habilitación tributaria y cada servicio.
- Conectar el checkout productivo futuro a los hooks anteriores; hoy Webpay público sigue en integración.
- Probar extremo a extremo Transbank integración + reserva/Meet + outbox sandbox en despliegue aislado dentro de los recursos existentes, con credenciales de prueba.
- Ensayar recepción del correo con destinatario de prueba autorizado; pruebas locales SMTP no equivalen a entrega real.
- Revisar backups/restauración, conciliación operativa, vencimiento del certificado y agotamiento de folios.
- Obtener credenciales productivas Transbank y del proveedor; autorizar encendido de pagos, DTE y correo por separado.
