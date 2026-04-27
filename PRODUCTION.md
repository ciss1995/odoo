# Production Deployment Guide — Odoo Backend

## Overview

In production, each tenant gets their own Odoo container + PostgreSQL database, provisioned automatically by the Control Plane. Odoo serves as a **headless API backend** — the user-facing UI is provided by the desktop SPA (yiri-streamline-flow) and mobile PWA (feere-mobile).

## Architecture

```
Per tenant:
  ┌──────────────────────────────────────────────┐
  │  odoo-<slug>    (:8069)  — Odoo API backend  │
  │  db-<slug>      (:5432)  — PostgreSQL        │
  └──────────────────────────────────────────────┘

Shared:
  ┌──────────────────────────────────────────────┐
  │  desktop-spa    (:80)   — Desktop UI         │
  │  mobile-spa     (:80)   — Mobile PWA         │
  │  control-plane  (:8000) — Admin API          │
  │  traefik        (:80)   — Reverse proxy      │
  └──────────────────────────────────────────────┘
```

## How Tenants Are Provisioned

The Control Plane's provisioning service creates tenant stacks from a Jinja2 template (`templates/docker-compose.tenant.yml.j2`). Each provisioned tenant gets:

1. A PostgreSQL container with an isolated database
2. An Odoo container with:
   - Its own database connection
   - Tenant ID and Control Plane credentials
   - Docker labels for Traefik auto-discovery
   - Resource limits based on the tenant's plan (CPU, memory)

## Traefik Docker Labels

The provisioning service sets these labels on each Odoo container:

```yaml
labels:
  - "traefik.enable=true"

  # Desktop API route
  - "traefik.http.routers.tenant-<slug>-api.rule=Host(`<slug>.yourdomain.com`) && PathPrefix(`/api`)"
  - "traefik.http.routers.tenant-<slug>-api.entrypoints=websecure"
  - "traefik.http.routers.tenant-<slug>-api.tls.certresolver=letsencrypt"
  - "traefik.http.routers.tenant-<slug>-api.priority=100"
  - "traefik.http.services.tenant-<slug>-api.loadbalancer.server.port=8069"

  # Mobile API route
  - "traefik.http.routers.tenant-<slug>-mobile-api.rule=Host(`<slug>.m.yourdomain.com`) && PathPrefix(`/api`)"
  - "traefik.http.routers.tenant-<slug>-mobile-api.entrypoints=websecure"
  - "traefik.http.routers.tenant-<slug>-mobile-api.tls.certresolver=letsencrypt"
  - "traefik.http.routers.tenant-<slug>-mobile-api.priority=100"
  - "traefik.http.services.tenant-<slug>-mobile-api.loadbalancer.server.port=8069"

  - "traefik.docker.network=saas-net"
```

Traefik auto-discovers these labels and starts routing immediately. No config files to update.

## Environment Variables (Per Tenant)

Each Odoo container is configured with:

```bash
DB_HOST=db-<slug>
DB_PORT=5432
DB_USER=odoo_<slug>
DB_PASSWORD=<generated-strong-password>
DB_NAME=odoo_<slug>_db
TENANT_ID=<slug>
CONTROL_PLANE_URL=http://control-plane-app-1:8000
CONTROL_PLANE_TOKEN=<internal-api-key>
```

## Subscription Enforcement

The `base_api` addon's subscription enforcer (`services/subscription_enforcer.py`):

- Caches plan info from the Control Plane (5-minute TTL)
- Enforces: subscription status, user limits, module access, API quotas
- Reports API usage back to the Control Plane
- All enforcement is a **no-op** when `TENANT_ID`/`CONTROL_PLANE_URL`/`CONTROL_PLANE_TOKEN` are unset

## Resource Limits

The Control Plane maps plans to container resources:

| Plan | CPU | Memory | Max Users | API Calls/mo |
|------|-----|--------|-----------|--------------|
| Basic ($49) | 0.5 cores | 512 MB | 5 | 10,000 |
| Mid ($149) | 1 core | 1 GB | 25 | 50,000 |
| Full ($399) | 2 cores | 2 GB | Unlimited | Unlimited |

## Odoo Image

The Odoo Docker image should be pre-built and pushed to a registry:

```bash
# Build and tag
docker build -t your-registry/odoo-saas:latest .

# Push to registry
docker push your-registry/odoo-saas:latest
```

Set `ODOO_IMAGE` in the Control Plane's `.env` to point to this image.

## Backups

Each tenant's data lives in:
- **PostgreSQL**: Database per tenant — back up with `pg_dump`
- **Filestore**: Docker volume per tenant — back up the volume

```bash
# Backup a tenant's database
docker exec db-<slug> pg_dump -U odoo_<slug> odoo_<slug>_db | gzip > backup-<slug>.sql.gz
```

## Monitoring

- Odoo logs are written to the `odoo-logs` volume
- API usage is tracked by the Control Plane (per tenant, per month)
- The Control Plane admin dashboard shows tenant health and usage

## Suspending / Destroying Tenants

Managed via the Control Plane API:

```bash
# Suspend (stops containers, keeps data)
curl -X POST "http://admin.yourdomain.com/admin/tenants/<uuid>/suspend" \
  -H "Authorization: Bearer <admin-key>"

# Activate (restarts containers)
curl -X POST "http://admin.yourdomain.com/admin/tenants/<uuid>/activate" \
  -H "Authorization: Bearer <admin-key>"

# Destroy (removes everything — IRREVERSIBLE)
curl -X POST "http://admin.yourdomain.com/admin/tenants/<uuid>/destroy" \
  -H "Authorization: Bearer <admin-key>"
```
