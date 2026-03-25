#!/bin/bash
set -e

# Render the Odoo config template by substituting environment variables.
# The template at /etc/odoo/odoo.conf.template uses ${VAR} placeholders
# for database connection settings supplied by Railway's Postgres service.
envsubst '${DB_HOST} ${DB_PORT} ${DB_USER} ${DB_PASSWORD} ${DB_NAME}' \
    < /etc/odoo/odoo.conf.template \
    > /etc/odoo/odoo.conf

exec "$@"
