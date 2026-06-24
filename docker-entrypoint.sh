#!/bin/sh
# Vocence container entrypoint.
#
# Runs as root only to make the mounted state dirs writable by the unprivileged
# `validator` user (uid 1000), then drops privileges and execs the app. This means
# operators can just `docker compose up -d` — no manual `chown` of ./data or ./logs,
# even though those are host bind mounts (which Docker would otherwise create as root).
set -e

for d in /app/data /app/logs; do
    mkdir -p "$d" 2>/dev/null || true
    # Best-effort: fix ownership so the validator user can write. Ignored on
    # read-only mounts or when already correct.
    chown -R 1000:1000 "$d" 2>/dev/null || true
done

# Drop from root to the validator user for the actual process.
exec gosu validator "$@"
