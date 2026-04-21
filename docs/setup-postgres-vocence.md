# PostgreSQL setup for Vocence

> **Never use `vocence` as the DB password.** Generate a strong random one with
> `openssl rand -base64 33`. This doc uses the shell variable `$DB_PW` below so
> the password never ends up in command history; set it once and reuse it.

## 1. Install PostgreSQL (if not already installed)

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
```

**Fedora/RHEL:**
```bash
sudo dnf install -y postgresql-server postgresql-contrib
sudo postgresql-setup --initdb
sudo systemctl start postgresql
```

**macOS (Homebrew):**
```bash
brew install postgresql@16
brew services start postgresql@16
```

## 2. Generate a strong password

```bash
export DB_PW="$(openssl rand -base64 33 | tr -d '=+/\n' | cut -c1-40)"
echo "Generated DB password (save this): $DB_PW"
```

## 3. Create user and database

Switch to the `postgres` system user and run `psql`, then run:

```sql
-- Replace <DB_PW> with the password you generated in step 2
CREATE USER vocence WITH PASSWORD '<DB_PW>';
CREATE DATABASE vocence OWNER vocence;
GRANT ALL PRIVILEGES ON DATABASE vocence TO vocence;

\c vocence
GRANT ALL ON SCHEMA public TO vocence;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO vocence;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO vocence;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO vocence;
```

Or as a one-liner:

```bash
sudo -u postgres psql \
  -c "CREATE USER vocence WITH PASSWORD '$DB_PW';" \
  -c "CREATE DATABASE vocence OWNER vocence;" \
  -c "\\c vocence" \
  -c "GRANT ALL ON SCHEMA public TO vocence;"
```

## 4. Lock Postgres to localhost (critical)

The Vocence DB must never be reachable from the internet. Miners can guess
the server IP from DNS (e.g. `subnet.vocence.ai`), port 5432 is a default,
and a weak password would give them full DB access — bypassing every API
control.

**a.** In `/etc/postgresql/16/main/postgresql.conf`:
```
listen_addresses = 'localhost'
```

**b.** In `/etc/postgresql/16/main/pg_hba.conf` — only allow loopback with
password auth:
```
# TYPE  DATABASE  USER    ADDRESS      METHOD
local   all       all                  peer
host    vocence   vocence 127.0.0.1/32 scram-sha-256
host    vocence   vocence ::1/128      scram-sha-256
```

**c.** Restart Postgres:
```bash
sudo systemctl restart postgresql
```

**d.** Belt-and-suspenders — block 5432 at the OS firewall or cloud security
group even though Postgres shouldn't be reachable anyway:
```bash
sudo ufw deny 5432/tcp
```

## 5. Test the connection

```bash
PGPASSWORD="$DB_PW" psql -h localhost -U vocence -d vocence -c "SELECT 1;"
```

Or with the connection string in `.env`:
```
postgresql://vocence:<DB_PW>@localhost:5432/vocence
```

## 6. Hand the password to the two services

Put the same password in both:

- `/workspace/vocence/.env` → `POSTGRES_PASSWORD` **and** `DB_CONNECTION_STRING`
- `/workspace/vocence_website/dashboard-backend/.env` → `DATABASE_URL`

Never commit either file to git.
