# Valhalla · WhatsApp para Windows

Programa local propio, gratuito, Python 3.12 (64 bits), Windows 10/11, WhatsApp de Windows ya instalado y conectado. No necesita suscripciones.

## Primera ejecución: solo tu número

1. Extrae este ZIP en una carpeta de tu computador (no lo ejecutes dentro del ZIP).
2. Abre WhatsApp. Si falta Python, instala Python 3.12 de 64 bits desde https://www.python.org/downloads/windows/ incluyendo el lanzador `py`.
3. Haz doble clic en **Iniciar.cmd**. Crea su entorno e instala las dependencias gratuitas automáticamente. No requiere administrador.
4. Escribe **tu propio número** y pulsa **Enviar una prueba a mi número**. Revisa el número y confirma. No se conecta a la campaña ni accede a clientes durante esta prueba.
5. Mantén WhatsApp visible y no uses teclado, ratón ni otro programa durante el intento.

Mensaje de prueba:

¡Hola! 👋 Prueba de Valhalla Office.
Emojis, tildes y conexión: todo en orden. 🌿

El teléfono debe aparecer completo en un botón del encabezado del chat. Un nombre guardado, «Tú», una foto o el número escrito en otro mensaje no prueban la identidad. Si tu versión no expone ese encabezado, el programa se detiene antes de escribir. Si ajustas el perfil tras una prueba detenida, puedes solicitar una **nueva prueba manual a tu propio número**: exige confirmar primero que revisaste la conversación y crea un registro separado. Nunca repite una prueba por sí solo.

No se considera que haya funcionado hasta ver `runner_confirmed_ui` y el mensaje real en tu WhatsApp.

## Campañas: únicamente después de autorizar una

1. En Administración → WhatsApp de https://valhallaoffice.cl selecciona una campaña guardada y aplica el filtro y la búsqueda deseados. El panel muestra los conteos **actuales** de coincidencias por correo; no se reutilizan conteos históricos.
2. Pulsa **Conectar programa Windows**. Incluye todos los pendientes del filtro completo, no solo la página de 40 filas. La selección y el mensaje quedan limitados a esa campaña. La conexión no envía nada.
3. Copia la clave temporal de 8 horas al programa y pulsa **Cargar selección y conteos actuales**. No pegues esta clave en un chat, correo o repositorio. No contiene tu contraseña; da acceso a la selección de la campaña.
4. Solo después de la prueba comprobada, marca la autorización de campaña, pulsa **Revisar e iniciar campaña**, revisa el mensaje y los nombres/números, y confirma en esa ventana. Por defecto no se inicia ninguna campaña.

Los enlaces se envían como texto simple. Los mensajes se personalizan en el servidor con el nombre actual. Se comprueban nuevamente las bajas y el estado antes de enviar. Las omisiones, bajas y estados previos se conservan. Si alguien da de baja a un contacto en el intervalo mínimo entre la última comprobación y el clic, puede que ese clic ya ocurra; detener no retira mensajes ya accionados.

## Pausar, detener y retomar

- **Pausar / continuar** suspende las siguientes acciones; una llamada UIA ya iniciada puede terminar. Al continuar se revalida destinatario, contenido y primer plano antes de Enviar.
- **Detener** termina el proceso en el siguiente punto de control. Cualquier resultado posterior a preparar la acción de Enviar que no pueda verificarse queda incierto.
- Para retomar, carga nuevamente la selección y revisa/inicia. El registro local y la reserva en el servidor impiden volver a intentar el mismo teléfono en esa campaña, incluso después de reiniciar el programa o restablecer el estado visual del panel.
- Un fallo de destinatario, editor, red o comprobación detiene la ejecución completa. No se sigue enviando a otros clientes con un fallo sin resolver.
- Los intentos fallidos o inciertos **no se desbloquean automáticamente**. «Restablecer» en el panel no elimina la protección del registro de intentos. Requieren revisión humana antes de cualquier nueva decisión de envío; esta versión no ofrece un botón para forzar el reenvío.

## Estados y persistencia

| Estado | Significado |
| --- | --- |
| runner_claimed | Intento reservado, aún sin preparar |
| runner_prepared | Número y borrador completo comprobados |
| runner_actioned | Acción de envío registrada anticipadamente; el clic puede haber ocurrido o no |
| runner_confirmed_ui | Nueva burbuja saliente, texto exacto y estado enviado detectados por accesibilidad |
| runner_uncertain | No se pudo confirmar lo ocurrido; no reenviar |
| runner_failed | Detenido antes de intentar enviar; tampoco se reintenta automáticamente |

Ninguno de estos estados equivale a «entregado» o «leído» por el destinatario. La marca anticipada `runner_actioned` favorece evitar duplicados si el equipo se interrumpe justo durante el clic.

El registro SQLite se guarda en `%LOCALAPPDATA%\ValhallaWhatsApp`, protegido para tu usuario y SYSTEM por el lanzador. Guarda campaña, teléfono, hash del mensaje, estados y pendientes de sincronización. No guarda contraseñas, sesión de WhatsApp ni el texto completo de campañas. Las claves y las colas permanecen en memoria. Los resultados sin confirmar en el servidor se sincronizan al reconectar con la misma clave mientras siga vigente; si vence antes, el intento continúa bloqueado en el servidor y el resultado exacto queda en el registro local para revisión. Nunca se reenvía por un error de sincronización.

## Si WhatsApp no expone los controles esperados

Las versiones de WhatsApp pueden cambiar sus controles. **Los selectores de esta distribución son iniciales y aún requieren validación en tu Windows**. No se reemplazan por clics en coordenadas cuando faltan.

Pulsa **Diagnosticar UIA**. Guarda `diagnostico-uia-privado.json` en la carpeta del registro. Puede contener nombres y conversaciones; no lo publiques ni lo adjuntes completo sin revisar sus datos. `uia-profile.json`, en la misma carpeta, permite adaptar selectores UIA para editor, encabezado, botón Enviar y burbujas salientes usando los nombres y AutomationId reales del diagnóstico. Después de modificarlo, el siguiente intento vuelve a cargarlo. El encabezado debe exponer el teléfono exacto, y la comprobación exige dirección saliente y estado enviado; no rebajes estas verificaciones a un nombre o texto genérico.

La escritura usa UIA ValuePattern cuando está disponible; como respaldo, pega texto Unicode con Ctrl+V después de comprobar el foco del editor específico y el primer plano de WhatsApp. En ese respaldo se reemplaza el contenido del portapapeles. Nunca pulsa Enter global ni utiliza coordenadas para Enviar: invoca el control accesible. No sobrescribe borradores previos.

## Validación realizada y pendiente

Pasaron 19 pruebas específicas de campañas y automatización. Se probaron en un entorno Linux la máquina de estados, recuperación tras interrupciones, bloqueo de duplicados, pérdida de red, exclusiones, bajas sobrevenidas, personalización Unicode, autenticación, CSRF y concurrencia con datos sintéticos. La interfaz de Windows, sus dependencias, PowerShell y los selectores reales de WhatsApp **no se ejecutaron aquí**. La prueba real con tu propio número está pendiente. No se enviaron mensajes ni campañas a clientes.

Los conteos de producción requieren iniciar sesión como administrador: el programa los consulta en vivo al conectar. No se presentan 618/32/586 como cifras verificadas.

La revisión general ejecutó 288 pruebas con 4 fallos. Se reprodujeron los mismos cuatro en la revisión original `65aa4e6`, sin estos cambios: anticipación de reservas por lote, enlaces de correo, disponibilidad con una fecha pasada y entrega Skedu con fecha vencida. No se modificaron esas funciones.
