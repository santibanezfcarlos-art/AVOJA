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
$hashes = @{'app.py' = '6e3b860870c8d2a97e7b14477d3553e13c9208a2344eec4dccea2595a1620016'; 'core.py' = 'aa655fede2a12c0fd73eff09c79067d068223f62720e75a3798658a8df6fe620'; 'windows_uia.py' = 'b0b895f6a48f043505d2ebe21df25a26c4d3aa4902acec72222374da7c5b83ef'}
foreach ($name in @('app.py','core.py','windows_uia.py')) {
    $target = Join-Path $stage $name
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/977faedc69bb5dc142ca60b512b0e04c03881555/valhalla/windows_whatsapp/' + $name) -OutFile $target
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $hashes[$name]) { throw 'Descarga incompleta. Instalacion conservada.' }
}
& $python -m py_compile (Join-Path $stage 'app.py') (Join-Path $stage 'core.py') (Join-Path $stage 'windows_uia.py')
if ($LASTEXITCODE -ne 0) { throw 'Validacion fallida. Instalacion conservada.' }
$backup = Join-Path $stage 'respaldo'
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @('app.py','core.py','windows_uia.py')) { Copy-Item -LiteralPath (Join-Path $dir $name) -Destination $backup }
try {
    foreach ($name in @('app.py','core.py','windows_uia.py')) { Copy-Item -LiteralPath (Join-Path $stage $name) -Destination $dir -Force }
} catch {
    foreach ($name in @('app.py','core.py','windows_uia.py')) { Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $dir -Force }
    throw
}
Write-Host 'Manejo de contactos no disponibles actualizado. Mensaje de campana y registro conservados. No se ha enviado ningun mensaje.'
& (Join-Path $dir 'Iniciar-Valhalla.ps1')
