import argparse
import json
from pathlib import Path

import psycopg

from containerops import repository
from containerops.config import Settings
from containerops.database import connect
from containerops.domain import SchemaIncompatible
from containerops.log import configure, event


def migrate(settings: Settings, target: int) -> int:
    if target not in (1, 2):
        raise ValueError("Versão de migração deve ser 1 ou 2")
    with connect(settings) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(8105001)")
        exists = connection.execute("SELECT to_regclass('public.schema_version')").fetchone()
        current = 0
        if exists is not None and exists[0] is not None:
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            assert row is not None
            current = int(str(row[0]))
        if target < current:
            raise ValueError("Downgrade de banco não é permitido; reverta somente a imagem")
        for version in range(current + 1, target + 1):
            sql = (Path(__file__).parent / "migrations" / f"{version:03d}.sql").read_text()
            connection.execute(sql)
        connection.execute("GRANT USAGE ON SCHEMA public TO containerops_app, containerops_backup")
        connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM containerops_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO containerops_app")
        connection.execute("GRANT SELECT, UPDATE ON operations TO containerops_app")
        connection.execute("GRANT SELECT ON schema_version TO containerops_app")
        connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO containerops_backup")
        connection.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT ON TABLES TO containerops_backup"
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Operação local do ContainerOps")
    commands = parser.add_subparsers(dest="command", required=True)
    migration = commands.add_parser("migrate")
    migration.add_argument("--target", type=int, choices=[1, 2], required=True)
    for command in ("pause", "resume", "snapshot", "cleanup", "metrics"):
        commands.add_parser(command)
    args = parser.parse_args()
    configure()
    settings = Settings.from_env()
    try:
        if args.command == "migrate":
            output: object = {"schema_version": migrate(settings, args.target)}
        elif args.command in ("pause", "resume"):
            paused = args.command == "pause"
            repository.set_admission(settings, paused)
            output = {"admission_paused": paused}
        elif args.command == "snapshot":
            output = repository.snapshot(settings)
        elif args.command == "cleanup":
            output = {"deleted_jobs": repository.cleanup(settings)}
        else:
            output = repository.metrics(settings)
    except (psycopg.Error, OSError, ValueError, SchemaIncompatible) as error:
        event("manage", settings.version, "operation_failed", error_category=type(error).__name__)
        return 1
    print(json.dumps(output, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
