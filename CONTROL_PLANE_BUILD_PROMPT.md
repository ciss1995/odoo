# Control Plane + Subscription Enforcer

> **Last updated:** 2026-04-04 (API Metering + Tenant Provisioning + Traefik Routing added)
> **Status:** Built and integrated. All 47 tests passing.

---

## Running Locally

### Prerequisites

- Docker and Docker Compose installed
- Both projects cloned:
  - `/Users/cheickcisse/Projects/control-plane/` (Control Plane — FastAPI)
  - `/Users/cheickcisse/Projects/odoo/` (Odoo 19 + base_api with enforcer)

### Ports Used

| Service | Port | URL |
|---------|------|-----|
| Control Plane API | 8000 | http://localhost:8000 |
| Control Plane PostgreSQL | 5434 | localhost:5434 |
| Odoo API (main-company) | 8069 | http://localhost:8069 |
| Odoo PostgreSQL (main-company) | 5433 | localhost:5433 |
| Provisioned tenant Odoo | 8069 (internal) | `http://<slug>-odoo-1:8069` on saas-net |

### Step 1: Create the shared Docker network (one-time)

Both services communicate over a shared network called `saas-net`. This only needs to be created once:

```bash
docker network create saas-net
```

If it already exists, the command will error — that's fine.

### Step 2: Start the Control Plane

```bash
cd /Users/cheickcisse/Projects/control-plane
docker compose up -d
```

On first run, run the database migrations to create tables and seed the three default plans (Basic, Mid, Full):

```bash
docker compose exec app alembic upgrade head
```

Verify it's running:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}

# Check the seeded plans
curl -s http://localhost:8000/admin/plans \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
```

### Step 3: Register a tenant (one-time per Odoo instance)

The current Odoo instance is registered as tenant `main-company` on the Mid plan (25 users, 8 modules). If the tenant already exists, skip this step.

```bash
curl -s -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "main-company",
    "company_name": "Main Company",
    "admin_email": "admin@main.com",
    "plan_slug": "mid",
    "status": "active",
    "payment_status": "current",
    "container_host": "odoo-odoo-1",
    "odoo_port": 8069,
    "internal_token": "dev-internal-key-change-me"
  }' | python3 -m json.tool
```

Check the tenant exists:

```bash
curl -s http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
```

### Step 4: Start Odoo

```bash
cd /Users/cheickcisse/Projects/odoo
docker compose up -d
```

The `docker-compose.yml` already has the enforcer env vars configured:

```yaml
TENANT_ID: main-company
CONTROL_PLANE_URL: http://control-plane-app-1:8000
CONTROL_PLANE_TOKEN: dev-internal-key-change-me
```

### Step 5: Verify everything works

```bash
# Login
curl -s -X POST http://localhost:8069/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' | python3 -m json.tool

# Use the session_token from above:
SESSION="<paste token here>"

# Check /auth/me — should include plan info + module_access with in_plan flags
curl -s http://localhost:8069/api/v2/auth/me \
  -H "session-token: $SESSION" | python3 -m json.tool

# Access an allowed module (CRM is in mid plan)
curl -s "http://localhost:8069/api/v2/search/crm.lead?limit=2" \
  -H "session-token: $SESSION" | python3 -m json.tool

# Access a blocked module (inventory is NOT in mid plan)
curl -s "http://localhost:8069/api/v2/search/stock.picking?limit=2" \
  -H "session-token: $SESSION" | python3 -m json.tool
# → 403 MODULE_NOT_IN_PLAN
```

---

## Day-to-Day Operations

### Stopping / Starting

```bash
# Stop everything
cd /Users/cheickcisse/Projects/control-plane && docker compose down
cd /Users/cheickcisse/Projects/odoo && docker compose down

# Start everything (Control Plane first, then Odoo)
cd /Users/cheickcisse/Projects/control-plane && docker compose up -d
cd /Users/cheickcisse/Projects/odoo && docker compose up -d
```

Order matters: start the Control Plane first so Odoo can reach it. If Odoo starts before the Control Plane is up, the enforcer will use stale cache or fail gracefully (requests still work, enforcement is skipped until the CP becomes reachable).

### Running Odoo without enforcement (standalone mode)

To run Odoo without the Control Plane (e.g., for quick local testing), comment out or remove the three env vars from `odoo/docker-compose.yml`:

```yaml
# TENANT_ID: main-company
# CONTROL_PLANE_URL: http://control-plane-app-1:8000
# CONTROL_PLANE_TOKEN: dev-internal-key-change-me
```

Then restart Odoo. The enforcer detects missing env vars and becomes a no-op — all modules accessible, no user limits, no plan info in `/auth/me`.

### Viewing Odoo logs (to see enforcer activity)

```bash
docker compose logs odoo -f --tail=50
```

Look for `subscription_enforcer` log lines (fetching from CP, cache hits/misses).

---

## Managing Plans and Tenants

### Dev API keys

| Key | Value | Used for |
|-----|-------|----------|
| Admin API key | `dev-admin-key-change-me` | `Authorization: Bearer ...` on `/admin/*` endpoints |
| Internal API key | `dev-internal-key-change-me` | `Authorization: Bearer ...` on `/internal/*` endpoints and as `CONTROL_PLANE_TOKEN` in Odoo |

### Change a tenant's plan

```bash
# Get the tenant UUID
TENANT_UUID=$(curl -s http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -c "
import sys,json; tenants=json.load(sys.stdin)
t = next(t for t in tenants if t['slug']=='main-company')
print(t['id'])")

# Get the target plan UUID
FULL_PLAN_UUID=$(curl -s http://localhost:8000/admin/plans \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -c "
import sys,json; plans=json.load(sys.stdin)
p = next(p for p in plans if p['slug']=='full')
print(p['id'])")

# Change plan (this also pushes cache invalidation to Odoo)
curl -s -X PUT "http://localhost:8000/admin/tenants/$TENANT_UUID/plan" \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d "{\"new_plan_id\": \"$FULL_PLAN_UUID\", \"reason\": \"Upgrading for testing\", \"changed_by\": \"admin\"}" \
  | python3 -m json.tool
```

### Add extra modules to a tenant (without changing plan)

```bash
curl -s -X PUT "http://localhost:8000/admin/tenants/$TENANT_UUID" \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"extra_modules": ["project", "inventory"]}' | python3 -m json.tool

# Push cache invalidation so Odoo picks it up immediately
curl -s -X POST http://localhost:8069/api/v2/internal/invalidate-cache \
  -H "Authorization: Bearer dev-internal-key-change-me"
```

### Override user limit for a tenant

```bash
curl -s -X PUT "http://localhost:8000/admin/tenants/$TENANT_UUID" \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"max_users_override": 50}' | python3 -m json.tool

curl -s -X POST http://localhost:8069/api/v2/internal/invalidate-cache \
  -H "Authorization: Bearer dev-internal-key-change-me"
```

To remove the override (go back to plan default): `{"max_users_override": null}`

### Check what the enforcer sees

```bash
curl -s http://localhost:8000/internal/tenants/main-company/info \
  -H "Authorization: Bearer dev-internal-key-change-me" | python3 -m json.tool
```

This returns the exact data the Odoo enforcer caches: plan details, effective limits (with overrides applied), payment status.

### Create a new plan

```bash
curl -s -X POST http://localhost:8000/admin/plans \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "starter",
    "name": "Starter Plan",
    "max_users": 3,
    "max_api_calls": 5000,
    "storage_limit_gb": 2,
    "allowed_modules": ["contacts", "crm"],
    "price_cents": 2900
  }' | python3 -m json.tool
```

---

## Current Tenant Configuration

The existing Odoo instance is registered as:

| Field | Value |
|-------|-------|
| Tenant slug | `main-company` |
| Plan | Mid Tier ($149/mo) |
| Max users | 25 (plan default) |
| Current users | 16 active internal users |
| Allowed modules | contacts, crm, sales, hr, purchase, accounting, products, calendar |
| Blocked modules | inventory, project, debt (not in mid plan) |

### What existing users experience

The enforcer does NOT touch existing users or data. It only gates **new** actions:

| Action | Behavior |
|--------|----------|
| Existing 16 users logging in and working | No change — works exactly as before |
| Creating user #17 through #25 | Allowed (within mid plan limit) |
| Creating user #26 | Blocked: `403 USER_LIMIT_REACHED` |
| Searching `crm.lead`, `sale.order`, etc. | Allowed (in mid plan) |
| Searching `stock.picking` or `project.project` | Blocked: `403 MODULE_NOT_IN_PLAN` |
| `GET /api/v2/auth/me` response | Now includes `plan` block + `in_plan` flag per module |

---

## API Metering / Usage Tracking

API calls from every tenant are metered and reported to the Control Plane. Usage data powers quota enforcement and admin dashboards.

### How it works

1. **Odoo side** (`addons/base_api/services/api_call_logger.py`): A singleton `ApiCallLogger` buffers API calls in memory and flushes to the Control Plane every **30 seconds** or **50 calls** (whichever comes first). Non-blocking — uses background threads.

2. **Control Plane side**: Usage is stored in `api_usage_monthly` (composite PK: tenant_id + year_month). Counters are incremented atomically.

### Usage endpoints

```bash
# Increment usage (called by Odoo containers automatically)
curl -X PUT http://localhost:8000/internal/tenants/main-company/usage/increment \
  -H "Authorization: Bearer dev-internal-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"calls": 10, "read_calls": 7, "write_calls": 3, "response_ms": 500}'

# View current month usage (admin)
TENANT_UUID="<uuid>"
curl -s http://localhost:8000/admin/tenants/$TENANT_UUID/usage \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool

# View usage history (admin)
curl -s "http://localhost:8000/admin/tenants/$TENANT_UUID/usage/history?months=6" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
```

### Quota enforcement

`check_api_quota()` is wired into every authenticated endpoint alongside `_enforce_subscription()`. When a tenant exceeds `max_api_calls`, requests return `429 API_QUOTA_EXCEEDED`. Graceful degradation: if the Control Plane is unreachable, quota enforcement is skipped.

---

## Tenant Provisioning Pipeline

New tenants can be provisioned via the admin API. Each tenant gets its own Docker Compose stack (PostgreSQL + Odoo) on the shared `saas-net` network.

### Prerequisites

- The Odoo Docker image must be pre-built (named `odoo-odoo`). Build it from the Odoo project: `cd /Users/cheickcisse/Projects/odoo && docker compose build odoo`
- Docker socket must be accessible to the Control Plane container (already configured in `docker-compose.yml`)

### Provisioning a new tenant

```bash
# Step 1: Create the tenant record
curl -s -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer dev-admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "acme-corp",
    "company_name": "Acme Corporation",
    "admin_email": "admin@acme.com",
    "plan_slug": "mid",
    "status": "provisioning"
  }' | python3 -m json.tool

# Step 2: Trigger provisioning (returns immediately with job ID)
TENANT_UUID="<uuid from step 1>"
curl -s -X POST "http://localhost:8000/admin/tenants/$TENANT_UUID/provision" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
# → {"status": "provisioning", "job_id": "..."}

# Step 3: Poll provisioning status
curl -s "http://localhost:8000/admin/tenants/$TENANT_UUID/provision/status" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
# → {"status": "running", "step": "init_database", ...}
# → {"status": "completed", "step": "complete", ...}
```

### What provisioning does (9-step pipeline)

1. **Generate credentials** — DB name, user, password, internal token, admin password
2. **Create tenant directory** — `/data/tenants/<slug>/` with `data/postgres/` and `data/filestore/`
3. **Render docker-compose.yml** — From Jinja2 template with plan-based resource limits
4. **Start containers** — `docker compose up -d`
5. **Wait for PostgreSQL** — Health check polling (60s timeout)
6. **Initialize Odoo database** — `odoo-bin -i <plan_modules> --stop-after-init`
7. **Create admin user** — Updates default admin password in DB
8. **Restart Odoo** — Restarts with full config, waits for HTTP health
9. **Update tenant record** — Sets `container_host`, `odoo_port`, `internal_token`, `status=active`

### Plan-based resource limits

| Plan | CPUs | Memory | DB Memory | Workers | DB Max Connections |
|------|------|--------|-----------|---------|-------------------|
| Basic | 1 | 1G | 512M | 2 | 16 |
| Mid | 2 | 2G | 1G | 4 | 32 |
| Full | 4 | 4G | 2G | 8 | 64 |

### Deprovisioning

```bash
# Suspend — stops containers, keeps data
curl -s -X POST "http://localhost:8000/admin/tenants/$TENANT_UUID/suspend" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool

# Activate — restarts stopped containers
curl -s -X POST "http://localhost:8000/admin/tenants/$TENANT_UUID/activate" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool

# Destroy — removes containers, volumes, and directory (IRREVERSIBLE)
curl -s -X POST "http://localhost:8000/admin/tenants/$TENANT_UUID/destroy" \
  -H "Authorization: Bearer dev-admin-key-change-me" | python3 -m json.tool
```

### Provisioning architecture

```
Control Plane container
    │
    │  POST /admin/tenants/{id}/provision
    │  → creates ProvisioningJob (pending)
    │  → kicks off background task
    │  → returns 202 immediately
    │
    │  Background task:
    │  1. Generate credentials
    │  2. mkdir /data/tenants/<slug>/
    │  3. Render docker-compose.yml (Jinja2)
    │  4. docker compose up -d
    │  5. Wait for DB healthy
    │  6. odoo-bin -i <modules> --stop-after-init
    │  7. Set admin password
    │  8. Restart Odoo, wait for HTTP
    │  9. Update tenant → status=active
    │
    ├── /data/tenants/acme-corp/
    │   ├── docker-compose.yml
    │   └── data/
    │       ├── postgres/
    │       └── filestore/
    │
    └── saas-net (shared Docker network)
        ├── acme-corp-db-1
        ├── acme-corp-odoo-1  ←── reachable by CP for cache invalidation
        └── control-plane-app-1
```

---

## Traefik Routing / Subdomain Management

Traefik is a reverse proxy that routes `<tenant-slug>.localhost` (or `<tenant-slug>.platform.example.com` in production) to the correct tenant Odoo container. It auto-discovers containers via Docker labels.

### How it works

```
Client browser
    │
    │  https://acme-corp.platform.example.com/api/v2/search/crm.lead
    │
    ▼
┌────────────────────────────────────────────────────┐
│  Traefik (port 80 / 443)                           │
│                                                     │
│  Reads Host header → looks up Docker labels         │
│  Host: acme-corp.platform.example.com               │
│  → route to acme-corp-odoo-1:8069                   │
│                                                     │
│  Host: admin.platform.example.com                   │
│  → route to control-plane-app-1:8000                │
└────────────────────────────────────────────────────┘
    │                              │
    ▼                              ▼
acme-corp-odoo-1:8069      control-plane-app-1:8000
(tenant container)          (admin API + dashboard)
```

### Local development URLs

With the default `BASE_DOMAIN=localhost`, Traefik routes:

| URL | Routes to | Purpose |
|-----|-----------|---------|
| `http://admin.localhost` | Control Plane (port 8000) | Admin dashboard + API |
| `http://api.localhost` | Control Plane (port 8000) | Same (alias) |
| `http://<slug>.localhost` | Tenant Odoo (port 8069) | Tenant's Odoo API |
| `http://localhost:8080` | Traefik dashboard | Traefik's own monitoring UI |
| `http://localhost:8000` | Control Plane (direct) | Still works via port mapping |

Most systems resolve `*.localhost` to `127.0.0.1` automatically (no `/etc/hosts` editing needed).

### Starting Traefik

Traefik is included in the Control Plane docker-compose. No separate steps:

```bash
cd /Users/cheickcisse/Projects/control-plane
docker compose up -d
```

This starts Traefik alongside the CP app and database. Traefik auto-discovers the CP app via its Docker labels.

When you provision a new tenant, Traefik auto-discovers the tenant's Odoo container (via labels in the generated docker-compose.yml) and starts routing to it within seconds.

### Verifying Traefik routing

```bash
# Check Traefik dashboard
open http://localhost:8080

# Access Control Plane via subdomain
curl -s http://admin.localhost/health
# → {"status":"ok"}

# Access a provisioned tenant via subdomain
curl -s -X POST http://acme-corp.localhost/api/v2/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<admin_password>"}'
```

### Production setup

For production with a real domain (e.g., `platform.example.com`):

1. **Set a wildcard DNS record**: `*.platform.example.com → <your-server-ip>`

2. **Update `BASE_DOMAIN`** in `docker-compose.yml`:
   ```yaml
   BASE_DOMAIN: platform.example.com
   ```

3. **Enable TLS** — uncomment the Let's Encrypt section in `traefik/traefik.yml`:
   ```yaml
   certificatesResolvers:
     letsencrypt:
       acme:
         email: admin@example.com
         storage: /data/acme.json
         dnsChallenge:
           provider: cloudflare
   ```

4. **Enable HTTPS redirect** — uncomment `redirect-to-https` middleware in `traefik/dynamic/middlewares.yml`.

5. Restart: `docker compose up -d`

Traefik will automatically obtain wildcard TLS certificates via DNS challenge and route all `*.platform.example.com` subdomains.

### Cost

- **Traefik**: Free (open-source, Apache 2.0)
- **Let's Encrypt TLS**: Free
- **Domain name**: ~$10-15/year depending on TLD
- **Total**: $0 beyond the domain name you likely already have

---

## Architecture Reference

| Component | Location | Port | Role |
|-----------|----------|------|------|
| **Traefik** | (inside docker) | 80, 443, 8080 | Reverse proxy — routes `<slug>.domain` to correct tenant container |
| **Control Plane** | `/Users/cheickcisse/Projects/control-plane/` | 8000 | Manages plans, tenants, memberships. Source of truth for "who can do what" |
| **Control Plane DB** | (inside docker) | 5434 | PostgreSQL with `plans`, `tenants`, `plan_changes`, `provisioning_jobs`, `api_usage_monthly` tables |
| **Odoo + base_api** | `/Users/cheickcisse/Projects/odoo/` | 8069 | Tenant Odoo instance. `base_api` enforcer checks limits on every API call |
| **Odoo DB** | (inside docker) | 5433 | PostgreSQL with Odoo data (users, CRM, sales, etc.) |
| **saas-net** | Docker network | — | Shared network so all containers can reach each other |

```
                         ┌─── saas-net (shared Docker network) ───┐
                         │                                         │
Browser ──► Traefik (:80/:443)                                    │
                │                                                  │
                ├── acme.localhost ──► acme-corp-odoo-1:8069       │
                ├── admin.localhost ──► control-plane-app-1:8000   │
                └── xyz.localhost ──► xyz-inc-odoo-1:8069          │
                                                                   │
Tenant Odoo container              Control Plane container         │
(acme-corp-odoo-1)                 (control-plane-app-1)           │
    │                                   │                          │
    │  enforcer cache miss:             │                          │
    │  GET /internal/tenants/acme/info  │                          │
    │  ────────────────────────────────►│                          │
    │                                   │  joins tenant + plan,    │
    │  ◄────────────────────────────────│  computes effective      │
    │  {plan, effective, status, usage} │  limits                  │
    │                                   │                          │
    │  on plan change (admin API):      │                          │
    │  POST /api/v2/internal/           │                          │
    │      invalidate-cache             │                          │
    │  ◄────────────────────────────────│                          │
    │  clears cache immediately         │                          │
    └───────────────────────────────────┴──────────────────────────┘
```

---

## Build Status

| Part | Status | Location |
|------|--------|----------|
| **Control Plane (FastAPI)** | COMPLETE | `/Users/cheickcisse/Projects/control-plane/` |
| **Subscription Enforcer (base_api)** | COMPLETE | `/Users/cheickcisse/Projects/odoo/addons/base_api/services/` |
| **Integration (network + env vars + tenant)** | COMPLETE | Both `docker-compose.yml` files configured |
| **API Metering / Usage Tracking** | COMPLETE | CP: `api_usage_monthly` table, usage endpoints. Odoo: `ApiCallLogger` + quota enforcement |
| **Tenant Provisioning Pipeline** | COMPLETE | CP: provisioning service, Jinja2 template, job tracking, de/re-provisioning |
| **Traefik Routing** | COMPLETE | Subdomain routing, auto-discovery, TLS-ready. `<slug>.localhost` in dev, `<slug>.domain.com` in prod |
| **End-to-end tested** | COMPLETE | All enforcement scenarios verified (see below) |

### Automated test results (2026-04-04)

**47/47 tests pass** across the Control Plane test suite:

| Suite | Tests | Status |
|-------|-------|--------|
| `test_plans.py` | 7 | ALL PASS |
| `test_tenants.py` | 6 | ALL PASS |
| `test_internal.py` | 7 | ALL PASS |
| `test_usage.py` | 8 | ALL PASS |
| `test_provisioning.py` | 18 | ALL PASS |

### Verified manual test results (2026-04-04)

| Test | Result |
|------|--------|
| `/auth/me` returns plan info (slug, max_users, current_users, can_create_users) | PASS |
| `module_access` includes `in_plan: true/false` per module | PASS |
| CRM search (in plan) returns data | PASS |
| Inventory search (not in plan) returns 403 `MODULE_NOT_IN_PLAN` | PASS |
| Project search (not in plan) returns 403 `MODULE_NOT_IN_PLAN` | PASS |
| System model search (res.users, no module mapping) returns data | PASS |
| Add `extra_modules` + invalidate cache, blocked module becomes accessible | PASS |
| Remove `extra_modules` + invalidate cache, module blocked again | PASS |
| Set `max_users_override` to current count, user creation returns 403 `USER_LIMIT_REACHED` | PASS |
| Remove override, `can_create_users` returns true | PASS |

---

## Related Documentation

- `/Users/cheickcisse/Projects/odoo/MEMBERSHIP_MODULE_ACCESS_PLAN.md` — the architecture plan for the membership/module-access system
- `/Users/cheickcisse/Projects/odoo/MULTI_TENANT_SAAS_PLAN.md` — the broader multi-tenant SaaS architecture
- `/Users/cheickcisse/Projects/control-plane/README.md` — Control Plane-specific docs

---

## How `saas-net` Works

`saas-net` is just a Docker network. Nothing more.

Docker Compose creates an isolated network for each project by default (`odoo_default`, `control-plane_default`). Containers in different Compose stacks **cannot talk to each other** unless they share a network. That's the problem `saas-net` solves.

```
Without saas-net:                      With saas-net:

┌─ odoo_default ──────────┐            ┌─ odoo_default ──────────┐
│  odoo-db-1  odoo-odoo-1 │            │  odoo-db-1  odoo-odoo-1 │──┐
└─────────────────────────┘            └─────────────────────────┘  │
                                                                     ├── saas-net
┌─ cp_default ────────────┐            ┌─ cp_default ────────────┐  │  (shared)
│  cp-db-1    cp-app-1    │            │  cp-db-1    cp-app-1    │──┘
└─────────────────────────┘            └─────────────────────────┘

  Cannot reach each other.               odoo-odoo-1 can call
                                         control-plane-app-1:8000
```

The Odoo container calls `http://control-plane-app-1:8000/internal/tenants/main-company/info` over this shared network to check plan limits. Without `saas-net`, that HTTP call would fail with "connection refused."

**Created once, persists across restarts:**
```bash
docker network create saas-net
```

Both `docker-compose.yml` files reference it as `external: true`, meaning Docker won't create or destroy it — it must already exist. If you see an error like `network saas-net declared as external, but could not be found`, run the create command above.

---

## Completion Checklist

### Part 1: Control Plane (`/Users/cheickcisse/Projects/control-plane/`)

| Task | Status | Files |
|------|--------|-------|
| Project scaffolding (FastAPI, Dockerfile, docker-compose, .env) | DONE | `main.py`, `config.py`, `database.py`, `dependencies.py`, `Dockerfile`, `docker-compose.yml` |
| Plan model + schema + CRUD router + service | DONE | `models/plan.py`, `schemas/plan.py`, `routers/admin_plans.py`, `services/plan_service.py` |
| Tenant model + schema + CRUD router + service | DONE | `models/tenant.py`, `schemas/tenant.py`, `routers/admin_tenants.py`, `services/tenant_service.py` |
| PlanChange audit model + schema | DONE | `models/plan_change.py`, `schemas/plan_change.py` |
| Internal tenant info endpoint (`GET /internal/tenants/{slug}/info`) | DONE | `routers/internal.py` |
| Effective limits computation (plan + overrides + extra_modules) | DONE | `services/tenant_service.py` (`build_tenant_info`, `_compute_effective_limits`) |
| Cache invalidation push to Odoo containers | DONE | `services/tenant_service.py` (`push_cache_invalidation`) |
| Plan change with audit log + invalidation | DONE | `routers/admin_tenants.py` (PUT `/{id}/plan`) |
| Suspend / activate tenant endpoints | DONE | `routers/admin_tenants.py` |
| Alembic migration: create tables | DONE | `migrations/versions/001_create_tables.py` |
| Alembic migration: seed 3 default plans | DONE | `migrations/versions/002_seed_plans.py` |
| Health check endpoint | DONE | `main.py` (`GET /health`) |
| Tests (plans, tenants, internal info) | DONE | `tests/test_plans.py`, `tests/test_tenants.py`, `tests/test_internal.py` |

### Part 2: Subscription Enforcer (`/Users/cheickcisse/Projects/odoo/addons/base_api/`)

| Task | Status | Files |
|------|--------|-------|
| SubscriptionEnforcer class (singleton, cached, check methods) | DONE | `services/subscription_enforcer.py` |
| Module resolver (model name → module key) | DONE | `services/module_resolver.py` |
| `_get_enforcer()` helper on controller | DONE | `controllers/simple_api.py` |
| `_enforce_subscription()` helper | DONE | `controllers/simple_api.py` |
| `_enforce_module_access()` helper | DONE | `controllers/simple_api.py` |
| Subscription check wired into all authenticated endpoints | DONE | `controllers/simple_api.py` (44+ endpoints) |
| Module access check wired into model-level endpoints | DONE | `controllers/simple_api.py` |
| User limit check in `_create_user_with_groups()` | DONE | `controllers/simple_api.py` |
| `_get_module_access()` enhanced with `in_plan` flag | DONE | `controllers/simple_api.py` |
| `GET /api/v2/auth/me` extended with plan info | DONE | `controllers/simple_api.py` |
| `POST /api/v2/internal/invalidate-cache` endpoint | DONE | `controllers/simple_api.py` |

### Part 3: Integration

| Task | Status | Notes |
|------|--------|-------|
| Create shared Docker network (`saas-net`) | DONE | `docker network create saas-net` — see "How `saas-net` Works" above |
| Add `saas-net` to control-plane docker-compose | DONE | `control-plane/docker-compose.yml` updated |
| Add `saas-net` + env vars to odoo docker-compose | DONE | `odoo/docker-compose.yml` updated with TENANT_ID, CONTROL_PLANE_URL, CONTROL_PLANE_TOKEN |
| Register existing Odoo instance as Tenant #1 | DONE | Tenant `main-company` on Mid plan, 16/25 users |
| Verify enforcement works end-to-end | DONE | All 10 test scenarios passed (2026-04-04) |

### Part 4: API Metering / Usage Tracking

| Task | Status | Files |
|------|--------|-------|
| ApiUsageMonthly model (composite PK: tenant_id + year_month) | DONE | `models/api_usage.py` |
| Alembic migration: create `api_usage_monthly` table | DONE | `migrations/versions/003_api_usage_monthly.py` |
| Usage Pydantic schemas (increment, batch, response, history) | DONE | `schemas/usage.py` |
| `PUT /internal/tenants/{slug}/usage/increment` endpoint | DONE | `routers/internal.py` |
| `POST /internal/usage/batch` endpoint | DONE | `routers/internal.py` |
| `build_tenant_info()` returns real usage from DB | DONE | `services/tenant_service.py` |
| `GET /admin/tenants/{id}/usage` (current month) | DONE | `routers/admin_tenants.py` |
| `GET /admin/tenants/{id}/usage/history` (last N months) | DONE | `routers/admin_tenants.py` |
| Usage tests (8 tests) | DONE | `tests/test_usage.py` |
| ApiCallLogger singleton (buffer + flush, 30s/50 calls) | DONE | `odoo/addons/base_api/services/api_call_logger.py` |
| Logger wired into all response helpers (timing + logging) | DONE | `odoo/addons/base_api/controllers/simple_api.py` |
| `check_api_quota()` wired into enforcement chain | DONE | `odoo/addons/base_api/controllers/simple_api.py` |

### Part 5: Tenant Provisioning Pipeline

| Task | Status | Files |
|------|--------|-------|
| Jinja2 tenant docker-compose template | DONE | `templates/docker-compose.tenant.yml.j2` |
| Provisioning config (TENANTS_BASE_DIR, ODOO_IMAGE, etc.) | DONE | `app/config.py` |
| Plan resources & modules mapping | DONE | `app/services/plan_resources.py` |
| Provisioning service (9-step pipeline) | DONE | `app/services/provisioning_service.py` |
| Deprovisioning (suspend → stop containers) | DONE | `app/services/provisioning_service.py` |
| Destroy (remove containers + volumes + directory) | DONE | `app/services/provisioning_service.py` |
| Restart (activate → `docker compose up -d`) | DONE | `app/services/provisioning_service.py` |
| ProvisioningJob model | DONE | `app/models/provisioning_job.py` |
| Alembic migration: create `provisioning_jobs` table | DONE | `migrations/versions/004_provisioning_jobs.py` |
| `POST /admin/tenants/{id}/provision` (async background) | DONE | `routers/admin_tenants.py` |
| `GET /admin/tenants/{id}/provision/status` | DONE | `routers/admin_tenants.py` |
| `POST /admin/tenants/{id}/destroy` | DONE | `routers/admin_tenants.py` |
| Suspend wired to `deprovision_tenant()` | DONE | `routers/admin_tenants.py` |
| Activate wired to `restart_tenant()` | DONE | `routers/admin_tenants.py` |
| Docker Compose: socket mount, templates, tenants-data volume | DONE | `docker-compose.yml` |
| `jinja2` added to requirements.txt | DONE | `requirements.txt` |
| Provisioning tests (16 tests: unit + integration) | DONE | `tests/test_provisioning.py` |

### Part 6: Traefik Routing / Subdomain Management

| Task | Status | Files |
|------|--------|-------|
| Traefik static config (entrypoints, Docker provider, dashboard) | DONE | `traefik/traefik.yml` |
| Dynamic middlewares (security headers, rate limiting) | DONE | `traefik/dynamic/middlewares.yml` |
| Traefik service in CP docker-compose | DONE | `docker-compose.yml` |
| Docker labels on CP app (admin.localhost, api.localhost) | DONE | `docker-compose.yml` |
| Docker labels on tenant template (slug.domain) | DONE | `templates/docker-compose.tenant.yml.j2` |
| TLS support (conditional labels when domain != localhost) | DONE | `templates/docker-compose.tenant.yml.j2` |
| `BASE_DOMAIN` config setting | DONE | `app/config.py` |
| Template renders with `base_domain` + `tls_enabled` | DONE | `app/services/provisioning_service.py` |
| Traefik label tests (2 tests) | DONE | `tests/test_provisioning.py` |
| Traefik certs volume | DONE | `docker-compose.yml` |

### Future Phases

| Phase | Status | What it is |
|-------|--------|------------|
| Admin Dashboard (Control Plane UI) | **DONE** | React app at `control-plane/admin-ui/`. Management console for platform operators |
| Client-Facing Web Frontend | **DONE** | React app at `yiri-streamline-flow/`. Plan integration complete |
| Client-Facing Mobile Frontend | **DONE** | React PWA at `feere-mobile/`. Plan integration complete |
| API metering / usage tracking | **DONE** | CP: `api_usage_monthly` table + endpoints. Odoo: `ApiCallLogger` service + quota enforcement |
| Tenant provisioning pipeline | **DONE** | CP: provisioning service, Jinja2 template, job tracking, suspend/activate/destroy |
| Stripe billing integration | NOT STARTED | Automate payments — Stripe Checkout, webhooks, grace period enforcement |
| Traefik routing / subdomain management | **DONE** | Traefik reverse proxy with auto-discovery, subdomain routing, TLS-ready |

---

### Frontend Plan Integration Tasks

The backend now returns `in_plan` and `plan` info in `GET /api/v2/auth/me`. Both frontends need updates to use this data.

#### Web Frontend (`/Users/cheickcisse/Projects/yiri-streamline-flow/`)

The web app already uses `module_access` from `/auth/me` with `ModuleGuard`, sidebar filtering, and `canAccessModule()`. Module hiding already works because the backend folds `in_plan` into `accessible` (if module is not in plan, `accessible` is `false`). What's missing is plan-aware UX.

| # | Task | Detail | Files to change |
|---|------|--------|-----------------|
| W1 | **Add `in_plan` to ModuleAccessEntry type** | The backend now returns `{accessible, in_plan, label, model}` per module. Add `in_plan?: boolean` to the TypeScript interface | `src/data/auth/interfaces.ts` |
| W2 | **Add `plan` to AuthMeUser type** | The backend now returns a `plan` block: `{slug, name, max_users, current_users, can_create_users, allowed_modules}`. Add this to the user type | `src/data/auth/interfaces.ts` |
| W3 | **Store `plan` in AuthContext** | Expose `plan` alongside existing `moduleAccess` in the auth context so any component can read it | `src/contexts/AuthContext.tsx` |
| W4 | **Show "Upgrade" instead of "Access Denied" for plan-gated modules** | When `in_plan === false`, `ModuleGuard` should show an upgrade prompt ("This module requires the Mid plan") instead of the generic "Access Denied" message. When `accessible === false` but `in_plan === true`, keep the current "Access Denied" (it's a permissions issue, not a plan issue) | `src/components/ModuleGuard.tsx` |
| W5 | **Show user count in Settings/User Management** | Display "12 / 25 users" with a progress indicator. When `can_create_users` is `false`, disable the "Add User" button and show "User limit reached — upgrade your plan" | Settings or User Management page |
| W6 | **Show current plan name in Settings** | Display the tenant's plan name (e.g., "Mid Tier") in account settings, with a link to upgrade | Settings page |
| W7 | **Update mock data for dev/testing** | Add `in_plan` field to mock module_access and add mock `plan` block so dev mode reflects the new response shape | `src/data/auth/mock/auth.ts` |

#### Mobile Frontend (`/Users/cheickcisse/ClaudeWorkspace/feere-mobile/`)

The mobile app does NOT use `module_access` at all. It uses a local `isPremium` boolean in localStorage that is disconnected from the backend. This needs to be replaced with real plan data from the API.

| # | Task | Detail | Files to change |
|---|------|--------|-----------------|
| M1 | **Extract `module_access` from `/auth/me` response** | The `/auth/me` response already includes `module_access` with `{accessible, in_plan, label, model}` per module. Add it to `OdooRawUser` type and extract it in `mapRawUser()` | `src/core/api/odoo-types.ts`, `src/core/services/auth.ts` |
| M2 | **Extract `plan` from `/auth/me` response** | Same — add `plan` block to the user model | `src/core/models/user.ts`, `src/core/services/auth.ts` |
| M3 | **Store module_access and plan in app state** | Add `moduleAccess` and `plan` to the Zustand store alongside `currentUser` | `src/store/app-store.ts` |
| M4 | **Replace `isPremium` boolean with plan-based checks** | Currently `premiumService.hasAccess(feature)` returns a single boolean from localStorage. Replace this with checks against `module_access[moduleKey].in_plan`. Map premium features to module keys: `debtManagement` → `debt`, `teamManagement` → `hr`, `advancedAnalytics` → check plan slug | `src/core/services/premium.ts` |
| M5 | **Add module guards to feature pages** | Pages like Debt, Team, Inventory currently have no module-based access checks. Add guards that check `module_access[key].accessible` before rendering, show upgrade prompt if `in_plan === false` | Feature pages: `DebtPage.tsx`, `TeamPage.tsx`, `InventoryDetailPage.tsx`, etc. |
| M6 | **Show plan info in Settings** | Display current plan name and user count (from `plan` block) in the settings/profile screen | Settings page |
| M7 | **Update mock/demo mode** | Add `module_access` and `plan` to mock user data so demo mode reflects the new structure | `src/core/repositories/mock/` |
