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

En **Perfil profesional → Mi perfil** puedes completar nombre y especialidad. Esa misma cuenta tiene paquetes y reservas personales vinculados a su identificador, sin transferencias ni horas gratuitas. Solicita un paquete, vuelve a Administración para confirmar el pago simulado y regresa al perfil profesional para reservar. Se aplican las mismas restricciones de saldo, conflictos y modificaciones. La cuenta propietaria conserva su verificación inicial; si adjuntas documentación nueva, el perfil profesional vuelve a revisión sin perder la administración.

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

- Tres boxes, sesiones de una hora, todos los días de 08:00 a 20:00, zona `America/Santiago`, hasta 90 días hacia adelante.
- Paquetes personales de 5, 10 y 20 horas; precios provisionales de $45.000, $85.000 y $160.000 CLP, sin vencimiento en este prototipo. Se comparten entre boxes, no entre profesionales.
- Solo el profesional activo y verificado reserva con saldo confirmado. Se consume el paquete confirmado más antiguo con saldo.
- Una transacción crea la reserva y descuenta el saldo. Un índice único protege el box y horario; el profesional tampoco puede reservar dos boxes simultáneamente. Un trigger impide transferir el paquete.
- Una modificación por reserva, al menos dos horas antes tanto del horario original como del nuevo. No consume otra hora; un intento fallido no gasta el cambio.
- Administración revisa el último PDF privado (hasta 2 MB), aprueba o rechaza con observaciones, confirma pagos simulados, activa usuarios y cancela reservas futuras devolviendo la hora una sola vez.
- Subir un documento nuevo pausa la creación y modificación de reservas hasta su aprobación; conserva las reservas existentes.
- Contraseñas con PBKDF2 y sal, sesiones HttpOnly/SameSite de ocho horas, protección CSRF, permisos comprobados en servidor y registro de operaciones. Los PDFs se descargan como adjuntos solo por el titular o administrador.

Entidades: `users`, `sessions`, `documents`, `packages`, `bookings` y `audit`. Los horarios se guardan en UTC. La API y la interfaz usan el mismo origen.

Para producción: PostgreSQL, almacenamiento privado de documentos, migraciones, límites de intentos, recuperación y verificación de correo, MFA administrativo, análisis de archivos, copias de seguridad y monitoreo. La revisión manual del PDF no certifica su autenticidad oficial. Precios, feriados, vencimientos y política definitiva de cancelación siguen siendo decisiones de producto. Ningún cobro real está implementado.

## Pruebas

```bash
cd /workspace/AVOJA/valhalla
python3 -m unittest discover -s tests -v
```

Las bases son temporales. Se cubren concurrencia, saldos, conflictos, límites de modificación, documentos, permisos, retiro de cuentas demo, reserva del correo administrador, contraseña privada y revocación de sesiones.

Prueba opcional de navegador: `python3 tests/run_browser.py` inicia una instancia descartable con una contraseña aleatoria, ejecuta el recorrido y la detiene. Requiere Node, Playwright y Chromium. Para un servidor de pruebas que ya esté iniciado, usa `node tests/browser.cjs`. Ejecuta solo contra una instancia aislada; configura `VALHALLA_TEST_URL` y `VALHALLA_TEST_ADMIN_PASSWORD` con la URL local y la contraseña privada **de esa instancia de pruebas**, nunca la del sitio publicado. Recorre registro, verificación, pago simulado, reserva, modificación, cancelación, desactivación y móvil.

Prueba manual: abre una ventana privada → Acceso profesional → crea una cuenta nueva → adjunta PDF ficticio → solicita paquete → entra como administrador para aprobar documento y pago → vuelve como profesional y reserva. Comprueba también que los accesos antiguos y el registro con el correo administrador se rechazan.

## Vista previa dentro del chat

`vista-previa.html` es autónomo y no se conecta a la base del sitio. Ahora abre el inicio con campos vacíos, sin cuentas precreadas. Permite explorar el registro con datos ficticios en memoria. No permite acceder a la administración privada: esa función se prueba en el servidor. Los datos se pierden al recargar. Regenera el archivo con `python3 build_preview.py` después de cambiar la interfaz o `preview-api.js`.

El bingo original de AVOJA permanece intacto.
