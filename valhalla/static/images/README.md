# Galería Valhalla Office

Las imágenes originales cargadas en GitHub se mantienen sin recortar ni modificar:

| Archivos | Espacio | Box reservable |
| --- | --- | --- |
| 1.png | Entrada | No |
| 2.png, 3.png | Recepción | No |
| 6.png, 7.png | Box infantil | 3 |
| 11.png, 12.png | Box 1 | 1 |
| 13.png, 14.png, 15.png | Box 2 | 2 |

El propietario confirmó que el box infantil es el tercero. Cambiar el nombre visible no modifica los identificadores de reservas ni crea un cuarto box.

Las fotos se muestran agrupadas en el inicio público y en el panel profesional. Disponibilidad enlaza solo las fotos de los tres boxes; entrada y recepción no son reservables. El visor permite avanzar y retroceder, conserva la imagen completa y cierra con botón o Escape.

Para reemplazar una foto, conserva el nombre exacto y formato real del archivo. Para añadir otras, actualiza `GALLERY_GROUPS` en `app.py`; únicamente se sirven los archivos permitidos. Se mantiene compatibilidad con `box-1.jpg/png`, `box-2.jpg/png` y `box-3.jpg/png` cuando no existen las fotos numeradas del box.

Las imágenes son públicas: no incluyas datos de pacientes ni documentos privados. El despliegue Docker copia la carpeta static desde Git, por lo que estas fotos no dependen del almacenamiento temporal de SQLite. `python3 build_preview.py` regenera la vista del chat; para evitar un HTML de muchos megabytes, enlaza las fotos públicas del sitio publicado. Las cuentas y operaciones de la vista previa permanecen simuladas y locales.
