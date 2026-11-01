# PSC: Restore native PostgreSQL backup in a disposable local Windows Docker container.
# Isolated (--network none), no exposed ports, no writes to Render or production.
# Plaintext remains exclusively inside the container, removed automatically.

param([string] $Archive = (Join-Path $HOME "psc_postgres_full.dump.enc"))
$ErrorActionPreference = "Stop"
$Image = "postgres:18-bookworm"
$ExpectedHash = "a6cf031c1a7645459be4d5201a84f36bff46520020ec4409d1f1dd6f00935e"
$Container = "psc-rehearsal-" + [guid]::NewGuid().ToString("N").Substring(0,12)
$Started = $false
$Succeeded = $false

function Assert-Success([string]$Stage) {
  if ($LASTEXITCODE -ne 0) { throw ("Error en " + $Stage + "; ninguna base real modificada") }
}
try {
  $Archive = [System.IO.Path]::GetFullPath($Archive)
  if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) { throw "Falta archivo cifrado local" }
  $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($Actual -ne $ExpectedHash) { throw "SHA-256 no coincide con Render" }
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker Desktop no esta instalado" }
  & docker info --format '{{.ServerVersion}}' | Out-Null
  Assert-Success "conexion a Docker"
  Write-Host "PSC: comprobado SHA-256. Descargando imagen oficial PostgreSQL 18..."
  & docker pull $Image | Out-Null
  Assert-Success "descarga de imagen"
  Write-Host "PSC: iniciando contenedor aislado, sin puertos ni red."
  & docker run --detach --rm --network none --name $Container --env POSTGRES_HOST_AUTH_METHOD=trust --mount "type=bind,source=$Archive,target=/tmp/psc-backup.enc,readonly" $Image | Out-Null
  Assert-Success "inicio de contenedor"
  $Started = $true
  $Ready = $false
  for ($i=0; $i -lt 40; $i++) {
    & docker exec --user postgres $Container pg_isready --username postgres --dbname postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $Ready=$true; break }
    Start-Sleep -Seconds 1
  }
  if (-not $Ready) { throw "PostgreSQL temporal no pudo arrancar" }
  Write-Host "PSC: escribe la contrasena de cifrado cuando OpenSSL la solicite (no aparece al escribir)."
  & docker exec --interactive --tty --user postgres $Container openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in /tmp/psc-backup.enc -out /tmp/psc-restore.dump
  Assert-Success "descifrado privado"
  & docker exec --user postgres $Container pg_restore --list /tmp/psc-restore.dump | Out-Null
  Assert-Success "lectura de respaldo"
  & docker exec --user postgres $Container createdb --username postgres psc_rehearsal
  Assert-Success "creacion de base descartable"
  Write-Host "PSC: restaurando PostgreSQL nativo en base descartable..."
  & docker exec --user postgres $Container pg_restore --exit-on-error --no-owner --no-privileges --username postgres --dbname psc_rehearsal /tmp/psc-restore.dump
  Assert-Success "restauracion"
  $SQL = @'
SELECT 'psychology_booking_days' AS tabla, count(*) AS registros FROM public.psychology_booking_days
UNION ALL SELECT 'psychology_booking_holds', count(*) FROM public.psychology_booking_holds
UNION ALL SELECT 'psychology_demo_payments', count(*) FROM public.psychology_demo_payments
UNION ALL SELECT 'psychology_demo_notifications', count(*) FROM public.psychology_demo_notifications
UNION ALL SELECT 'psychology_reschedule_links', count(*) FROM public.psychology_reschedule_links
UNION ALL SELECT 'psychology_reschedule_audit', count(*) FROM public.psychology_reschedule_audit
UNION ALL SELECT 'psychology_google_oauth', count(*) FROM public.psychology_google_oauth
UNION ALL SELECT 'psychology_fonasa_bookings', count(*) FROM public.psychology_fonasa_bookings
ORDER BY tabla;
'@
  & docker exec --user postgres $Container psql --username postgres --dbname psc_rehearsal --no-psqlrc --set ON_ERROR_STOP=1 --pset pager=off --command $SQL
  Assert-Success "verificacion de datos restaurados"
  $Succeeded=$true
  Write-Host "PSC_NATIVE_POSTGRES_RESTORE_OK: restauracion nativa exitosa, sin modificar produccion." -ForegroundColor Green
}
catch {
  Write-Host ("PSC_RESTORE_TEST_FAILED: " + $_.Exception.Message) -ForegroundColor Red
}
finally {
  if ($Started) {
    Write-Host "PSC: eliminando contenedor temporal y archivo descifrado..."
    & docker rm --force --volumes $Container | Out-Null
  }
}
if (-not $Succeeded) { exit 1 }
