#!/usr/bin/env bash
# Create PostgreSQL user and database for Vocence.
#
# Usage:
#   DB_PW="$(openssl rand -base64 33 | tr -d '=+/\n' | cut -c1-40)" \
#     sudo -u postgres -E ./setup-vocence-db.sh
#
# The generated password is ONLY printed at the end — copy it into
# /workspace/vocence/.env (POSTGRES_PASSWORD + DB_CONNECTION_STRING) and
# /workspace/vocence_website/dashboard-backend/.env (DATABASE_URL), then
# delete it from your shell history.

set -euo pipefail

USER_NAME="vocence"
DB_NAME="vocence"

if [[ -z "${DB_PW:-}" ]]; then
    echo "ERROR: DB_PW env var must be set to a strong random password." >&2
    echo "       Generate one with: openssl rand -base64 33 | tr -d '=+/\\n' | cut -c1-40" >&2
    exit 1
fi
if [[ "${DB_PW}" == "vocence" || ${#DB_PW} -lt 16 ]]; then
    echo "ERROR: DB_PW must be at least 16 chars and not the literal 'vocence'." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 <<EOF
CREATE USER ${USER_NAME} WITH PASSWORD '${DB_PW}';
CREATE DATABASE ${DB_NAME} OWNER ${USER_NAME};
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${USER_NAME};
\c ${DB_NAME}
GRANT ALL ON SCHEMA public TO ${USER_NAME};
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ${USER_NAME};
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ${USER_NAME};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${USER_NAME};
EOF

echo
echo "Done. User '${USER_NAME}', database '${DB_NAME}' created."
echo "Connection string (put this in both .env files):"
echo "  postgresql://${USER_NAME}:${DB_PW}@localhost:5432/${DB_NAME}"
echo
echo "Next: bind Postgres to localhost and restart — see docs/setup-postgres-vocence.md step 4."
