# Inicio de sesión profesional con Microsoft

Permite entrar con una cuenta personal Hotmail, Outlook, Live o MSN. El botón aparece en el inicio y el registro cuando las credenciales están completas en Render. Los dominios admitidos son `hotmail.com`, `outlook.com`, `live.com`, `msn.com`, `hotmail.es`, `outlook.es` y `live.cl`. Las cuentas corporativas/educativas y los alias externos pueden seguir usando el correo y contraseña de Valhalla.

## Activación en Microsoft Entra y Render

1. Abre https://entra.microsoft.com/ → **Identidad → Aplicaciones → Registros de aplicaciones → Nuevo registro**. También puedes acceder desde https://portal.azure.com/ buscando **Registros de aplicaciones**. Usa la cuenta Microsoft que administrará la integración de Valhalla; puede ser necesario disponer de un directorio Entra para gestionar el registro.
2. Nombre: **Valhalla Office**. En tipos de cuenta compatibles, admite **cuentas personales de Microsoft** (o la opción que incluye cuentas de cualquier directorio y cuentas personales). El inicio de Valhalla usa la autoridad `consumers`, exclusivamente para cuentas personales.
3. En URI de redirección selecciona la plataforma **Web**, con este valor exacto:

   ```text
   https://valhallaoffice.cl/api/microsoft/callback
   ```

   No selecciones SPA ni habilites flujos implícitos. El flujo es código de autorización de servidor con PKCE.
4. En **Información general**, copia el **Id. de aplicación (cliente)**. En **Certificados y secretos → Secretos de cliente → Nuevo secreto de cliente**, genera un secreto y copia su **Valor**, no su identificador. Conserva su vencimiento para renovarlo antes de que expire.
5. En **Render → valhalla-office-demo → Environment**, configura:

   | Variable | Valor |
   | --- | --- |
   | `VALHALLA_MICROSOFT_CLIENT_ID` | Id. de aplicación (cliente) de Microsoft, con formato UUID |
   | `VALHALLA_MICROSOFT_CLIENT_SECRET` | Valor del secreto, solo en Render |
   | `VALHALLA_PUBLIC_URL` | `https://valhallaoffice.cl` (la misma configuración existente) |

   Guarda y despliega. Conserva las credenciales existentes de Google, Transbank, correo y administración. No compartas el secreto por chat, capturas ni Git.
6. Abre https://valhallaoffice.cl e ingresa con **Ingresar con Microsoft**. Microsoft solicitará `openid profile email`; no se solicitan permisos para leer correo, contactos, archivos ni calendarios. No se guardan sus tokens.

## Cuentas y saldos

- Un nuevo cliente obtiene una cuenta profesional, debe completar profesión/especialidad y conserva los requisitos de documentación y saldo.
- Si su correo está en una migración pendiente, se vinculan su nombre validado, saldo e identidad previa una sola vez. La migración no genera avisos de carga de horas.
- Una cuenta existente con contraseña debe entrar primero con esa contraseña y usar **Mi perfil → Vincular Microsoft**. Si la olvidó puede recuperarla por correo. Un correo coincidente no fusiona automáticamente cuentas.
- Una cuenta ya vinculada vuelve a entrar a su mismo perfil sin duplicar clientes, planes o reservas.
- El acceso administrador permanece con contraseña privada. Los bloqueos, desactivaciones y eliminaciones se aplican también a Microsoft.

## Validación

La integración verifica la firma RS256 contra las claves públicas de Microsoft obtenidas por HTTPS, el emisor y tenant de cuentas personales, destinatario, vigencia y nonce del ID token. Contrasta su sujeto con el perfil HTTPS de Microsoft. El estado es de un solo uso, ligado a una cookie segura del navegador y separado del estado de Google; utiliza PKCE S256.

Pruebas locales, sin tráfico real a Microsoft:

```sh
python3 -m unittest discover -s tests -p test_microsoft_auth.py
python3 tests/run_browser.py microsoft_browser.cjs
python3 tests/run_browser.py google_browser.cjs
```

La prueba de una cuenta real requiere la activación anterior. `/api/microsoft/config` solo informa si está habilitado, nunca muestra credenciales.
