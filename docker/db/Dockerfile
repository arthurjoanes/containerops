ARG POSTGRES_IMAGE=postgres:17.11-alpine3.24@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
FROM ${POSTGRES_IMAGE}

# Preserve the existing data and backup volumes' UID/GID contract.
# su-exec implements the entrypoint's user switch without gosu's Go runtime.
RUN set -eux; \
    test "$(id -u postgres)" = 70; \
    test "$(id -g postgres)" = 70; \
    test -z "$(grep -E '^[^:]+:[^:]*:999:' /etc/passwd)"; \
    test "$(grep -E '^[^:]+:[^:]*:999:' /etc/group)" = 'ping:x:999:'; \
    test -z "$(grep -E '^[^:]+:[^:]*:998:' /etc/group)"; \
    test -z "$(find /usr /bin /sbin /etc /var /lib -group 999 -print -quit)"; \
    sed -i 's/^ping:x:999:/ping:x:998:/' /etc/group; \
    sed -i 's/^postgres:x:70:70:/postgres:x:999:999:/' /etc/passwd; \
    sed -i 's/^postgres:x:70:/postgres:x:999:/' /etc/group; \
    chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql; \
    apk add --no-cache su-exec=0.3-r0; \
    rm /usr/local/bin/gosu; \
    sed -i 's/exec gosu postgres/exec su-exec postgres/' /usr/local/bin/docker-entrypoint.sh; \
    test "$(id -u postgres)" = 999; \
    test "$(id -g postgres)" = 999; \
    test "$(su-exec postgres id -u)" = 999; \
    test "$(su-exec postgres id -g)" = 999; \
    test "$PGDATA" = /var/lib/postgresql/data; \
    command -v bash; \
    ! grep -q 'exec gosu' /usr/local/bin/docker-entrypoint.sh

COPY --chmod=755 docker/db/entrypoint.sh /usr/local/bin/containerops-entrypoint.sh
COPY --chmod=755 docker/db/00-platform.sh /docker-entrypoint-initdb.d/00-containerops-platform.sh
ENTRYPOINT ["containerops-entrypoint.sh"]
