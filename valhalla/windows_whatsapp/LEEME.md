# Valhalla · WhatsApp Windows v1.10

## Continuar después de una prueba que ya llegó

1. Extrae todo el ZIP en una carpeta nueva y ejecuta Iniciar.cmd. No ejecutes desde dentro del ZIP.
2. Escribe tu número y pulsa **Registrar prueba ya enviada · sin reenviar** si el mensaje ya llegó. Conserva el registro local de versiones anteriores.
3. En https://valhallaoffice.cl entra en **Administración → WhatsApp**. Los contactos ya importados se seleccionan allí; no vuelvas a importar el Excel salvo que quieras actualizar los datos.
4. Crea una campaña con el mensaje definitivo. Usa {nombre} para personalizar. Para cambiar el mensaje de una campaña existente, crea otra campaña; revisa sus destinatarios porque los envíos se excluyen por campaña.
5. Selecciona la campaña y aplica el filtro de destinatarios. Revisa los conteos actuales; las cifras antiguas no sustituyen esta revisión.
6. Pulsa **Conectar programa Windows** y **Copiar clave**. La selección comprende el filtro completo, no solo la página visible.
7. Pega la clave temporal en el programa y pulsa **Cargar selección y conteos actuales (sin enviar)**.
8. Revisa mensaje y destinatarios. Marca la autorización y pulsa **Revisar e iniciar campaña**; revisa la vista previa antes de confirmar.

No envíes a clientes hasta definir el mensaje y la selección. La clave caduca; no la compartas ni la guardes en repositorios.

## Estados y continuidad

El registro persistente está en %LOCALAPPDATA%\ValhallaWhatsApp. No borres registro.sqlite3 para actualizar.

El programa distingue preparación, acción de Enviar y comprobación en interfaz. Si Enviar termina pero no aparece confirmación accesible, registra runner_uncertain, bloquea ese teléfono y continúa con el siguiente. Nunca lo marca como entregado ni lo reintenta automáticamente. Si falla el chat, el contenido, la acción de enviar o la sincronización, detiene la ejecución para revisar. La prueba personal incierta también se detiene: si observaste el mensaje enviado usa el botón de registro manual.

Pausar / continuar y Detener permiten interrumpir. Retomar conserva los intentos registrados; las bajas y omitidos se respetan. Mantén WhatsApp abierto y evita usar teclado o ratón durante los intentos.

## Requisitos

Windows 10/11, WhatsApp instalado y conectado, Python 3.12 de 64 bits con lanzador py. Iniciar.cmd ejecuta el lanzador PowerShell que prepara el entorno e instala requirements.txt. No requiere suscripciones ni administrador.

El diagnóstico puede contener datos privados: no lo publiques. Los archivos de diagnóstico se guardan junto al registro local. No se incluyen contraseñas, sesiones ni contactos en esta distribución.

## Validación

La versión anterior envió la prueba personal según la conversación y captura del usuario. Esta actualización se verifica con pruebas automatizadas de estados y bloqueo de duplicados; su continuidad real en Windows debe validarse en el computador del usuario. Registrar una observación manual no equivale a confirmación automática UIA.
