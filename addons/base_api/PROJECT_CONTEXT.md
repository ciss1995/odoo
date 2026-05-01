# Project Context — base_api + yiri-platform

Bootstrap context for picking up work on this codebase in a fresh chat. Read this first; it points to the deeper docs.

## The two repos

This is a multi-tenant Odoo SaaS spread across two sibling repos that ship together:

- `~/Projects/odoo/` — Odoo fork (this repo). The headless REST surface lives in `addons/base_api/`.
- `~/Projects/yiri-platform/` — control plane, Traefik, both SPAs, AI agent, root docker-compose.

On the server they sit at `/opt/yiri/odoo/` and `/opt/yiri/yiri-platform/`. The yiri-platform compose mounts `../odoo/addons/` read-only into the control-plane container, and the per-tenant compose template (rendered by the CP at provision time) does the same into each tenant's Odoo container. Code changes to `addons/base_api/` therefore need a tenant container restart, **not** an image rebuild.

## What base_api does

A single Odoo addon (`v19.0.1.0.0`, depends on base/web/sale/sale_stock/purchase/purchase_stock/account/hr/crm) that exposes `/api/v2/*` as a headless REST API. It does NOT modify Odoo core — it sits on top.

| Path | What |
|---|---|
| `controllers/simple_api.py` (4368 LoC) | Every `/api/v2/*` endpoint (~41 routes) |
| `models/api_session.py` | `api.session` table — sha256-hashed session tokens, expiry |
| `models/res_users.py` | Adds `api_key` field to `res.users` |
| `models/cors.py` | Inherits `ir.http` for preflight + response headers |
| `services/subscription_enforcer.py` | Calls CP for tenant info; 5-min cache; fails closed |
| `services/api_call_logger.py` | Buffered usage reporter to CP (batched) |
| `services/rate_limiter.py` | Per-IP login + per-user API throttle |
| `services/module_resolver.py` | Plan→module→model mapping |

### Auth — two manual modes

Every route is `auth='none', csrf=False` — Odoo session auth is bypassed and the controller does it manually:

- **Session token** (SPAs) — `POST /api/v2/auth/login` with username+password issues a 48-char token, stored as sha256 in `api.session`, 24h TTL. Sent back as `session-token` header on every subsequent request. `/refresh` allows a 1h grace past expiry.
- **API key** (programmatic) — `api-key` header. Validated via Odoo 19's native `res.users.apikeys._check_credentials(scope='rpc')`.

### Per-handler enforcement stack

Run in order in any write endpoint (e.g. `create_record` ~line 1304):

1. `_authenticate_session()` (or API key)
2. `_enforce_subscription()` — CP tenant status (active/trial/grace/suspended)
3. `_enforce_api_quota()` — monthly call budget
4. `_is_model_blocked()` — `BLOCKED_MODELS` deny-list
5. `_check_model_access()` — Odoo ACL per user+group
6. `_enforce_module_access()` — plan-based module gating
7. Rate limit (auth layer)

`BLOCKED_MODELS` (simple_api.py:16) keeps generic CRUD off auth/security/system models: `api.session`, `ir.cron`, `ir.rule`, `ir.model.access`, `res.users.apikeys`, `ir.attachment`, `base.module.update`, `ir.config_parameter`, `ir.module.module`, `ir.actions.server`, `base.automation`, `ir.model.data`.

### Endpoint groups

- Auth: `/auth/login|refresh|logout|me|test`
- Public: `/test`, `/public/branding`
- Generic CRUD over any model: `/search|create|update|delete/<model>[/<id>]`
- User mgmt: list/get/update + password + api-key + reset-password
- Field/model introspection: `/fields/<model>`, `/models`, `/groups`
- Convenience: `/partners`, `/products`, `/inventory/{adjust,decrement}`, `/picking/<id>/{validate,return}`, `/sales/<id>/create-invoice`, `/sales/in-store-purchase`, `/purchase/<id>/confirm`
- Analytics dashboards (8): `/analytics/{dashboard,crm,sales,invoicing,inventory,purchases,hr,projects}`
- `/modules/access` — per-user module gating
- `/internal/invalidate-cache` — CP→Odoo cache bust hook

### Talks back to the control plane

Two env vars baked in by the tenant compose template:

- `CONTROL_PLANE_URL`
- `CONTROL_PLANE_TOKEN` (== platform `INTERNAL_API_KEY`)

Plus `TENANT_ID` (which slug the container belongs to). With those:

- Reads: `GET /internal/tenants/<TENANT_ID>/info` — cached 5 min, fails closed on network error if no stale cache.
- Writes: `PUT /internal/tenants/<TENANT_ID>/usage/increment` — batched, 50-call or 30s flush.
- Receives: `POST /api/v2/internal/invalidate-cache` from CP when plans change.

`base_api` is **not** tenant-aware in code — it trusts the env it was started with. Tenancy is at the container level: one Odoo+Postgres pair per tenant.

## yiri-platform — what's around base_api

- `control-plane/` — FastAPI + Postgres + Alembic. Provisions tenants by rendering `templates/docker-compose.tenant.yml.j2` into `/data/tenants/<slug>/docker-compose.yml` and shelling `docker compose up -d` (mounts `/var/run/docker.sock`). Two-token auth: `ADMIN_API_KEY` (humans), `INTERNAL_API_KEY` (Odoo→CP). Bundles a React `admin-ui/`.
- `apps/web/` — Vite/React 18/TS desktop SPA. Session token in `localStorage[yiri-session]`, sent as `session-token` header. `BrandingContext` fetches `/api/v2/public/branding` on first paint. **Has a hard CI gate**: lint+test+build all must pass.
- `apps/mobile/` — Vite/React PWA. Zustand, i18next (en/fr/ar incl. RTL). Has a built-in demo mode (West African retail seed) gated in `store/app-store.ts`.
- `ai-agent/` — shared FastAPI assistant (Claude Sonnet 4 / Haiku 4.5). Per-platform, not per-tenant. Validates SPA's `session-token` against tenant Odoo `/api/v2/auth/me` then issues tool-use calls back into Odoo.
- `traefik/` + root `docker-compose.yml` — entrypoints, ACME, host-based routing.

## Request flow

```
<slug>.amyslab.com/         → Traefik → desktop-spa     (priority 1, catchall)
<slug>.amyslab.com/api/*    → Traefik → <slug>-odoo-1   (priority 100) → base_api
<slug>.amyslab.com/ai/*     → Traefik → ai-agent        (priority 90, /ai stripped)
<slug>.m.amyslab.com/*      → same pattern, mobile-spa instead of desktop-spa
admin.amyslab.com           → Traefik → control plane (admin UI + API)
api.amyslab.com             → Traefik → control plane (admin API alias)
```

## Local vs production

### Local

```bash
docker network create saas-net
git clone https://github.com/ciss1995/odoo ../odoo  # sibling clone is mandatory
cp .env.example .env                                 # only ANTHROPIC_API_KEY required
docker compose up -d --build
docker compose exec app alembic upgrade head
```

URLs:

- `http://admin.localhost` — admin UI
- `http://api.localhost` — admin API
- `http://localhost:8000` — CP direct (bypasses Traefik)
- `http://localhost:8081` — Traefik dashboard
- `http://<slug>.localhost` — tenant SPA after provisioning

Local quirks:

- Traefik **file provider** with hard-coded routers in `traefik/dynamic/routers.yml` for `main-company`, `test-corp`, `cheick`. New local tenants need a manual entry. (Production auto-discovers via Docker labels — file provider is off there.)
- `control-plane/app/` and `templates/` are bind-mounted (hot reload). Admin-ui dist is baked into the image.
- `docker-compose.override.yml` overrides `ODOO_ADDONS_DIR` to the local addons path so the CP can read it for provisioning.
- Default secrets are placeholders (`dev-admin-key-change-me`, `dev-internal-key-change-me`).
- Traefik `:8081` dashboard insecurely exposed.

### Production

- Hetzner Cloud VPS, IP `178.105.35.11`, user `yiri`. Layout under `/opt/yiri/`.
- DNS: two wildcard A records `*.amyslab.com` and `*.m.amyslab.com`. **No apex record** (intentional — avoids TXT collisions during Let's Encrypt DNS-01).
- TLS via DNS-01 + GoDaddy API (`GODADDY_API_KEY`/`GODADDY_API_SECRET`).
- Traefik **Docker provider** is enabled — tenant containers self-register via labels written by the compose template.
- `COMPOSE_PROJECT_NAME=yiri_control_panel` is **pinned** so existing volumes (`yiri_control_panel_*`) stay attached after the monorepo rename. Don't change this.
- **Four files are skip-worktree** so `git pull` never touches them: `control-plane/Dockerfile`, root `docker-compose.yml`, `traefik/traefik.yml`, `traefik/dynamic/routers.yml`. Production-specific edits go on the server directly.
- `ODOO_IMAGE=ghcr.io/ciss1995/odoo-saas:1.0` (vs local `odoo-odoo`).
- Daily Postgres backup cron at 03:00 → `/opt/yiri/backups/`, 7d daily / 4w weekly retention. Hetzner snapshots on top.

### Deploying

For backend (addon) changes:

```bash
cd /opt/yiri/odoo && git pull
for c in $(docker ps --format '{{.Names}}' | grep -E '^[a-z0-9-]+-odoo-1$'); do
  docker restart "$c"
done
```

For platform changes:

```bash
cd /opt/yiri/yiri-platform && git pull --ff-only origin main
docker compose build app desktop-spa mobile-spa ai-agent  # only what changed
docker compose up -d <service>
```

Caveat: the server's PAT was scoped to old per-component repos and can't yet pull the monorepo — until the PAT is rotated, deploys are rsync from a dev machine.

## Where to look

Backend (this repo, `addons/base_api/`):

- `COMPLETE_API_GUIDE.md` — per-endpoint reference (3472 lines)
- `MODEL_DISCOVERY_GUIDE.md`
- `AUTHENTICATION_TROUBLESHOOTING.md`
- `ROLE_BASED_ACCESS_CONTROL_TEST.md`
- `SECURITY_AUDIT.md`
- `SETTINGS_API_GUIDE.md`
- `NOTIFICATIONS.md` — current generic-CRUD shape over `mail.message`/`mail.activity`
- `TEST_USERS.md`

Platform (sibling repo `yiri-platform/`):

- Root `README.md`, `LOCAL.md`, `PRODUCTION.md`, `CLAUDE.md`, `PROJECT_OVERVIEW.md`
- `apps/web/CLAUDE.md` — the CI gate rules
- `apps/web/API_ROLE_REFERENCE.md` — per-endpoint role spec
- `apps/web/CACHING_STRATEGY.md`, `apps/web/INSIGHTS_ANALYTICS_PLAN.md`

## Conventions worth knowing

- The UI is **not** a security boundary. Every check (model access, plan, quota, blocked-models) is enforced in `simple_api.py`. The SPA hides controls it knows will 403, but trusting that is the addon's job.
- Generic CRUD on a sensitive model = add it to `BLOCKED_MODELS` and write a dedicated endpoint that enforces tighter rules.
- New endpoints follow the existing pattern: `_authenticate_session()` → `_enforce_subscription()` → business logic → `_log_api_call()` → `_response()` / `_error_response()`. CORS is automatic via the `ir.http` override.
- `request.env` is already user-scoped after `_authenticate_session()` — avoid `sudo()` unless you have a specific reason and have considered the ACL bypass.
