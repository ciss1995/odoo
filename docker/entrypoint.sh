#!/bin/bash
set -e

# Defaults match docker-compose local dev (Postgres service named "db").
# Railway overrides these via its own environment variables.
export DB_HOST="${DB_HOST:-db}"
export DB_PORT="${DB_PORT:-5432}"
export DB_USER="${DB_USER:-odoo}"
export DB_PASSWORD="${DB_PASSWORD:-odoo}"
export DB_NAME="${DB_NAME:-}"

envsubst '${DB_HOST} ${DB_PORT} ${DB_USER} ${DB_PASSWORD} ${DB_NAME}' \
    < /etc/odoo/odoo.conf.template \
    > /etc/odoo/odoo.conf

exec "$@"
