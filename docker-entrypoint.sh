#!/bin/sh
# Make mounted state dirs writable by the validator user, then drop privileges.
# Lets operators just `docker compose up -d` (no manual chown of ./data, ./logs).
set -e

for d in /app/data /app/logs; do
    mkdir -p "$d" 2>/dev/null || true
    chown -R 1000:1000 "$d" 2>/dev/null || true
done

exec gosu validator "$@"
