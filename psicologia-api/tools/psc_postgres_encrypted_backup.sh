#!/usr/bin/env bash
# PSC: respaldo cifrado bajo demanda, sin escribir en la base de origen.
# Requisitos: pg_dump/pg_restore PostgreSQL 18, OpenSSL, Bash.
set -Eeuo pipefail
umask 077

: "${PSICOLOGIA_HOLDS_DATABASE_URL:?Falta la URL de lectura de PostgreSQL}"
: "${PSC_BACKUP_PASSPHRASE:?Falta contraseña de cifrado (no se guarda aquí)}"
: "${PSC_BACKUP_DIR:?Falta directorio de destino para el respaldo}"
for command in pg_dump pg_restore openssl sha256sum mktemp; do
  command -v "$command" >/dev/null || { echo "Falta: $command" >&2; exit 2; }
done
mkdir -p -- "$PSC_BACKUP_DIR"
[ -d "$PSC_BACKUP_DIR" ] || exit 2
dir="$(mktemp -d)"
cleanup() { rm -rf -- "$dir"; }
trap cleanup EXIT INT TERM
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
plain="$dir/psc.dump"
encrypted="$PSC_BACKUP_DIR/psc-postgres-$stamp.dump.enc"
manifest="$PSC_BACKUP_DIR/psc-postgres-$stamp.sha256"
if [ -e "$encrypted" ] || [ -e "$manifest" ]; then
  echo "Nombre duplicado: abortando" >&2; exit 2
fi
echo "Creando respaldo personalizado de solo lectura..."
pg_dump --dbname="$PSICOLOGIA_HOLDS_DATABASE_URL" --format=custom --no-owner --no-acl --file="$plain"
pg_restore --list "$plain" >"$dir/toc.txt"
for table in psychology_booking_holds psychology_demo_payments psychology_google_oauth psychology_fonasa_bookings; do
  grep -Eq " TABLE public $table " "$dir/toc.txt" || {
    echo "Tabla esperada ausente: $table" >&2; exit 3;
  }
done
openssl enc -aes-256-cbc -pbkdf2 -iter 250000 -salt \
  -pass env:PSC_BACKUP_PASSPHRASE -in "$plain" -out "$encrypted"
# Comprobar descifrado real y formato antes de declarar generado.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 250000 \
  -pass env:PSC_BACKUP_PASSPHRASE -in "$encrypted" -out "$dir/roundtrip.dump"
cmp "$plain" "$dir/roundtrip.dump"
pg_restore --list "$dir/roundtrip.dump" >/dev/null
(cd "$PSC_BACKUP_DIR" && sha256sum "$(basename "$encrypted")" > "$(basename "$manifest")")
echo "BACKUP_ENCRYPTED_AND_DECRYPT_TEST_OK"
echo "Archivo: $encrypted"
echo "SHA256: $manifest"
echo "ATENCIÓN: no es una prueba de restauración integral. Restaurar a una BD AISLADA antes del corte."
