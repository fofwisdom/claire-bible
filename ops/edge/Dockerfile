# Edge service of Claire: the reverse proxy rules of docs/implementation/FEDERATION.md Section 10 in one image.
# nginx 1.30.7 is the stable release on 2026-11-22 (nginx.org). License: BSD 2-clause (https://nginx.org/LICENSE).
FROM nginx:1.30.7-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY claire/ /etc/nginx/claire/
COPY templates/ /etc/nginx/templates/

# The entrypoint of the base image writes /tmp/edge.conf from the template (NGINX_ENVSUBST_OUTPUT_DIR). Compose gives the user of the Claire services, so that the process can connect to the socket of Claire.
ENV NGINX_ENVSUBST_OUTPUT_DIR=/tmp
EXPOSE 8080
