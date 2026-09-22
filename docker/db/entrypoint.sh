#!/bin/sh
set -eu

case "${1:-}" in
    postgres|-*)
        if [ -s "$PGDATA/PG_VERSION" ]; then
            platform=''
            if [ -f "$PGDATA/.containerops-platform" ]; then
                platform="$(cat "$PGDATA/.containerops-platform")"
            fi
            if [ "$platform" != 'alpine3.24' ]; then
                printf '%s\n' 'ContainerOps: this existing PostgreSQL data directory was not initialized for Alpine 3.24.' >&2
                printf '%s\n' 'Start the previous database image, create a logical backup with pg_dump, then restore into a new volume initialized by this image. The existing volume was not modified.' >&2
                exit 1
            fi
        fi
        ;;
esac

exec /usr/local/bin/docker-entrypoint.sh "$@"
