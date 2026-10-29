# Segunda etapa: después de emitir

Implementada como módulo preparado, no conectada a emisión, compras ni correo.
`SII_POST_ISSUE_ENABLED` permanece desactivada por defecto. Ningún endpoint
activa esta opción. La vista previa privada no modifica órdenes ni envía correo.

## Componentes

- `sii_post_issue.py`: registra folio e invoice_status en una transacción;
  conserva el PDF original en SQLite persistente; estados independientes SII/PSC.
- `sii_receipt_browser.py`: observa exclusivamente el componente ya emitido,
  obtiene el mismo PDF base64 usado por el portal, completa E-mail y pulsa su
  envío una sola vez. No contiene un clic de EMITIR.
- `psc_sender(send_payload)`: adaptador para el transporte corporativo existente
  `demo_notifications.send_payload` del servicio de retenciones, sin copiar claves.
- `/admin/sii-visual/post-emission`: muestra el flujo previsto y correo corporativo
  con la primera compra pendiente, marcado como vista previa sin folio real.

## Integración posterior pendiente

El navegador y el almacenamiento/correo viven en servicios diferentes. El próximo
paso debe conectar los callbacks por el puente servidor-servidor autenticado,
con autorización vinculada a una compra y límites de tamaño de PDF. No exponer
credenciales, PDFs o información del destinatario en URLs ni logs. El servicio
de retenciones conserva su clave Resend; no duplicarla en el navegador.

La rutina `complete_after_issue` es el contrato de coordinación. Primero recibe
folio/tipo/monto desde una respuesta emitida; luego registra, guarda PDF y procesa
los dos canales. La invocación todavía no está conectada al flujo real.
Los selectores de la pantalla posterior se prepararon desde el JavaScript público
del SII y la captura proporcionada; deben validarse en la primera prueba autorizada.

Una excepción de descarga no vuelve la compra a pendiente. Cada envío se reclama
con BEGIN IMMEDIATE antes de contactar al proveedor. Un timeout queda `unknown`;
un proceso interrumpido queda `sending`. Ninguno se reenvía automáticamente.
La reconciliación/reintento de estos casos requiere confirmar el resultado previo;
no inventar éxito ni resetear a pending indiscriminadamente. `accepted` significa
aceptado por el proveedor, no entregado en la bandeja del destinatario.

Pruebas: `python -m unittest test_sii_post_issue -v` en psicologia-api.
Usan SQLite temporal y transportes falsos; no conectan al SII ni a Resend.
