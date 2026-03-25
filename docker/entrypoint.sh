#!/bin/bash
set -e

parse_database_url() {
    if [ -z "${DATABASE_URL:-}" ]; then
        return
    fi

    # Parse only if DB_* was not explicitly provided.
    if [ -n "${DB_HOST:-}" ] && [ -n "${DB_USER:-}" ] && [ -n "${DB_NAME:-}" ]; then
        return
    fi

    eval "$(
        python3 - <<'PY'
import os
from urllib.parse import urlparse, unquote

url = os.environ.get("DATABASE_URL", "")
if not url:
    raise SystemExit(0)

p = urlparse(url)
if p.scheme not in ("postgres", "postgresql"):
    raise SystemExit(0)

host = p.hostname or ""
port = str(p.port or 5432)
user = unquote(p.username or "")
password = unquote(p.password or "")
dbname = p.path.lstrip("/") if p.path else ""

def emit(key, value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    print(f'export {key}="{escaped}"')

emit("DB_HOST", host)
emit("DB_PORT", port)
emit("DB_USER", user)
emit("DB_PASSWORD", password)
emit("DB_NAME", dbname)
PY
    )"
}

ensure_non_superuser() {
    if [ "${DB_USER}" != "postgres" ]; then
        return
    fi

    local app_user="${ODOO_DB_APP_USER:-odoo_app}"
    local app_password="${ODOO_DB_APP_PASSWORD:-${DB_PASSWORD}}"
    local admin_password="${DB_PASSWORD}"
    local admin_user="${DB_USER}"

    echo "Detected postgres superuser; provisioning app role '${app_user}' for Odoo."

    DB_HOST="${DB_HOST}" \
    DB_PORT="${DB_PORT}" \
    DB_NAME="${DB_NAME}" \
    ADMIN_USER="${admin_user}" \
    ADMIN_PASSWORD="${admin_password}" \
    APP_USER="${app_user}" \
    APP_PASSWORD="${app_password}" \
    python3 - <<'PY'
import os
import psycopg2
from psycopg2 import sql

host = os.environ["DB_HOST"]
port = int(os.environ["DB_PORT"])
db_name = os.environ.get("DB_NAME", "").strip()
admin_user = os.environ["ADMIN_USER"]
admin_password = os.environ.get("ADMIN_PASSWORD", "")
app_user = os.environ["APP_USER"]
app_password = os.environ.get("APP_PASSWORD", "")

conn = psycopg2.connect(
    host=host,
    port=port,
    user=admin_user,
    password=admin_password,
    dbname="postgres",
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,))
role_exists = cur.fetchone() is not None

if role_exists:
    cur.execute(
        sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD %s").format(
            sql.Identifier(app_user)
        ),
        (app_password,),
    )
else:
    cur.execute(
        sql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD %s").format(
            sql.Identifier(app_user)
        ),
        (app_password,),
    )

if db_name:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    db_exists = cur.fetchone() is not None
    if db_exists:
        cur.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(db_name),
                sql.Identifier(app_user),
            )
        )
        cur.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                sql.Identifier(db_name),
                sql.Identifier(app_user),
            )
        )

cur.close()
conn.close()
PY

    export DB_USER="${app_user}"
    export DB_PASSWORD="${app_password}"
}

# Railway often provides DATABASE_URL / PG* variables.
# Docker-compose local dev continues to use DB_* defaults.
parse_database_url
export DB_HOST="${DB_HOST:-${PGHOST:-db}}"
export DB_PORT="${DB_PORT:-${PGPORT:-5432}}"
export DB_USER="${DB_USER:-${PGUSER:-odoo}}"
export DB_PASSWORD="${DB_PASSWORD:-${PGPASSWORD:-odoo}}"
export DB_NAME="${DB_NAME:-${PGDATABASE:-}}"

ensure_non_superuser

# Railway sets $PORT for the app to listen on; local dev defaults to 8069.
export HTTP_PORT="${PORT:-8069}"
# Behind Railway's reverse proxy, proxy_mode must be True.
export PROXY_MODE="${PROXY_MODE:-False}"
if [ -n "${RAILWAY_ENVIRONMENT:-}" ]; then
    export PROXY_MODE="True"
    export ODOO_LOGFILE="${ODOO_LOGFILE:-False}"
else
    export ODOO_LOGFILE="${ODOO_LOGFILE:-/var/log/odoo/odoo.log}"
fi

envsubst '${DB_HOST} ${DB_PORT} ${DB_USER} ${DB_PASSWORD} ${DB_NAME} ${HTTP_PORT} ${PROXY_MODE} ${ODOO_LOGFILE}' \
    < /etc/odoo/odoo.conf.template \
    > /etc/odoo/odoo.conf

exec "$@"
