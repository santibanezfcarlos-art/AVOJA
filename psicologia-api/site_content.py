"""Versioned public copy; editing is restricted to the existing private admin."""
import json
import sqlite3
from pathlib import Path
from flask import jsonify,request
import billing

SCHEMA=json.loads((Path(__file__).parent/'site_content_defaults.json').read_text())
FIELDS=SCHEMA['fields']

def content_config():
    values={key:field['default'] for key,field in FIELDS.items()}
    if not billing.configured():return values,0
    with billing.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS admin_site_content (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL, version INTEGER NOT NULL)')
        row=con.execute('SELECT value,version FROM admin_site_content WHERE id=1').fetchone()
    if row:
        values.update({k:v for k,v in json.loads(row['value']).items() if k in FIELDS})
    return values,row['version'] if row else 0

def validate_changes(body):
    if not isinstance(body,dict) or type(body.get('version')) is not int:
        raise ValueError('Versión inválida. Recarga los textos antes de guardar.')
    changes=body.get('changes')
    if not isinstance(changes,dict) or not changes or len(changes)>len(FIELDS):
        raise ValueError('Selecciona una sección y revisa los textos.')
    for key,value in changes.items():
        if key not in FIELDS or not isinstance(value,str) or not value.strip() or len(value)>3000 or any(ord(c)<32 and c not in '\n\t' for c in value):
            raise ValueError('Cada texto debe tener entre 1 y 3000 caracteres.')
    return changes

def register(bp):
    @bp.get('/api/site-content')
    def public_site_content():
        try:
            values,version=content_config()
            response=jsonify(version=version,content={key:{'node':f['node'],'index':f['index'],'value':values[key]} for key,f in FIELDS.items()})
            response.headers['Cache-Control']='no-store'
            return response
        except (RuntimeError,sqlite3.Error,OSError):
            return jsonify(error='Contenido temporalmente no disponible.'),503

    @bp.route('/admin/site-content',methods=['GET','POST'])
    @billing.private
    def manage_site_content():
        if request.method=='GET':
            values,version=content_config()
            return jsonify(schema=SCHEMA,values=values,version=version)
        if (request.content_length or 0)>650000:return jsonify(error='Contenido demasiado extenso.'),413
        body=request.get_json(silent=True)
        try:changes=validate_changes(body)
        except ValueError as exc:return jsonify(error=str(exc)),400
        content_config()
        with billing.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT value,version FROM admin_site_content WHERE id=1').fetchone()
            version=row['version'] if row else 0
            if version!=body['version']:
                return jsonify(error='Otra edición fue guardada. Recarga los textos para no sobrescribirla.'),409
            saved=json.loads(row['value']) if row else {}
            saved.update(changes)
            con.execute('INSERT INTO admin_site_content VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,version=excluded.version',
                        (json.dumps(saved,ensure_ascii=False),version+1))
            con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                        ('site_content','content_updated',billing.now(),request.authorization.username))
        values={key:saved.get(key,f['default']) for key,f in FIELDS.items()}
        return jsonify(ok=True,values=values,version=version+1)
