# Acceso profesional con Google

La aplicación admite «Continuar con Google» para registro e inicio de sesión profesional. El botón permanece oculto hasta configurar Google en Render. La integración usa autorización de servidor con código, PKCE S256 y estado de un solo uso ligado al navegador (10 minutos). Solo solicita `openid email profile`; no accede a Gmail, contactos ni calendario. No guarda tokens de Google.

## Configuración en Google Cloud

1. Abre https://console.cloud.google.com/ y selecciona o crea un proyecto para Valhalla Office.
2. En **Google Auth Platform**, configura **Branding** (nombre Valhalla Office, correo de soporte y contacto). Añade `valhallaoffice.cl` como dominio autorizado. Completa los enlaces públicos: inicio `https://valhallaoffice.cl`, privacidad `https://valhallaoffice.cl/privacidad` y condiciones `https://valhallaoffice.cl/condiciones`. Estas páginas legales son accesibles sin sesión ni JavaScript y están enlazadas desde el inicio.
3. En **Audience**, selecciona **External** para admitir clientes externos. Si la aplicación está en Testing, añade los correos que probarán el acceso como usuarios de prueba. Para abrirla al público, cambia el estado de publicación y completa cualquier revisión que Google exija.
4. En **Clients → Create client**, elige **Web application**. Añade exactamente esta **Authorized redirect URI**:

   ```text
   https://valhallaoffice.cl/api/google/callback
   ```

   No uses el dominio de Render ni una barra final. No hacen falta orígenes JavaScript para este flujo de servidor. No solicites permisos de Gmail.
5. Copia el ID de cliente y su secreto directamente a **Render → valhalla-office-demo → Environment**:

   | Variable | Valor |
   | --- | --- |
   | `VALHALLA_GOOGLE_CLIENT_ID` | ID de cliente de Google (termina en `.apps.googleusercontent.com`) |
   | `VALHALLA_GOOGLE_CLIENT_SECRET` | Secreto del cliente, solo en Render |
   | `VALHALLA_PUBLIC_URL` | `https://valhallaoffice.cl` |

   Conserva las variables de administrador y Resend existentes. Guarda y aplica el despliegue. Nunca publiques el secreto en Git, capturas ni conversaciones.
6. Abre **https://valhallaoffice.cl** y prueba «Continuar con Google». El dominio alternativo de Render no admite comenzar este flujo porque la cookie y el retorno deben pertenecer al dominio canónico.

## Comportamiento

- Nuevo usuario: crea una cuenta profesional y confirma su correo mediante Google. Debe completar su profesión en Mi perfil; conserva los requisitos de identidad y saldo para reservar.
- Cliente migrado: al coincidir el correo, recibe el saldo importado y la validación anterior. Conserva el nombre validado de la planilla, sin reemplazarlo por el nombre público de Google.
- Cuenta ya existente con contraseña: primero inicia sesión con su contraseña y elige **Mi perfil → Vincular Google**, usando la misma dirección. No se fusionan cuentas solo por compartir un correo. Si olvidó su contraseña, usa la recuperación existente.
- Cuenta vinculada: los siguientes accesos con Google entran a la misma cuenta y conservan sus datos, saldo y reservas. Puede establecer una contraseña mediante la recuperación por correo si la necesita.
- Administrador: mantiene el acceso exclusivo con contraseña privada; no se registra ni entra como administrador con Google.
- Google confirma el correo, no el carnet. Las reglas de modificación (una vez, con al menos dos horas de anticipación) y de cancelación exclusiva de administración se mantienen.

## Verificación técnica

Desde `valhalla/`:

```sh
python3 -m unittest discover -s tests -q
python3 tests/run_browser.py google_browser.cjs
python3 tests/run_browser.py
```

Las pruebas usan respuestas de Google simuladas, una base temporal y correo local; no requieren credenciales ni contactan usuarios. La prueba con Google real debe hacerse tras configurar el cliente en Render. El endpoint público `/api/google/config` solo expone si el acceso está habilitado, nunca sus claves.
