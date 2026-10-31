$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Selecciona Iniciar.cmd de la carpeta EXTRAIDA v1.10'
$dialog.Filter = 'Iniciar.cmd|Iniciar.cmd'
if ($dialog.ShowDialog() -ne 'OK') { return }
$dir = Split-Path -Parent $dialog.FileName
$env:VALHALLA_REPAIR_DIR = $dir
$repair = @'
import os, pathlib, py_compile, shutil, datetime
b=pathlib.Path(os.environ['VALHALLA_REPAIR_DIR'])
a=b/'app.py'; w=b/'windows_uia.py'
x=a.read_text(encoding='utf-8-sig'); y=w.read_text(encoding='utf-8-sig')
if 'Windows v1.11' in x:
    print('Reparacion ya instalada.'); raise SystemExit(0)
assert 'Windows v1.10' in x, 'Selecciona la carpeta extraida v1.10'
needle="            except Exception as e:self.log(getattr(e,'safe_reason','Operación detenida. No se reenvía. Revisa conexión, registro y diagnóstico UIA.'))"
assert needle in x and '    def clipboard_text(self,editor):' in y, 'Los archivos no coinciden; no se modificaron'
x=x.replace('import uuid','import uuid\nimport traceback').replace('Windows v1.10','Windows v1.11')
x=x.replace(needle,"""            except Exception as e:
                diagnostic=FOLDER/'ultimo-error-privado.txt'
                try:diagnostic.write_text(traceback.format_exc(),encoding='utf-8')
                except OSError:pass
                self.log(getattr(e,'safe_reason','Fallo '+type(e).__name__+'. No se reenvía.'))
                self.log('Detalle privado: '+str(diagnostic))""")
y=y.replace('    def clipboard_text(self,editor):', '''    def open_clipboard(self,clipboard):
        deadline=time.monotonic()+2
        while True:
            self.gate()
            try:
                clipboard.OpenClipboard()
                return
            except Exception:
                if time.monotonic()>=deadline:
                    raise VerificationError('El portapapeles está ocupado. No se enviará; cierra otros programas que lo estén usando.') from None
                time.sleep(.05)
    def clipboard_text(self,editor):''').replace('win32clipboard.OpenClipboard()','self.open_clipboard(win32clipboard)')
compile(x,str(a),'exec'); compile(y,str(w),'exec')
backup=b/('respaldo-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')); backup.mkdir()
for p in (a,w): shutil.copy2(p,backup/p.name)
try:
    a.write_text(x,encoding='utf-8'); w.write_text(y,encoding='utf-8')
except Exception:
    for p in (a,w): shutil.copy2(backup/p.name,p)
    raise
print('Reparacion instalada. Registro de envios conservado. No se envio ningun mensaje.')
'@
$python = Join-Path $dir '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'No existe el entorno de la carpeta seleccionada.' }
$repair | & $python -X utf8 -
if ($LASTEXITCODE -ne 0) { throw 'Reparacion detenida. No se iniciara el programa.' }
& (Join-Path $dir 'Iniciar-Valhalla.ps1')
