ARG NGINX_IMAGE=nginx:1.30.5-alpine@sha256:ef8676b33d681f272ba429b27658bdd7e640963279714c96bddf1dc76307f7b6
FROM ${NGINX_IMAGE}
# Fixed Alpine libraries reported by the service image scan.
RUN apk add --no-cache \
    libexpat=2.8.5-r0 \
    libpng=1.6.59-r0 \
    nghttp2-libs=1.70.0-r0 \
    pcre2=10.49-r0
LABEL org.opencontainers.image.title="ContainerOps proxy" \
      org.opencontainers.image.source="https://github.com/arthurjoanes/containerops"
COPY docker/proxy/nginx.conf /etc/nginx/nginx.conf
COPY docker/proxy/locations.conf /etc/nginx/locations.conf
COPY docker/proxy/http.conf /etc/nginx/conf.d/default.conf
USER 101:101
EXPOSE 8080 8443
STOPSIGNAL SIGQUIT
# Bypass upstream scripts which attempt to edit configuration at startup.
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
