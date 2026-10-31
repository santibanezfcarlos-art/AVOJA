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
$hashes = @{'app.py' = '963be88aae037dfd3bc37de7dd08c70c1d9fd6727c8ff8ec7331496199328dfd'; 'core.py' = 'b6a13a85f8fedb5a161532d1b4fdd539c6a3b175694164f4dccb0aba423b03f2'; 'windows_uia.py' = 'b25e5936936d521ce6cdbf4ee4e9088da67ccfb5a8c05aba0e4844776dcc8ad9'}
foreach ($name in @('app.py','core.py','windows_uia.py')) {
    $target = Join-Path $stage $name
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/8199a5fb65eade055502900b840987b88587ac90/valhalla/windows_whatsapp/' + $name) -OutFile $target
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
Write-Host 'Rapidez actualizada. Mensaje de campana y registro conservados. No se ha enviado ningun mensaje.'
& (Join-Path $dir 'Iniciar-Valhalla.ps1')
