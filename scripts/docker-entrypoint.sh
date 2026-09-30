#!/usr/bin/env bash
set -e

# 1. If extra CA certificates are mounted into /extra-certs, install them into container's trust store (runs as root)
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

# 2. Privileged drop to target host UID / GID
TARGET_UID="${CB_UID:-1000}"
TARGET_GID="${CB_GID:-1000}"

# If running explicitly as root, bypass privilege drop
if [ "$TARGET_UID" = "0" ] || [ "$TARGET_UID" = "root" ]; then
  exec "$@"
fi

# Ensure group with TARGET_GID exists
if ! getent group "$TARGET_GID" >/dev/null 2>&1; then
  groupadd -g "$TARGET_GID" claire 2>/dev/null || true
fi

# Ensure user with TARGET_UID exists
if ! id -u "$TARGET_UID" >/dev/null 2>&1; then
  useradd -u "$TARGET_UID" -g "$TARGET_GID" -m -s /bin/bash -d /home/claire claire 2>/dev/null || true
fi

USER_HOME=$(getent passwd "$TARGET_UID" | cut -d: -f6 2>/dev/null || echo "/home/claire")
mkdir -p "$USER_HOME" /app/data /app/vault

# Automatically align ownership of user home and application data/vault
# so existing volumes from previous root deployments are seamlessly migrated without manual host action
chown -R "$TARGET_UID:$TARGET_GID" "$USER_HOME" /app/data /app/vault 2>/dev/null || true

# Allow traversing /root if config volumes were mounted at /root/.gemini or /root/.codex
chmod 755 /root 2>/dev/null || true

# Symlink credentials to user home if mounted at /root
if [ -d "/root/.gemini" ] && [ ! -e "$USER_HOME/.gemini" ]; then
  ln -sfn /root/.gemini "$USER_HOME/.gemini"
fi
if [ -d "/root/.codex" ] && [ ! -e "$USER_HOME/.codex" ]; then
  ln -sfn /root/.codex "$USER_HOME/.codex"
fi

export HOME="$USER_HOME"

# 3. Drop privileges and execute command via gosu
exec gosu "$TARGET_UID:$TARGET_GID" "$@"
