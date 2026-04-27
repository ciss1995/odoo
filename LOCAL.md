# Local Development Guide — Odoo Backend

## Prerequisites

- **Docker Desktop** (Mac/Windows) or Docker Engine (Linux)

## Quick Start

```bash
# 1. Create the shared Docker network (one-time)
docker network create saas-net 2>/dev/null || true

# 2. Configure environment
cp .env.example .env
# Defaults are fine for local dev

# 3. Build and start Odoo + PostgreSQL
docker compose up -d --build

# 4. Wait for Odoo to initialize (~1-2 minutes on first run)
docker compose logs -f odoo
# Look for: "HTTP service (werkzeug) running on ..."
```

Odoo is now running at **http://localhost:8069**.

## Local URLs

| URL | Purpose |
|-----|---------|
| `http://localhost:8069` | Odoo web interface (direct) |
| `http://localhost:8069/api/v2/auth/login` | REST API login endpoint |
| `http://localhost:5433` | PostgreSQL (external port) |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ODOO_DB` | `odoo19_db` | Database name |
| `ODOO_DB_USER` | `odoo` | Database user |
| `ODOO_DB_PASSWORD` | `odoo` | Database password |
| `ODOO_INIT_MODULES` | (full preset) | Comma-separated modules to install |
| `TENANT_ID` | `main-company` | Tenant slug (for control plane integration) |
| `CONTROL_PLANE_URL` | `http://control-plane-app-1:8000` | Control Plane URL |
| `CONTROL_PLANE_TOKEN` | `dev-internal-key-change-me` | Internal API key |

## Module Presets

Choose a preset in `.env` based on what you're developing:

```bash
# Minimal (API only — fastest startup)
ODOO_INIT_MODULES=base,base_api

# Standard (core business apps)
ODOO_INIT_MODULES=base,base_api,sale,crm,hr,purchase,stock,account

# Full (all modules — matches production)
ODOO_INIT_MODULES=base,base_api,api_doc,account,...  # (see .env.example for full list)
```

## Connecting to the Control Plane

The Odoo container is on the `saas-net` network, so it can communicate with the Control Plane:

- `TENANT_ID`: identifies this Odoo instance to the Control Plane
- `CONTROL_PLANE_URL`: where to report usage / fetch plan limits
- `CONTROL_PLANE_TOKEN`: authentication token for internal API

When these are **not set**, subscription enforcement is disabled — Odoo works standalone.

## Connecting the Frontend SPAs

Both frontend apps (desktop + mobile) connect to Odoo via `/api/v2`:

- **Desktop SPA** (yiri-streamline-flow): Set `VITE_DATA_SOURCE=api` and `VITE_API_PROXY_TARGET=http://localhost:8069`
- **Mobile PWA** (feere-mobile): Connects directly, set base URL at login

Or run everything through Traefik via the Control Plane's `docker-compose.yml` — the SPAs are then accessible at `<slug>.localhost` and `<slug>.m.localhost`.

## Custom Addons

Custom addons are in the `addons/` directory, mounted at `/opt/odoo/addons` in the container. Key custom addons:

- `base_api` — REST API controller (3400+ lines), provides `/api/v2` endpoints
- `api_doc` — API documentation
- `debt_management` — Customer debt tracking

Changes to addon files are picked up automatically (Odoo watches the addons directory).

## Database Access

Connect to PostgreSQL directly:

```bash
# Via docker
docker compose exec db psql -U odoo odoo19_db

# Via local client
psql -h localhost -p 5433 -U odoo odoo19_db
```

## Logs

```bash
# Follow Odoo logs
docker compose logs -f odoo

# Logs are also persisted in a Docker volume (odoo-logs)
```

## Rebuilding

```bash
# After changing Dockerfile or system dependencies
docker compose up -d --build odoo

# After adding new Python packages
docker compose up -d --build odoo
```
