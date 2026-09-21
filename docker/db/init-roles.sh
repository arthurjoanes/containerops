#!/usr/bin/env bash
# Linux checkouts may source this file; isolate flags from the official entrypoint.
(
set -Eeuo pipefail

# The official entrypoint invokes this only for a new, empty database volume.
export CONTAINEROPS_MIGRATOR_PASSWORD="$(cat /run/secrets/db_migrator)"
export CONTAINEROPS_APP_PASSWORD="$(cat /run/secrets/db_app)"
export CONTAINEROPS_BACKUP_PASSWORD="$(cat /run/secrets/db_backup)"

psql --no-psqlrc --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv migrator_password CONTAINEROPS_MIGRATOR_PASSWORD
\getenv app_password CONTAINEROPS_APP_PASSWORD
\getenv backup_password CONTAINEROPS_BACKUP_PASSWORD
CREATE ROLE containerops_migrator LOGIN PASSWORD :'migrator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE containerops_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE containerops_backup LOGIN PASSWORD :'backup_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE containerops FROM PUBLIC;
GRANT CONNECT ON DATABASE containerops TO containerops_migrator, containerops_app, containerops_backup;
ALTER SCHEMA public OWNER TO containerops_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO containerops_app, containerops_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE containerops_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO containerops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE containerops_migrator IN SCHEMA public GRANT SELECT ON TABLES TO containerops_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE containerops_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO containerops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE containerops_migrator IN SCHEMA public GRANT SELECT ON SEQUENCES TO containerops_backup;
SQL

unset CONTAINEROPS_MIGRATOR_PASSWORD CONTAINEROPS_APP_PASSWORD CONTAINEROPS_BACKUP_PASSWORD
)
