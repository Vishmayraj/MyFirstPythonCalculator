#!/usr/bin/env bash
# ============================================================
# Sentinel — local (non-Docker) Postgres bootstrap
# ============================================================
# Creates everything a fresh clone needs to run `pytest` (and, if you
# want, the app itself) against a *local* Postgres install instead of
# Docker:
#
#   1. the `sentinel` LOGIN role (password: sentinel_dev)
#   2. the `sentinel` database, owned by `sentinel`           (dev DB)
#   3. the postgis / pgcrypto / vector extensions on that DB
#
# It does NOT create `sentinel_test` — tests/conftest.py drops and
# rebuilds that one itself, every test session, from
# shared/db/{schema,triggers,seed}.sql. It only needs the `sentinel`
# role + server to already exist, which is exactly what this script
# provides.
#
# Safe to re-run: every step below is idempotent (IF NOT EXISTS /
# existence checks), so running this twice does nothing bad.
#
# If you'd rather not install Postgres locally at all, `docker compose
# up -d` (see infra/README.md) creates the equivalent role/db/extensions
# automatically on first boot — this script is only for people running
# the app or tests directly on the host.
#
# Only tested on Linux/macOS. On Windows, either use `docker compose up`
# instead, or run the SQL in ADMIN_SQL / CREATE_DB_SQL / EXT_SQL below
# yourself via pgAdmin or psql, as whatever admin login your install uses.
# ============================================================
set -euo pipefail

PGHOST="127.0.0.1"
PGPORT="5432"
SENTINEL_USER="sentinel"
SENTINEL_PASSWORD="sentinel_dev"
SENTINEL_DB="sentinel"
# Every psql call below gets a timeout so a misconfigured local install
# (e.g. one that ends up blocked on an interactive password prompt)
# fails fast and loud instead of hanging the terminal indefinitely.
PSQL_TIMEOUT="15"

EXT_SQL="CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector;"

# Written to real files rather than passed as -c strings: -c strings
# with $$ (dollar-quoting) or \gexec get mangled once they pass through
# an extra shell layer (su -c "...", sudo -u ... sh -c "..."), so a
# plain -f avoids a whole class of quoting bugs across those layers.
ADMIN_SQL_FILE="$(mktemp)"
CREATE_DB_SQL_FILE="$(mktemp)"
trap 'rm -f "$ADMIN_SQL_FILE" "$CREATE_DB_SQL_FILE"' EXIT

cat > "$ADMIN_SQL_FILE" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${SENTINEL_USER}') THEN
        CREATE ROLE ${SENTINEL_USER} WITH LOGIN SUPERUSER PASSWORD '${SENTINEL_PASSWORD}';
    END IF;
END
\$\$;
SQL

cat > "$CREATE_DB_SQL_FILE" <<SQL
SELECT 'CREATE DATABASE ${SENTINEL_DB} OWNER ${SENTINEL_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${SENTINEL_DB}')
\gexec
SQL

# Readable by the 'postgres' OS user too (su/sudo below may run as it,
# and mktemp's default 600 permissions would otherwise block that read).
chmod 644 "$ADMIN_SQL_FILE" "$CREATE_DB_SQL_FILE"

resolve_psql() {
    if [[ -n "${PSQL_PATH:-}" ]]; then
        echo "$PSQL_PATH"
        return
    fi
    if command -v psql >/dev/null 2>&1; then
        command -v psql
        return
    fi
    echo "ERROR: could not find 'psql' on PATH, and PSQL_PATH is not set." >&2
    echo "Install the PostgreSQL client tools, or set PSQL_PATH to the" >&2
    echo "full path to psql, then re-run this script." >&2
    exit 1
}

PSQL="$(resolve_psql)"

# Run SQL as the Postgres superuser, over the local Unix socket (no -h),
# so normal peer authentication applies instead of requiring a password
# over TCP. Tries, in order:
#   1. Already running as the `postgres` OS user           -> plain psql
#   2. Running as root                                     -> su postgres -c
#   3. `sudo` is available                                 -> sudo -u postgres
#   4. Last resort: `psql -U postgres` on whatever socket/host the
#      current OS user can already reach (covers e.g. macOS Homebrew
#      installs where your own login IS the Postgres superuser).
run_as_admin() {
    local sql_file="$1"
    local me
    me="$(id -un)"
    if [[ "$me" == "postgres" ]]; then
        timeout "$PSQL_TIMEOUT" "$PSQL" -d postgres -v ON_ERROR_STOP=1 -f "$sql_file"
        return $?
    fi
    if [[ "$(id -u)" == "0" ]]; then
        # su's -c takes a single command string, but the file is already
        # world-readable (mktemp default) and postgres can read it
        # directly - no need to smuggle SQL through an extra shell layer.
        timeout "$PSQL_TIMEOUT" su postgres -c "$PSQL -d postgres -v ON_ERROR_STOP=1 -f $sql_file"
        return $?
    fi
    if command -v sudo >/dev/null 2>&1 && timeout "$PSQL_TIMEOUT" sudo -n -u postgres "$PSQL" -d postgres -v ON_ERROR_STOP=1 -f "$sql_file" 2>/dev/null; then
        return 0
    fi
    timeout "$PSQL_TIMEOUT" "$PSQL" -U postgres -w -d postgres -v ON_ERROR_STOP=1 -f "$sql_file"
}

echo "== Sentinel local DB bootstrap =="

echo "-- Creating role '${SENTINEL_USER}' (if missing)..."
if ! run_as_admin "$ADMIN_SQL_FILE"; then
    echo >&2
    echo "ERROR: could not connect to Postgres as a superuser to create the '${SENTINEL_USER}' role." >&2
    echo "This script tries, in order: running as the 'postgres' OS user," >&2
    echo "'su postgres' (as root), 'sudo -u postgres', then a direct" >&2
    echo "'psql -U postgres'. If none of those work for your local install:" >&2
    echo "  * run the SQL in this script's ADMIN_SQL/CREATE_DB_SQL/EXT_SQL" >&2
    echo "    variables yourself via whatever admin login your install uses, or" >&2
    echo "  * skip local Postgres entirely and use 'docker compose up -d'" >&2
    echo "    from infra/ instead (see infra/README.md) — it self-seeds." >&2
    exit 1
fi
echo "   ok (role exists or was created)"

echo "-- Creating database '${SENTINEL_DB}' owned by '${SENTINEL_USER}' (if missing)..."
run_as_admin "$CREATE_DB_SQL_FILE"
echo "   ok (database exists or was created)"

echo "-- Enabling extensions (postgis, pgcrypto, vector) on '${SENTINEL_DB}'..."
export PGPASSWORD="$SENTINEL_PASSWORD"
timeout "$PSQL_TIMEOUT" "$PSQL" -h "$PGHOST" -p "$PGPORT" -U "$SENTINEL_USER" -d "$SENTINEL_DB" -v ON_ERROR_STOP=1 -c "$EXT_SQL"
echo "   ok"

echo
echo "Done. '${SENTINEL_USER}' / '${SENTINEL_DB}' are ready at ${PGHOST}:${PGPORT}."
echo "Next: cd model1-registry && pip install -r requirements-dev.txt && pytest"
