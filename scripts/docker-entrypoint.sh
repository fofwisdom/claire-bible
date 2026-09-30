#!/usr/bin/env bash
set -e

# If extra CA certificates are mounted into /extra-certs, install them into container's trust store
if [ -d "/extra-certs" ]; then
  found_certs=0
  for cert_file in /extra-certs/*.crt /extra-certs/*.pem; do
    if [ -f "$cert_file" ]; then
      cp -f "$cert_file" /usr/local/share/ca-certificates/ 2>/dev/null || true
      found_certs=1
    fi
  done

  if [ "$found_certs" -eq 1 ]; then
    update-ca-certificates > /dev/null 2>&1 || true
  fi
fi

exec "$@"
