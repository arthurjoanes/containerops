#!/bin/sh
set -eu
umask 077

operation="${1:?Use dump ou restore}"
artifact="${2:-/var/lib/postgresql/data/containerops.dump}"
case "$artifact" in
    /var/lib/postgresql/data/*.dump) ;;
    *) printf '%s\n' 'Artefato fora do volume de backup do projeto' >&2; exit 2 ;;
esac

# Libpq receives the secret at execution time, never as a Compose environment value.
export PGPASSWORD="$(cat "${DB_PASSWORD_FILE:-/run/secrets/db_password}")"
export PGCONNECT_TIMEOUT=5
case "$operation" in
    dump)
        exec pg_dump --format=custom --no-owner --no-privileges --file="$artifact"
        ;;
    restore)
        test -s "$artifact"
        exec pg_restore --exit-on-error --single-transaction --no-owner --no-privileges --dbname="$PGDATABASE" "$artifact"
        ;;
    *) printf '%s\n' 'Operação inválida: use dump ou restore' >&2; exit 2 ;;
esac
