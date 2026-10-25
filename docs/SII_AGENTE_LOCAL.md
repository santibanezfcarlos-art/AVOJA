# SII: primera entrega de integración local

## Alcance comprobable

- Se conserva la sesión manual y el perfil de Chromium en Windows.
- El botón **Preparar en mi computador (sin emitir)** crea un trabajo con los datos de la base de datos, exclusivamente para compras de producción aprobadas, pendientes y sin folio.
- **Emitir boleta SII** aparece deshabilitado. El endpoint rechaza modos distintos de `preview`; el agente nunca envía argumentos de emisión final.
- El módulo `sii_final_control.py` prepara la operación final: revalidación de seis campos, asociación estructural del botón con su formulario (sin coordenadas), documento exento y monto, marcador persistente previo al clic y estado incierto. Aún NO se habilita desde el script ni el panel. El controlador del servidor deberá enlazarse antes de habilitarlo.
- No hay parser de folio confirmado ni descarga/envío de comprobantes habilitados. Hace falta observar la respuesta de una primera operación real expresamente autorizada. No se interpreta un clic ni un HTTP 200 como una boleta emitida.

## Instalar la actualización en Windows

Conservar `perfil-privado` y `datos_prueba.json`. Cerrar antes los navegadores que utilicen ese perfil.

```powershell
Set-Location "$env:USERPROFILE\PSC-SII"
$base = 'https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/psicologia-carlos/tools'
foreach ($archivo in @('sii_monto_local.py','sii_final_control.py','sii_agente_local.py','sii_sesion_persistente.py')) {
    Invoke-WebRequest "$base/$archivo" -OutFile ".\$archivo"
}
python .\sii_monto_local.py --perfil "$env:USERPROFILE\PSC-SII\perfil-privado" --monto 30000 --continuar --mostrar-campos --datos .\datos_prueba.json --resultado .\resultado_preparacion.json
Get-Content .\resultado_preparacion.json
```

`prepared` significa formulario y seis campos verificados, sin emisión. `blocked` significa que no se puede verificar de forma inequívoca. `session_required` pide renovación manual. Enviar solo el resultado estructurado o mensajes de diagnóstico, nunca el archivo de datos ni el perfil.

Si el diagnóstico queda bloqueado por `ambiguous_form`, necesitamos revisar la estructura de ese formulario en la sesión local antes de continuar. No reemplazarlo por coordenadas.

## Conectar panel y computador

```powershell
python .\sii_agente_local.py --perfil "$env:USERPROFILE\PSC-SII\perfil-privado"
```

Escribir la contraseña del **panel PSC**, no la Clave Tributaria. Si el usuario del panel es distinto de `carlos`, agregar `--usuario NOMBRE`.

La contraseña se solicita sin mostrarla y se intercambia por un token limitado a la cola del agente, válido por 12 horas. Solo el hash queda almacenado en Render; el token permanece en memoria local. No se guarda la contraseña en disco ni se imprime. Volver a ejecutar el agente cuando venza.

En `/admin/boletas`, elegir una compra **real aprobada** y pulsar **Preparar en mi computador (sin emitir)**. Mantener el agente ejecutándose. Pulsar **Actualizar** para ver el resultado. Los datos no se copian manualmente. Compras de integración/simuladas nunca se convierten en reales para probar esta función.

No ejecutar simultáneamente el script de diagnóstico y el agente con el mismo perfil.

## Sesión SII vencida

Detener el agente con Ctrl+C y ejecutar:

```powershell
python .\sii_sesion_persistente.py autorizar --perfil "$env:USERPROFILE\PSC-SII\perfil-privado"
```

Iniciar sesión manualmente, volver a la consola y pulsar Enter. Ejecutar nuevamente el agente y preparar la compra desde el panel.

## Duplicados y recuperación

- SQLite usa `BEGIN IMMEDIATE` y un índice único por compra para trabajos abiertos.
- Solo un trabajo puede estar `running` globalmente. Se vuelve a comprobar elegibilidad al reclamarlo.
- Un trabajo reclamado jamás se entrega de nuevo automáticamente, aunque el agente pierda conexión o cierre.
- Si falla la comunicación al informar el resultado, el agente reintenta solamente ese informe, nunca el formulario.
- El registro manual de folios se bloquea mientras hay un trabajo abierto.
- Si quedó una preparación interrumpida, primero detener el agente y su Chromium; después usar **Cerrar preparación interrumpida**. Solo existe esa liberación para trabajos `preview`, que no emiten.
- Los resultados de preparación no cambian el folio ni el estado tributario de la compra. El resultado se conserva en `sii_jobs` y `billing_audit`.

Los archivos temporales con datos de la ficha se eliminan al finalizar el proceso hijo. Mantener la cuenta Windows y su carpeta temporal privadas; un apagado abrupto puede dejar un directorio temporal `psc-sii-*`. Nunca subirlo a GitHub.

## Siguiente etapa (aún pendiente)

1. Validar la asociación estructural con el formulario real del usuario.
2. Vincular el controlador final a una transición irreversible y transaccional en el servidor antes del clic, con autorización por compra y sin reentrega automática.
3. Con una operación real autorizada, inspeccionar la respuesta y el comprobante para validar DTE 41, emisor, receptor, importe y folio.
4. Guardar documento privado asociado a la compra, implementar conciliación de resultados inciertos y preparar correo. No enviar correos hasta instrucción expresa.

No cambia la lógica de pagos, reservas, Calendar, Meet ni método de pago.
