# Clientes y saldos del sistema anterior

Administración → Migración permite reconocer clientes ya validados, sin exigirles nuevamente el carnet. No es una compra ni un pago: cada saldo importado queda en un paquete personal de origen `migration`, precio cero y estado habilitado, con referencia del sistema anterior, observación, operador y fecha.

**Antes de cargar saldos reales:** el servicio necesita almacenamiento persistente y una copia de seguridad verificable. La demo gratuita actual puede perder la base al desplegar o reiniciar. Usa clientes y saldos ficticios hasta resolverlo. No importes contraseñas ni carnets en la planilla.

## Desde Excel o CSV

1. Abre Administración → Migración → Descargar plantilla CSV.
2. Reemplaza la fila de ejemplo por los clientes reales, cuando el almacenamiento esté listo. Exporta Excel como CSV UTF-8. Se admiten separadores coma o punto y coma; la interfaz también lee archivos Windows-1252.
3. Conserva exactamente estos encabezados, en este orden:

```csv
referencia,nombre,correo,especialidad,sesiones,observacion
antiguo-001,Juanita Ejemplo,juanita@example.com,Psicología,10,Saldo restante revisado en sistema anterior
```

`sesiones` es el saldo **restante**, entero entre 0 y 10.000. Una sesión del nuevo sistema dura 55 minutos y comienza a la hora exacta. No importes el total histórico comprado ni horas que ya fueron consumidas. Revisa previamente cualquier diferencia de duración o vencimiento con el sistema antiguo.

4. Selecciona el archivo (máximo 500 clientes y 1 MB), confirma que sus identidades y saldos ya fueron revisados y pulsa Revisar planilla.
5. Revisa la vista previa. Todavía no hay cambios guardados.
6. Confirma la importación. Si una fila es inválida o hay un correo/referencia duplicados, se rechaza toda la carga: ninguna fila recibe saldo parcial.

El correo se normaliza sin espacios y en minúsculas. La referencia debe identificar una sola migración; el mismo correo solo se importa una vez, incluso con otra referencia. La carga individual tiene las mismas comprobaciones y un diálogo de confirmación.

## Si la persona ya tiene cuenta

Se vincula por correo, conserva sus contraseñas, reservas y paquetes, y recibe el saldo anterior por separado. Debe ser una cuenta profesional activa y el nombre debe coincidir (sin distinguir mayúsculas). Si hay diferencias, revisa el titular antes de importar: la herramienta no cambia ni reasigna cuentas automáticamente.

## Si todavía no tiene cuenta

La migración queda pendiente de registro. Indícale que se registre con el mismo correo; no se crea una contraseña compartida ni una cuenta de acceso automático. Cuando se registra, se vinculan la validación previa y el saldo una sola vez. Deberá confirmar su correo para reservar. La confirmación necesita el servicio SendGrid/Resend configurado.

Los clientes migrados ven «Validación anterior» y Documentación explica que no necesitan reenviar el carnet. Un usuario nuevo no puede declararse migrado a sí mismo. Cambiar el nombre o subir voluntariamente otro carnet retira la excepción anterior y exige nueva revisión. La desactivación impide reservar sin borrar saldos ni reservas existentes.

## Respaldo y control

La tabla de migraciones muestra saldo inicial importado, referencia, observación, fecha, operador y cuenta vinculada. El saldo actual, con las sesiones gastadas, se consulta en Paquetes. La importación no transfiere paquetes entre profesionales ni confirma pagos Webpay. Una carga duplicada se rechaza, no se suma otra vez.

No hay un botón para borrar migraciones o corregir saldos a ciegas. Si hubo un error, conserva el respaldo y concilia el ajuste antes de aplicar una corrección auditada. No edites SQLite manualmente en producción ni repitas la carga con otro correo para compensar.
