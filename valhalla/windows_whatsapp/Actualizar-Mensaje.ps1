$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Selecciona Iniciar.cmd de tu carpeta extraida actual'
$d.Filter = 'Iniciar.cmd|Iniciar.cmd'
if ($d.ShowDialog() -ne 'OK') { return }
$dir = Split-Path -Parent $d.FileName
$python = Join-Path $dir '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Selecciona la carpeta instalada, no el ZIP.' }
$stage = Join-Path $dir ('actualizacion-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$hashes = @{'app.py' = '193a4a785b6cb26a93636ad68f1f58e112171442cab459fde2455df4e1be0e48'; 'core.py' = '887842a69a910522b40355eae8d10a78dfb7a4961c4e4d3cba8920fec6f9c5c1'}
foreach ($name in @('app.py','core.py')) {
    $target = Join-Path $stage $name
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/9736a4a4ead3732ba2aa66c1bae22ee2229f81f1/valhalla/windows_whatsapp/' + $name) -OutFile $target
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $hashes[$name]) { throw 'Descarga incompleta. Instalacion conservada.' }
}
& $python -m py_compile (Join-Path $stage 'app.py') (Join-Path $stage 'core.py')
if ($LASTEXITCODE -ne 0) { throw 'Validacion fallida. Instalacion conservada.' }
$backup = Join-Path $stage 'respaldo'
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @('app.py','core.py')) { Copy-Item -LiteralPath (Join-Path $dir $name) -Destination $backup }
try {
    foreach ($name in @('app.py','core.py')) { Copy-Item -LiteralPath (Join-Path $stage $name) -Destination $dir -Force }
} catch {
    foreach ($name in @('app.py','core.py')) { Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $dir -Force }
    throw
}
Write-Host 'Mensaje integrado. Registro conservado. No se ha enviado ningun mensaje.'
& (Join-Path $dir 'Iniciar-Valhalla.ps1')
