"""Build a self-contained HTML preview; the real server keeps its original API."""
import json
from app import gallery_items
from rules import purchase_rules
from pathlib import Path

root = Path(__file__).resolve().parent
js = (root / 'static/app.js').read_text().replace('src="/images/valhalla-logo.png"','src="https://valhallaoffice.cl/images/valhalla-logo.png"')
start = js.index('async function api(')
end = js.index('function toast(', start)
js = js[:start] + (root / 'preview-api.js').read_text() + '\n' + js[end:]
js = js.replace('Cada persona utiliza su propia cuenta. No hay accesos automáticos.', 'Vista previa local: regístrate para explorar. Los datos se reinician al recargar. La administración privada funciona en el sitio con servidor.')
# Keep the preview lightweight; all original photos live on the published service.
# UI and demo state remain local; only public photo requests use this origin.
groups=gallery_items()
for group in groups:
    for photo in group['photos']:
        photo['image_url']='https://valhalla-office-demo.onrender.com'+photo['image_url']
photos=[{'box':group['box'],'image_url':group['photos'][0]['image_url'],'photos':group['photos']}
        for group in sorted((g for g in groups if g['box']),key=lambda g:g['box'])]
js='globalThis.VALHALLA_RULES='+json.dumps(purchase_rules(),ensure_ascii=False)+';\n'+js
js='globalThis.VALHALLA_GALLERY='+json.dumps(groups,ensure_ascii=False)+';\n'+js
js='globalThis.VALHALLA_ROOMS='+json.dumps(photos,ensure_ascii=False)+';\n'+js
css = (root / 'static/style.css').read_text()
html = f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Valhalla Office — Vista previa interactiva</title><link rel="icon" type="image/png" href="https://valhallaoffice.cl/images/valhalla-logo.png"><style>{css}</style></head>
<body><div id="toast" role="status" aria-live="polite" hidden></div>
<div id="root"></div><dialog id="modal" aria-labelledby="modal-title"></dialog>
<script>{js}</script></body></html>'''
(root / 'vista-previa.html').write_text(html)
print('Generada valhalla/vista-previa.html: interfaz autónoma con fotos públicas del sitio.')
