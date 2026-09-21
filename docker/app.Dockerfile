# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_COMPILE=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY app/requirements-runtime.lock /locks/requirements-runtime.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --only-binary=:all: -r /locks/requirements-runtime.lock
# A sentinela entra apenas pelo secret mount.
RUN --mount=type=secret,id=sentinel,required=true test -s /run/secrets/sentinel

FROM deps AS test
COPY app/requirements-test.lock /locks/requirements-test.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --only-binary=:all: -r /locks/requirements-test.lock
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY app/pyproject.toml /app/pyproject.toml
COPY app/src /app/src
COPY app/tests /app/tests
USER 10001:10001
CMD ["python", "-m", "pytest", "/app/tests"]

FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=1.0.0
ARG VCS_REF=uncommitted
LABEL org.opencontainers.image.title="ContainerOps" \
      org.opencontainers.image.description="Lab de Docker com jobs em PostgreSQL" \
      org.opencontainers.image.source="local:ContainerOps" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"
RUN groupadd --gid 10001 containerops \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin containerops
COPY --from=deps /opt/venv /opt/venv
# pip e setuptools ficam só em deps/test; não são necessários no runtime.
RUN for site in /usr/local/lib/python3.13/site-packages /opt/venv/lib/python3.13/site-packages; do \
        rm -rf "$site/pip" "$site"/pip-*.dist-info \
               "$site/setuptools" "$site"/setuptools-*.dist-info \
               "$site/pkg_resources" "$site/_distutils_hack" \
               "$site/distutils-precedence.pth"; \
    done \
    && rm -rf /usr/local/lib/python3.13/ensurepip \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
             /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.13 \
    && /opt/venv/bin/python -c "import importlib.util; assert all(importlib.util.find_spec(name) is None for name in ('pip', 'setuptools', 'pkg_resources', 'ensurepip'))"
WORKDIR /app
COPY --chown=10001:10001 app/src /app/src
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "-m", "containerops.api"]
