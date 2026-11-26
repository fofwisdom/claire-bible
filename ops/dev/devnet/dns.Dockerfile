# Development network only (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.2 item 3): dnsmasq for the names below claire.test.
# The base image is the one of the edge service (ops/edge/Dockerfile), so the development network needs no other base image.
FROM nginx:1.30.7-alpine
RUN apk add --no-cache dnsmasq
