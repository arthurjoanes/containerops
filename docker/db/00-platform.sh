#!/bin/sh
set -eu

# Called only after initdb has initialized a new data directory.
# The entrypoint uses this marker to reject unreviewed libc/platform migrations.
umask 077
printf '%s\n' 'alpine3.24' > "$PGDATA/.containerops-platform"
