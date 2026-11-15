# Development test only. A DNS server for the wildcard names of the DrFed test stack (tests/drfed/docker-compose.yml).
FROM nginx:1.27-alpine
RUN apk add --no-cache dnsmasq
