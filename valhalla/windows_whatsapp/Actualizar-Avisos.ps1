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
$hashes = @{'app.py' = 'c4008c75ca39a5d237e057f8840f83beee1b1727ecd1fec5e4322904b6d04590'; 'core.py' = 'aa655fede2a12c0fd73eff09c79067d068223f62720e75a3798658a8df6fe620'; 'windows_uia.py' = 'd55e5e6ffac7e62258e1475cada5ede3d3ce9e3918858d2717b9c6b35e6f8f0d'}
foreach ($name in @('app.py','core.py','windows_uia.py')) {
    $target = Join-Path $stage $name
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/aa5f67bb63f6c56532fa8214321562a504e85cb8/valhalla/windows_whatsapp/' + $name) -OutFile $target
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
