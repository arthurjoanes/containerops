ARG NGINX_IMAGE=nginx:1.30.5-alpine3.24@sha256:a5f2157a0302eb0c5e300415effb63a9e70ed1eb9c107283819bf6d149ab607c
FROM ${NGINX_IMAGE}
# Keep security floors while accepting newer fixes from the same Alpine branch.
RUN apk add --no-cache --upgrade \
    'libexpat>=2.8.5-r0' \
    'libpng>=1.6.59-r0' \
    'nghttp2-libs>=1.70.0-r0' \
    'pcre2>=10.49-r0' \
    'tiff>=4.7.2-r0'
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
