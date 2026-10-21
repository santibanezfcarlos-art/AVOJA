# Valhalla Office — reservas

Plataforma de prueba para tres boxes. Interfaz adaptable a celulares, API Python y SQLite. Los pagos siguen siendo simulados; no existe integración de cobros.

## Acceso privado y registro profesional

- Inicio sin correo ni contraseña rellenados por la aplicación.
- **Acceso profesional** abre el registro; quien ya tiene cuenta puede pulsar **Inicia sesión**.
- Las nuevas cuentas profesionales no reciben saldo ni verificación automática. Deben enviar documentación, ser aprobadas por administración y solicitar un paquete cuyo pago simulado sea confirmado.
- **Acceso administrador** abre un formulario vacío. El servidor admite como administrador exclusivamente a `santibanezf.carlos@gmail.com`, con una contraseña privada configurada por el propietario.
- Ese correo está reservado: no se puede crear ni ascender una cuenta administradora desde el registro público. Escribir el correo por sí solo no concede acceso.
- La aplicación no inicia sesión con Google ni comprueba el buzón Gmail: autentica con la contraseña privada que el propietario configura en el servidor. No uses tu contraseña de Gmail.
- Las cuentas ficticias antiguas quedan inactivas y sus sesiones se revocan al iniciar la nueva versión. No se borran los historiales ni los datos de profesionales existentes.
- Un cambio de la contraseña administradora en la configuración revoca sus sesiones anteriores. Reiniciar con la misma contraseña conserva la cuenta sin duplicarla.

## Una cuenta con dos perfiles para el propietario

Al iniciar sesión con `santibanezf.carlos@gmail.com`, el selector **Perfil activo** permite alternar entre **Administración** y **Perfil profesional**, también desde el celular, sin salir de la cuenta. El permiso administrativo se conserva al usar el perfil profesional; otros usuarios no pueden habilitarlo cambiando su perfil.

En **Perfil profesional → Mi perfil** puedes completar nombre y especialidad. Esa misma cuenta tiene paquetes y reservas personales vinculados a su identificador, sin transferencias ni horas gratuitas. Solicita un paquete, vuelve a Administración para confirmar el pago simulado y regresa al perfil profesional para reservar. Se aplican las mismas restricciones de saldo, conflictos y modificaciones. El propietario también debe tener carnet y certificado de título aprobados para reservar. El acceso administrativo se mantiene aunque su identidad profesional esté pendiente; subir un carnet nuevo exige revisión otra vez.

No se crea otro usuario ni se cambia el correo o la contraseña al alternar. Al recargar la página la cuenta administradora abre Administración; puedes volver a elegir el perfil profesional. No necesitas otra variable en Render.

## Aplicar el cambio en Render

El servicio desplegado debe usar la rama `valhalla-demo` del repositorio AVOJA.

1. Abre el servicio en Render → **Environment** → **Add Environment Variable**.
2. Nombre: `VALHALLA_ADMIN_PASSWORD`.
3. Valor: elige una contraseña privada nueva de **14 a 200 caracteres**. Puedes usar el generador del proveedor y guardarla en tu gestor de contraseñas. No la envíes por chat ni la subas al repositorio.
4. Guarda la configuración. En **Manual Deploy → Deploy latest commit**, despliega la última versión de `valhalla-demo`. Las variables de un servicio creado manualmente no se añaden automáticamente desde `render.yaml`.
5. Espera a **Live**, abre el sitio y comprueba que los formularios están vacíos. Entra como administrador con el correo autorizado y la contraseña que elegiste. En una ventana privada prueba el registro profesional.

La retirada de las credenciales públicas solo se aplica al sitio publicado cuando Render ejecuta la nueva versión. Si despliegas sin la variable, los accesos ficticios se cierran igualmente, pero en una base nueva no habrá administrador disponible hasta configurarla. Una contraseña de configuración demasiado corta impide el arranque con un error que no muestra su valor.

`render.yaml` conserva el plan Free y solicita el secreto al crear un Blueprint nuevo. `Dockerfile` inicia la aplicación con cookies Secure y **no crea cuentas demo**. No se ha contratado almacenamiento ni servicios de pago.

**Persistencia pendiente:** el disco del plan gratuito de Render es efímero. Cuentas, documentos y reservas pueden perderse al reiniciar o desplegar. La contraseña de configuración permite recrear la cuenta administradora, pero no restaura los datos. Antes de un piloto que necesite conservar cuentas y reservas, hay que conectar una base persistente. Usa documentos ficticios mientras esto siga siendo un prototipo compartido.

## Ejecutar localmente

Python 3.10+ y zonas horarias del sistema; sin dependencias del servidor fuera de su biblioteca estándar. El contenedor incluye `tzdata` si la imagen no ofrece la zona de Chile.

```bash
cd /workspace/AVOJA/valhalla
python3 app.py
```

Sirve en el puerto 8080 de loopback. Para habilitar al administrador local sin escribir su contraseña en el comando ni en archivos:

```bash
read -r -s -p 'Nueva contraseña administradora: ' VALHALLA_ADMIN_PASSWORD
export VALHALLA_ADMIN_PASSWORD
python3 app.py
unset VALHALLA_ADMIN_PASSWORD
```

Se crea `data/valhalla.sqlite3`, ignorado por Git. Puedes elegir otra base con `--db /tmp/valhalla-prueba.sqlite3` y otro puerto con `--port 8081`. `--host 0.0.0.0` permite conexión desde otras interfaces. `--secure-cookie` es para HTTPS detrás del proxy del proveedor, no para las pruebas locales HTTP. La opción antigua `--seed-demo` solo se acepta por compatibilidad con comandos previos: ya no crea cuentas ni saldo.

## Reglas y arquitectura

- Tres boxes, sesiones de 55 minutos con inicios cada hora de 07:00 a 22:00 (última atención hasta 22:55, margen hasta el cierre a las 23:00), zona `America/Santiago`, hasta 90 días hacia adelante.
- Sesión individual de $5.990 y planes personales mixtos de 5, 10, 30, 60, 100 y 200 horas, sin vencimiento en este prototipo. Cada hora contratada equivale a una sesión de 55 minutos. Se comparten entre boxes, no entre profesionales.
- Solo el profesional activo, con correo confirmado, carnet y certificado de título aprobados o validación anterior por migración administrativa, reserva con saldo confirmado. Se consume el paquete confirmado más antiguo con saldo.
- Una transacción crea la reserva y descuenta el saldo. Un índice único protege el box y horario; el profesional tampoco puede reservar dos boxes simultáneamente. Un trigger impide transferir el paquete.
- Una modificación por reserva, al menos dos horas antes tanto del horario original como del nuevo. No consume otra hora; un intento fallido no gasta el cambio.
- Administración revisa el último envío privado (hasta 5 archivos y 10 MB en total), aprueba o rechaza con observaciones, confirma pagos simulados, activa usuarios y cancela reservas futuras devolviendo la hora una sola vez.
- Subir un carnet o certificado de título nuevo pausa la creación y modificación de reservas hasta su aprobación; conserva las reservas existentes.
- Contraseñas con PBKDF2 y sal, sesiones HttpOnly/SameSite de ocho horas, protección CSRF, permisos comprobados en servidor y registro de operaciones. Los archivos se descargan como adjuntos solo por el titular o administrador.

Entidades: `users`, `sessions`, `documents`, `packages`, `bookings` y `audit`. Los horarios se guardan en UTC. La API y la interfaz usan el mismo origen.

Para producción: PostgreSQL, almacenamiento privado de documentos, migraciones, límites de intentos, recuperación de cuentas, MFA administrativo, análisis de archivos, copias de seguridad y monitoreo. La revisión manual del carnet no certifica su autenticidad oficial. Los precios son los indicados por el propietario; feriados, vencimientos y política definitiva de cancelación siguen siendo decisiones de producto. Webpay está implementado únicamente en integración, desactivado por defecto; ningún cobro real está habilitado.

## Pruebas

```bash
cd /workspace/AVOJA/valhalla
python3 -m unittest discover -s tests -v
```

Las bases son temporales. Se cubren concurrencia, saldos, conflictos, límites de modificación, documentos, permisos, retiro de cuentas demo, reserva del correo administrador, contraseña privada y revocación de sesiones.

Prueba opcional de navegador: `python3 tests/run_browser.py` inicia una instancia descartable con una contraseña aleatoria, ejecuta el recorrido y la detiene. Requiere Node, Playwright y Chromium. Para un servidor de pruebas que ya esté iniciado, usa `node tests/browser.cjs`. Ejecuta solo contra una instancia aislada; configura `VALHALLA_TEST_URL` y `VALHALLA_TEST_ADMIN_PASSWORD` con la URL local y la contraseña privada **de esa instancia de pruebas**, nunca la del sitio publicado. Recorre registro, verificación, pago simulado, reserva, modificación, cancelación, desactivación y móvil.

Prueba manual: abre una ventana privada → Acceso profesional → crea una cuenta nueva → confirma tu correo → adjunta archivos ficticios → solicita paquete → entra como administrador para aprobar documento y pago → vuelve como profesional y reserva. Comprueba también que los accesos antiguos y el registro con el correo administrador se rechazan.

## Vista previa dentro del chat

`vista-previa.html` es autónomo y no se conecta a la base del sitio. Ahora abre el inicio con campos vacíos, sin cuentas precreadas. Permite explorar el registro con datos ficticios en memoria. No permite acceder a la administración privada: esa función se prueba en el servidor. Los datos se pierden al recargar. Regenera el archivo con `python3 build_preview.py` después de cambiar la interfaz o `preview-api.js`.

El bingo original de AVOJA permanece intacto.

## Fotos de la oficina

Galería pública agrupada: Entrada (1), Recepción (2 y 3), Box infantil (6 y 7), Box 1 (11 y 12) y Box 2 (13, 14 y 15). El box infantil es el tercer box reservable. Todas las fotografías se conservan completas y el visor permite recorrer las de cada espacio. En disponibilidad aparecen únicamente los boxes, con sus fotos correspondientes.

Las fotos viven en `static/images` y se publican desde Git, sin depender de SQLite. Consulta `static/images/README.md` para reemplazarlas. La vista del chat tiene interfaz y operaciones locales, pero carga estas imágenes desde la web pública para mantener liviano el HTML.

## Presentación del inicio

El panel verde presenta las fotos originales mediante un carrusel automático de cinco segundos, con flechas, selector de foto y botón Pausar/Reanudar. Sustituye a los arcos decorativos 01/02/03. El texto explica los tres boxes, la recepción y los paquetes de horas, y enlaza a la galería completa.

El carrusel pausa al situar el puntero o el foco sobre sus controles y cuando la pestaña queda oculta. Si el navegador solicita movimiento reducido, comienza detenido y permite navegación manual. Al entrar a la aplicación se detiene y limpia su temporizador. Las fotos conservan su encuadre completo en computador y celular.

## Carnet obligatorio y agenda administrativa

Los perfiles profesionales nuevos, incluido el propietario, necesitan el último carnet y el último certificado de título aprobados por separado **y** saldo con pago confirmado. Los clientes anteriores pueden recibir una validación administrativa de migración, sin repetir el carnet, según [MIGRACION.md](MIGRACION.md); todos necesitan confirmar su correo y tener saldo para reservar. Para los nuevos perfiles, se requieren carnet y certificado de título aprobados para crear o modificar reservas. El carnet se envía en entre 1 y 5 archivos de cualquier formato, hasta 10 MB en total, que incluyan anverso y reverso. El administrador confirma explícitamente que revisó ambos lados y su correspondencia con el titular. Una acreditación profesional no sustituye el carnet, ni un flag antiguo de verificación permite reservar. Cambiar el nombre del perfil vuelve a dejar ambos documentos pendientes de revisión. La revisión es manual, no una validación contra un registro oficial.

La migración añade `documents.kind` sin borrar archivos ni reservas. Los documentos anteriores se clasifican como acreditaciones, por lo que requieren enviar un carnet nuevo; los usuarios conservan sus cuentas, saldos y reservas. La identidad pendiente no bloquea el acceso administrativo del propietario. Hasta contar con almacenamiento persistente y protección de documentos adecuada para un piloto, usa carnets ficticios.

Administración → Disponibilidad muestra nombre, correo, box y rango de 55 minutos por reserva. Identifica próximas atenciones, horarios en curso, los cinco minutos de margen y reservas finalizadas. Pulsa Actualizar agenda para refrescar. Esto informa la ocupación según agenda; no registra presencia física ni ingreso o salida. El endpoint `/api/admin/availability` exige permisos administrativos. El calendario profesional nunca expone identidades ajenas, aunque se envíen parámetros adicionales.

| Plan mixto, todos los boxes | Precio CLP | Sesiones de 55 min |
| --- | ---: | ---: |
| 1 hora | $5.990 | 1 |
| 5 horas | $28.990 | 5 |
| 10 horas | $54.990 | 10 |
| 30 horas | $144.990 | 30 |
| 60 horas | $254.990 | 60 |
| 100 horas | $364.990 | 100 |
| 200 horas | $599.990 | 200 |

Las reservas empiezan a las horas exactas; una atención de 10:00 termina a las 10:55 y el siguiente inicio es 11:00. Una reserva consume una sesión. Los planes previamente solicitados conservan su precio histórico y saldo; solo las nuevas solicitudes usan el catálogo actualizado. Los pagos continúan simulados.


## Confirmación de correo sin dominio propio

Las cuentas profesionales nuevas y anteriores necesitan confirmar el correo antes de reservar. El propietario administrador conserva su acceso de confianza. El enlace vence en 24 horas, se utiliza una sola vez y no inicia sesión. Reenviarlo invalida el anterior y exige esperar un minuto. La base almacena solo su hash. Sin proveedor configurado, la cuenta sigue pendiente; no existe aprobación automática.

Para pruebas sin dominio propio puedes verificar una dirección Gmail como remitente individual en SendGrid:

1. Crea una cuenta en [SendGrid](https://sendgrid.com/). Revisa las condiciones y límites vigentes; el correo no se garantiza gratuito de forma indefinida.
2. En Settings → Sender Authentication → Single Sender Verification, registra tu dirección y confirma el mensaje que recibas. No necesitas entregar tu contraseña Gmail a la aplicación.
3. En Settings → API Keys, crea una clave restringida con permiso Mail Send.
4. En Render → valhalla-office-demo → Environment, agrega:

| Variable | Valor |
| --- | --- |
| `VALHALLA_EMAIL_PROVIDER` | `sendgrid` |
| `VALHALLA_EMAIL_FROM` | La dirección que verificaste en SendGrid |
| `VALHALLA_SENDGRID_API_KEY` | La clave privada de SendGrid, solo en Render |
| `VALHALLA_PUBLIC_URL` | `https://valhalla-office-demo.onrender.com` |

Guarda y vuelve a desplegar con las variables. Crea una cuenta con un correo distinto, revisa entrada y spam, abre el enlace y pulsa Confirmar correo. Después inicia sesión normalmente. La aplicación exige también carnet y certificado de título aprobados y saldo confirmado para reservar. El envío del proveedor no garantiza recepción; el usuario puede solicitar otro enlace. Nunca pegues claves en el chat ni en GitHub.

Para producción conviene un dominio propio con autenticación del remitente. También se admite Resend configurando `VALHALLA_EMAIL_PROVIDER=resend`, `VALHALLA_RESEND_API_KEY` y un remitente autorizado por ese proveedor. No se ha realizado ningún envío real en las pruebas automatizadas: usan un buzón local aislado.

Los adjuntos se descargan como binarios privados, nunca se ejecutan ni muestran incrustados. Admitir cualquier extensión no demuestra que el documento sea legible o auténtico: administración debe revisar ambos lados. En Render gratuito la base local puede perderse al desplegar o reiniciar; antes de usar carnets reales o pagos, se requiere almacenamiento persistente y protegido.

La conexión de pagos de prueba está en [TRANSBANK.md](TRANSBANK.md). Los cobros reales siguen deshabilitados.


## Confirmación y recuperación de contraseña

El registro exige escribir la contraseña dos veces; la coincidencia se verifica también en el servidor. En el inicio de sesión, «Olvidé mi contraseña» permite solicitar un enlace al correo de una cuenta profesional activa. El enlace vence en 30 minutos, es de un solo uso y solo se almacena su hash. Solicitar otro enlace tras un minuto invalida el anterior. Guardar una contraseña nueva cierra todas las sesiones anteriores, sin cambiar la verificación de correo, el carnet, los saldos o las reservas.

La respuesta pública no revela si una cuenta existe. Hay límites por correo y por dirección de conexión; detrás de un proxy, varias personas pueden compartir el límite de 10 solicitudes por hora. El límite por conexión vive en memoria y se reinicia con el proceso. Para un despliegue mayor, trasladarlo a un limitador persistente con conocimiento de los proxies de confianza.

La recuperación utiliza el mismo servicio SendGrid/Resend ya documentado. Si todavía no está configurado, la interfaz lo indica y no simula un envío. Tras activar el dominio y HTTPS, establece `VALHALLA_PUBLIC_URL=https://valhallaoffice.cl` en Render para que los enlaces lleguen con tu dominio. Las pruebas usan un buzón local aislado; no envían correos reales.

La contraseña del administrador, incluso cuando utiliza su perfil profesional, sigue gestionándose únicamente mediante `VALHALLA_ADMIN_PASSWORD` en Render y un nuevo despliegue. La pantalla de recuperación del acceso administrador explica ese procedimiento. Esto evita que un restablecimiento web sea reemplazado al reiniciar por el valor privado configurado.

Prueba manual: registra una cuenta con contraseñas distintas y comprueba el rechazo; vuelve a enviarlas iguales. Desde el inicio solicita recuperación, abre el correo, escribe dos veces la contraseña nueva e inicia sesión. Comprueba que el enlace no pueda reutilizarse y que la contraseña anterior ya no funcione. Usa una instancia aislada para estas pruebas.


## Clientes anteriores y Webpay

- [MIGRACION.md](MIGRACION.md): carga individual o CSV con vista previa, migración de saldos restantes y reconocimiento de identidades ya validadas. Solo administración puede realizarla. No se crean contraseñas compartidas.
- [TRANSBANK.md](TRANSBANK.md): API Webpay Plus de integración, checkout alojado, acreditación automática y única, conciliación, configuración y pasos pendientes para producción. El proxy de este entorno bloqueó la conexión al sandbox; la aprobación real de integración queda pendiente de probar desde Render.

La base local de Render gratuito sigue siendo temporal. Antes de trasladar saldos reales, configura almacenamiento persistente y respaldo. `VALHALLA_DB_PATH` permite elegir la ruta de una base en un disco persistente, sin cambiar la ruta predeterminada de la demo. Un despliegue de código no contrata ni monta ese disco.


## Diagnóstico de confirmaciones y recuperación

Administración → Correos muestra el proveedor, remitente, origen HTTPS de los enlaces y si existe una clave configurada, sin mostrar su valor. El botón «Enviar prueba a mi correo administrador» envía únicamente a la dirección administradora fija, requiere sesión administrativa y CSRF y admite una prueba por minuto. No permite enviar a destinatarios arbitrarios ni cambia contraseñas o validaciones.

Los intentos recientes diferencian aceptación del proveedor, error y recuperación sin intento (cuenta profesional no disponible o límite/cooldown). La aceptación **no** garantiza entrega; comprueba también Resend → Emails y el buzón. El aviso profesional ya no afirma que se envió un correo cuando el último intento falló. Por privacidad, la recuperación pública mantiene la misma respuesta para cuentas inexistentes y errores de proveedor.

Diagnósticos seguros: HTTP 401 indica que se rechazó la autenticación; HTTP 403 requiere revisar permisos/dominio o filtros; 422 indica remitente/payload; 429, límites del proveedor; los fallos de red se registran aparte. Solo se retienen categorías fijas, estado HTTP, tipo y cuenta asociada al evento; no cuerpos de respuesta, claves, contenido de correos ni tokens. Los fallos también dejan una línea segura en Render → Logs. Estos registros son privados de administración.

El cliente identifica la aplicación mediante User-Agent y normaliza espacios exteriores de las variables. Esto mejora compatibilidad y elimina errores accidentales de pegado; no demuestra por sí solo que una clave sea válida. La confirmación requiere que Resend acepte una prueba real desde Render y que el destinatario la reciba. Primero prueba el envío administrador, después registra una cuenta profesional de prueba y comprueba confirmación y recuperación. Un nuevo despliegue todavía puede eliminar cuentas de prueba mientras no exista almacenamiento persistente.


## Avisos automáticos y correos del día

Sin cambiar las variables de correo ya configuradas, la aplicación encola estos nuevos avisos:

| Evento | Destinatario | Contenido |
| --- | --- | --- |
| Nueva inscripción profesional | Administrador autorizado | Nombre, correo y especialidad; no implica identidad ni correo ya validados |
| Envío de documentos | Administrador autorizado | Profesional, referencia del envío, tipo y cantidad de archivos; nunca se adjunta el carnet |
| Reserva confirmada | Profesional titular | Referencia, box, fecha, inicio y término de 55 minutos en `America/Santiago`, dirección y acceso al sitio |
| Modificación o cancelación de reserva | Profesional titular | Comprobante actualizado o cancelación con devolución de sesión |

Los avisos nuevos se guardan en `notification_outbox` dentro de la misma transacción que registra la operación. Si una reserva falla por conflicto no se encola un comprobante. El envío ocurre después, fuera del bloqueo de la base, en un trabajador de fondo del servidor. Un fallo del proveedor no deshace una reserva válida ni descuenta otra sesión. No se generan avisos retrospectivos para registros anteriores a esta actualización.

Se reintenta hasta cinco veces con espera creciente. Resend recibe una clave de idempotencia estable para evitar duplicados ante respuestas perdidas; los reintentos inciertos se detienen antes de superar 23 horas desde el primer intento, dentro de la ventana de idempotencia del proveedor. SendGrid no ofrece esa misma garantía en esta implementación. Un cambio/cancelación sustituye comprobantes anteriores que aún no se habían enviado; un mensaje que ya salió no se puede retirar.

Administración → Correos muestra el estado de los avisos y el diagnóstico seguro de cada intento. Aceptado por el proveedor no significa entregado al buzón: revisa Resend y spam si corresponde. Los límites del plan de correo siguen aplicando. El trabajador necesita que el servicio esté ejecutándose y que la configuración de envío esté completa; si el servicio se suspende, el procesamiento se reanuda al volver a ejecutarse. La cola comparte las limitaciones de persistencia de la base local de Render: no reemplaza un disco persistente y un respaldo.

**Administración → Correos del día** permite elegir una fecha, ver los nombres y direcciones únicas de profesionales con reservas confirmadas, consultar sus boxes/horarios y copiar todos los correos. Una persona con varias reservas aparece una sola vez. Se excluyen cancelaciones y otros días. Las modificaciones cambian el consolidado inmediatamente al actualizar. Los límites del día se calculan en Chile, no en UTC. Las reservas de una cuenta desactivada siguen incluidas si no fueron canceladas.

El endpoint `/api/admin/daily-contacts?date=YYYY-MM-DD` comprueba sesión y permisos administrativos en cada consulta, no permite acceso profesional y responde sin caché. Los correos ajenos no se incorporan al calendario profesional. Este consolidado no envía mensajes masivos.

Prueba manual: crea una cuenta de prueba y comprueba el aviso al administrador y la verificación al profesional; adjunta archivos ficticios y revisa el aviso sin adjuntos; confirma una reserva y revisa su comprobante, luego modifica/cancela para comprobar las actualizaciones. Para el consolidado, agenda varios profesionales, repite uno en otro horario y comprueba que el correo se repita solo una vez y que las cancelaciones desaparezcan. Usa datos ficticios hasta contar con almacenamiento persistente.

## Iniciar sesión con Google

El acceso profesional con Google se activa al configurar el cliente OAuth en Render. Sigue [GOOGLE.md](GOOGLE.md) para crear el cliente y vincular cuentas existentes. El administrador mantiene su contraseña privada.

## Normas antes de comprar y dos documentos

`rules.py` contiene las 12 normas del propietario y genera su versión a partir del texto. `/normas` es público y usa la misma fuente que el modal de compra. Cada solicitud simulada y cada inicio o reanudación de Webpay exige una aceptación explícita de la versión vigente. La casilla comienza desmarcada. El servidor rechaza solicitudes sin aceptación válida antes de crear planes o contactar al banco. `purchase_acceptances` conserva titular, plan, fecha, versión y copia del texto; la fecha es visible en Administración → Pagos. No se inventan aceptaciones para planes históricos.

La documentación usa `identity` para carnet y `degree` para certificado de título; se envían y aprueban por separado. Se toma siempre la última revisión de cada tipo. Reemplazar cualquiera pausa nuevas reservas y modificaciones hasta su aprobación; las reservas anteriores permanecen. Los documentos antiguos `accreditation` no sustituyen ninguno de estos dos requisitos. Los clientes migrados con validación anterior reconocida conservan su exención de documentos.

El correo de reserva conserva profesional, espacio, fecha, horario, dirección, condiciones de modificación y enlace. La referencia permanece dentro de la plataforma, pero ya no figura en el correo; tampoco se muestran líneas de duración ni zona horaria. La fecha y hora siguen calculándose en America/Santiago.

Las reglas de sobretiempo y sanciones se comunican en las normas; esta actualización no implementa cargos automáticos adicionales a una tarjeta ni deducciones nuevas de saldo por esos conceptos.

## Selección de varias horas

En Disponibilidad, «Seleccionar varias horas» permite combinar fechas y boxes antes de confirmar. El resumen conserva la selección al cambiar de día, limita las horas al saldo pagado y ofrece «Agendar las restantes más adelante» cuando solo se usa parte del saldo. La selección es temporal, no bloquea horarios ni descuenta saldo; se pierde al recargar la página o cerrar sesión.

`POST /api/bookings/batch` recibe de 1 a 200 horarios y una clave de confirmación aleatoria. Requiere sesión, CSRF, correo y documentación aprobados (o validación migrada). Confirma todo en una transacción o nada ante saldo insuficiente o conflictos; usa primero los planes pagados más antiguos. La clave impide repetir reservas y descuentos al reintentar la misma confirmación. Rechaza una clave reutilizada con otra selección y horarios simultáneos del mismo profesional, incluso en distintos boxes. Cada hora conserva su reserva, comprobante y única modificación según las reglas vigentes.

El saldo no utilizado permanece en los planes del titular para futuras reservas. No se crean reservas ficticias para las horas pendientes de elegir.

## Cargas de horas, ficha profesional e ingresos mensuales

- **Administración → Cargar horas** permite acreditar de 1 a 200 sesiones a una cuenta activa por efectivo, transferencia o cortesía administrativa. Registra monto entero en CLP, fecha real de recepción (hasta hoy), referencia única para pagos, observación opcional y confirmación del administrador. Una cortesía registra monto cero y admite referencia vacía: se genera una referencia interna estable. La revisión final muestra cuenta y correo antes de confirmar. No ejecuta cobros bancarios ni aprueba documentación.
- La referencia se controla por medio de pago, sin distinguir mayúsculas; la misma confirmación es idempotente. La transacción crea plan, constancia privada y auditoría juntos. El profesional acepta las 12 normas desde **Paquetes** antes de usar ese saldo; conserva los requisitos de correo confirmado y documentación aprobada. Las referencias y observaciones internas no se exponen en su estado profesional.
- **Administración → Usuarios → nombre del profesional** abre una ficha de consulta con planes, horas acreditadas, atenciones realizadas, reservas pendientes/en curso, saldo disponible, historial de reservas, cargas externas y documentos. También se puede abrir desde nombres en Pagos, Cargar horas, Documentos y Reservas. No inicia sesión como el profesional. Las horas consumidas o reservadas por plan se distinguen de las atenciones finalizadas; el consumo previo a un saldo migrado no se inventa.
- **Administración → Pagos** muestra un selector mensual, total recibido, efectivo, transferencias y sus registros. Agrupa por fecha de recepción, no por fecha de reserva ni por fecha de captura del registro. Los montos son brutos registrados, sin deducir comisiones. Excluye pagos simulados, Webpay de integración, saldos migrados, solicitudes pendientes y cortesías. Los cobros reales Webpay continúan desactivados.
- En **Disponibilidad**, la agenda se presenta antes de las fotos de los boxes.

Validación de estos recorridos: `python3 tests/run_browser.py manual_credits_browser.cjs`. Comprueba carga manual, aceptación de normas, reserva con saldo, ficha móvil/escritorio, separación mensual de un pago anterior, exclusión de pagos simulados y orden agenda/fotos. Las pruebas de servidor incluyen permisos, CSRF, privacidad, fechas inválidas, concurrencia y prevención de cargas duplicadas.

## Confirmación de reservas y anticipación mínima

Después de una reserva individual o múltiple se presenta la confirmación con box, fecha, horario de Chile, dirección y saldo restante. Cada reserva tiene accesos **Agregar a mi calendario** (Google Calendar, evento de 55 minutos), **Cómo llegar** (Google Maps) y **Mis reservas**. Los accesos también permanecen en las reservas confirmadas y se incluyen en sus correos. El saldo del correo es una fotografía al confirmar esa reserva; el saldo vigente se consulta en la cuenta. Agregar un evento requiere guardarlo en Google Calendar y no sincroniza posteriores modificaciones automáticamente.

El enlace `/#mis-reservas` abre las reservas de la cuenta autenticada; si se requiere iniciar sesión, conserva ese destino. No incluye datos personales ni credenciales en la URL. Los enlaces de calendario usan instantes UTC y zona `America/Santiago` para conservar el horario de Chile.

Toda nueva reserva exige **al menos 24 horas completas** de anticipación, también en lotes y al elegir un nuevo horario en una modificación. Por ejemplo, a las 19:55 de hoy se puede reservar desde las 20:00 de mañana. La agenda deshabilita las horas anteriores a ese límite y el servidor vuelve a validarlo dentro de la transacción. Se conserva la regla de una sola modificación y al menos dos horas antes del horario original. Las reservas ya existentes no se cancelan por esta actualización. El cálculo de 24 horas usa tiempo transcurrido real, incluso durante cambios de horario de verano.

Prueba del recorrido: `python3 tests/run_browser.py confirmation_browser.cjs`. Incluye confirmación móvil, saldo, enlaces, acceso directo con/sin sesión y rechazos del servidor para reserva individual, lote y modificación con menos de 24 horas.

## Diseño de los correos de reserva

Las confirmaciones y modificaciones usan la alternativa de bienvenida cercana: saludo, tarjeta con fecha/horario/box, dirección, información en viñetas y tres botones verticales al final, en orden **Mis reservas → Agregar al calendario → Cómo llegar**. La cancelación tiene su propio texto y no invita a guardar un evento cancelado.

Resend recibe `html` y `text`; SendGrid recibe partes `text/plain` y `text/html`. La plantilla usa tablas, estilos en línea, texto alternativo y datos escapados. Se conserva la clave de idempotencia en los reintentos. Recuperación, confirmación de correo y avisos administrativos mantienen su formato anterior.

La vista de ejemplo está en `vista-previa-correo.html`. El logo original entregado por el propietario está en `static/images/valhalla-logo.png`, conservado sin modificaciones. La plantilla lo muestra a 200 píxeles de ancho con proporciones originales y una URL pública absoluta. Los clientes que bloquean imágenes muestran el texto alternativo Valhalla.

## Avisos de aprobación y actualización automática

Al aprobar un carnet o certificado de título se encola un correo personal al titular, dentro de la misma transacción. Cada aviso identifica el documento aprobado, incluye el logo oficial y un botón para ir a la cuenta, y recuerda los requisitos para reservar sin afirmar que una aprobación aislada habilita todas las reservas. Se envían versiones HTML y texto.

Repetir la misma aprobación no duplica el mensaje; una nueva revisión después de un rechazo tiene su propio aviso. Los errores del proveedor conservan la aprobación y usan el reintento existente con la misma clave de entrega. Una sustitución del documento, un cambio de nombre o un rechazo invalida avisos anteriores que aún no se enviaron. Los documentos que ya estaban aprobados antes de esta versión no generan envíos retroactivos.

La cuenta consulta novedades cada **30 segundos**, solo mientras la pestaña está visible, y al volver a ella. Administración muestra nuevas cuentas, documentos, pagos y reservas; el profesional ve las aprobaciones y su saldo actualizados. La agenda también renueva disponibilidad cuando no hay una selección o modificación en curso. Se conservan la pestaña, filtros y posición de desplazamiento.

La actualización visual se pausa si hay un formulario con cambios, un campo enfocado, una operación de formulario deshabilitada o un diálogo abierto. Una respuesta que llega durante la edición no reemplaza el formulario; se conserva la actualización pendiente para cuando pueda mostrarse. Fallos temporales de conexión no interrumpen la pantalla y se vuelven a consultar en el siguiente ciclo.

Pruebas específicas: `python3 -m unittest discover -s tests -p test_approval_notifications.py` y `python3 tests/run_browser.py live_updates_browser.cjs`. El navegador acelera únicamente el temporizador de pruebas, verificando que producción solicita intervalos de 30 segundos, y cubre nuevas cuentas sin F5, formularios, revisiones, respuestas tardías y los dos avisos de aprobación.

## Ajustes manuales de saldo y planes archivados

En **Administración → Usuarios → nombre → Planes y saldos**, cada plan confirmado tiene **Ajustar saldo**. Permite sumar o descontar de 1 a 200 sesiones por movimiento, con motivo opcional y vista previa del saldo final. La confirmación registra plan, cuenta, administrador, fecha, saldo anterior, diferencia y saldo final. El historial queda en la ficha del profesional, visible únicamente para administración.

El ajuste modifica solo el saldo disponible: conserva el precio, las horas iniciales, el origen y las reservas del plan. No registra un pago ni aumenta los ingresos mensuales. Las tablas distinguen horas acreditadas originalmente, ajustes netos y sesiones consumidas/reservadas dentro del sistema; el uso externo durante la migración se refleja como ajuste. Se rechaza un saldo negativo, un plan ajeno o pendiente y una confirmación basada en saldo desactualizado. Reintentar la misma confirmación no duplica el ajuste.

Los planes pendientes o rechazados, sin pago acreditado ni reservas asociadas, tienen **Eliminar de la vista** en la ficha y en Pagos. La operación archiva el plan y su intento de pago, con motivo y registro permanente. Desaparecen de las listas habituales del administrador y profesional, y del conteo de pendientes. **Mostrar planes archivados** permite consultarlos. No hay borrado de registros financieros, pagos confirmados ni reservas.

Archivar no anula ni devuelve una operación en Transbank. Se exige comprobar primero los pagos con resultado incierto, una conciliación en curso o una confirmación iniciada. Un intento archivado conocido como pendiente deja de impedir iniciar otra compra. Su token sigue disponible para la conciliación segura: si más tarde se verifica una aprobación legítima, se recupera el plan y se acredita una sola vez. El motivo del archivo permanece en su historial incluso después de esa recuperación.

La base existente se amplía conservando sus planes y saldos; no se requiere volver a importar clientes. Pruebas: `python3 -m unittest discover -s tests -p test_balance_archive.py` y `python3 tests/run_browser.py balance_archive_browser.cjs`. El navegador comprueba saldo migrado 5 → 2 → 4, rechazo de saldo negativo, archivo de Webpay desde la ficha, consulta del historial y nueva compra en móvil, usando un banco simulado localmente.

Los motivos, observaciones y comentarios administrativos son opcionales, también al ocultar planes, revisar documentos e importar saldos. Se conservan las validaciones de identidad, consentimiento y datos operativos.
