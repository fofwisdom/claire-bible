#!/bin/sh
# Development network only (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.4).
# Makes the private certificate authority (one time for each development network) and the server certificate of the names below claire.test.
# Output in /tls (the run folder .dev/devnet/tls): ca/ca.crt (the parties trust it), server/ (the gateway), private/ (the key of the authority).
set -eu
umask 077
out=/tls
mkdir -p "$out/ca" "$out/private" "$out/server"
if [ ! -f "$out/private/ca.key" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
    -subj "/CN=Claire development network authority" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$out/private/ca.key" -out "$out/ca/ca.crt"
fi
# A wildcard covers one label only: each server has its own wildcard for its role hosts (Section 6.2 item 4).
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=claire.test" \
  -keyout "$out/server/server.key" -out "$out/private/server.csr"
printf '%s\n' \
  "basicConstraints=critical,CA:FALSE" \
  "keyUsage=critical,digitalSignature" \
  "extendedKeyUsage=serverAuth" \
  "subjectAltName=DNS:claire.test,DNS:*.claire.test,DNS:*.a.claire.test,DNS:*.b.claire.test" > "$out/private/server.ext"
openssl x509 -req -in "$out/private/server.csr" -CA "$out/ca/ca.crt" -CAkey "$out/private/ca.key" \
  -CAserial "$out/private/ca.srl" -CAcreateserial -days 365 -extfile "$out/private/server.ext" -out "$out/server/server.crt"
chmod 0755 "$out/ca" "$out/server"
chmod 0644 "$out/ca/ca.crt" "$out/server/server.crt"
