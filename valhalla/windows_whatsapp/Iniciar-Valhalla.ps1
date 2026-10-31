$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$privateDir = Join-Path $env:LOCALAPPDATA 'ValhallaWhatsApp'
New-Item -ItemType Directory -Force -Path $privateDir | Out-Null
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $privateDir /inheritance:r /grant:r "*${userSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No se pudo proteger el registro local.' }
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonExe)) {
    if (!(Get-Command py -ErrorAction SilentlyContinue)) { throw 'Instala Python 3.12 de python.org con el lanzador py y vuelve a ejecutar.' }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Se necesita Python 3.12 de 64 bits.' }
}
$requirementsHash = (Get-FileHash -LiteralPath requirements.txt -Algorithm SHA256).Hash
$stamp = Join-Path $PSScriptRoot '.venv\dependencies.ok'
if (!(Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $requirementsHash) {
    & $pythonExe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias gratuitas.' }
    Set-Content -LiteralPath $stamp -Value $requirementsHash
}
& $pythonExe app.py
if ($LASTEXITCODE -ne 0) { Read-Host 'No se pudo iniciar. Presiona Enter para cerrar' }
