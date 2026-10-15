"""Build a self-contained HTML preview; the real server keeps its original API."""
import base64
import json
from pathlib import Path

root = Path(__file__).resolve().parent
js = (root / 'static/app.js').read_text()
start = js.index('async function api(')
end = js.index('function toast(', start)
js = js[:start] + (root / 'preview-api.js').read_text() + '\n' + js[end:]
js = js.replace('Cada persona utiliza su propia cuenta. No hay accesos automáticos.', 'Vista previa local: regístrate para explorar. Los datos se reinician al recargar. La administración privada funciona en el sitio con servidor.')
photos=[]
for box in (1,2,3):
    for extension,mime in [('jpg','image/jpeg'),('jpeg','image/jpeg'),('png','image/png'),('webp','image/webp')]:
        image=root/'static'/'images'/f'box-{box}.{extension}'
        if image.is_file():
            photos.append({'box':box,'image_url':f'data:{mime};base64,'+base64.b64encode(image.read_bytes()).decode()})
            break
js='globalThis.VALHALLA_ROOMS='+json.dumps(photos)+';\n'+js
css = (root / 'static/style.css').read_text()
html = f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Valhalla Office — Vista previa interactiva</title><style>{css}</style></head>
<body><div id="toast" role="status" aria-live="polite" hidden></div>
<div id="root"></div><dialog id="modal" aria-labelledby="modal-title"></dialog>
<script>{js}</script></body></html>'''
(root / 'vista-previa.html').write_text(html)
print('Generada valhalla/vista-previa.html: HTML autónomo sin solicitudes de red.')
