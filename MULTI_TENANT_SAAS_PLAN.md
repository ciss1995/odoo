# Multi-Tenant SaaS Platform — Architecture & Implementation Plan

> **Status:** Draft  
> **Date:** 2026-03-23  
> **Authors:** Engineering Team  
> **Stakeholders:** Product Management, Engineering Leads, Finance

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State](#2-current-state)
3. [Target Architecture Overview](#3-target-architecture-overview)
4. [Multi-Tenant Container Strategy](#4-multi-tenant-container-strategy)
5. [Subscription Plans & Enforcement](#5-subscription-plans--enforcement)
6. [Payment Verification & Access Control](#6-payment-verification--access-control)
7. [Frontend UI & Client Interaction Model](#7-frontend-ui--client-interaction-model)
8. [Company Onboarding & User Creation](#8-company-onboarding--user-creation)
9. [Domain & Routing Strategy](#9-domain--routing-strategy) *(incl. Nginx vs Traefik)*
10. [API Monitoring, Metering & Analytics](#10-api-monitoring-metering--analytics)
11. [Financial Tracking & Company Metrics](#11-financial-tracking--company-metrics)
12. [Platform Admin Dashboard](#12-platform-admin-dashboard)
13. [Infrastructure & DevOps](#13-infrastructure--devops)
14. [Security Considerations](#14-security-considerations)
15. [Data Architecture](#15-data-architecture)
16. [Migration Path](#16-migration-path)
17. [Cost Estimation Model](#17-cost-estimation-model)
18. [Risk Register](#18-risk-register)
19. [Implementation Phases & Timeline](#19-implementation-phases--timeline)
20. [Open Decisions](#20-open-decisions)

---

## 1. Executive Summary

We are transforming our single-instance Odoo 19 deployment into a multi-tenant SaaS platform. Each customer company receives an isolated Docker container stack (Odoo + PostgreSQL) with its own database. Access is gated by a three-tier subscription model (Basic, Mid, Full) that controls which Odoo modules are available and how many users each company can create. A central **Platform Control Plane** manages tenant provisioning, payment verification, access enforcement, API metering, and operational dashboards.

### Key Goals

- **Tenant isolation:** One Docker stack per company, each with a dedicated PostgreSQL database — no data leakage between companies.
- **Revenue control:** Enforce subscription tiers — block access for non-paying tenants, cap users and modules per plan.
- **Observability:** Track API calls, resource consumption, and business metrics per tenant for billing accuracy and operational intelligence.
- **Unified entry point:** Companies authenticate through a single domain, with routing handled transparently behind the scenes.

### Interaction Model

Clients (company end-users) **never interact with the Odoo web interface directly**. All client-facing interaction flows through two layers:

1. **Frontend UI** — A separate web application (React/Next.js) that serves as the user-facing interface. This is the only thing end-users see and interact with.
2. **`base_api`** — The REST API backend (`/api/v2/*`) running inside each tenant's Odoo container. The Frontend UI communicates exclusively with `base_api`. Odoo acts purely as a headless backend.

```
End Users ──▶ Frontend UI (React/Next.js) ──▶ base_api (/api/v2/*) ──▶ Odoo ORM/DB
                                                                        (headless)
```

The Odoo web UI (port 8069) is **not exposed** to tenants. It is only accessible to platform administrators for emergency debugging.

---

## 2. Current State

### What Exists Today

| Component | Detail |
|-----------|--------|
| **Odoo version** | 19.0 (full source tree) |
| **Custom addons** | `base_api` (REST API v2 facade), `api_doc` (API documentation UI) |
| **Docker setup** | Single `docker-compose.yml`: one `postgres:18` container + one Odoo container |
| **Database** | Single database (`odoo19_db`) |
| **API layer** | `base_api` provides `/api/v2/*` endpoints: auth (API key + session), CRUD on any model, analytics dashboards |
| **Module presets** | Three tiers already defined in `.env.example`: Minimal, Standard, Full |
| **Auth methods** | API key (`res.users.apikeys`) and session token (`api.session` model with hashed tokens) |
| **Config** | `docker/odoo.conf` (container), `production-updated.conf` (local/nginx) |

### Current docker-compose.yml (Simplified)

```
services:
  db:       postgres:18  → port 5433, volume odoo-db-data
  odoo:     build .      → ports 8069/8072, bind-mounts ./addons
```

### What Needs to Change

- Single container → **N containers** (one per company)
- Single database → **N databases** (isolated per company)
- No billing logic → **Subscription plan enforcement**
- No usage tracking → **API metering and consumption analytics**
- No admin portal → **Central control plane with dashboards**

---

## 3. Target Architecture Overview

```
                          ┌─────────────────────────────────┐
                          │          End Users               │
                          │  (Company employees / clients)   │
                          └──────────────┬──────────────────┘
                                         │
                          ┌──────────────▼──────────────────┐
                          │      Frontend UI (React/Next)    │
                          │   app.platform.example.com       │
                          │   - Login / company selection    │
                          │   - Module dashboards            │
                          │   - User management              │
                          │   - All client-facing screens    │
                          └──────────────┬──────────────────┘
                                         │ API calls only
                          ┌──────────────▼──────────────────┐
                          │        DNS / CDN                 │
                          │   *.platform.example.com         │
                          └──────────────┬──────────────────┘
                                         │
                          ┌──────────────▼──────────────────┐
                          │  Reverse Proxy (Traefik/Nginx)   │
                          │   + SSL Termination              │
                          │   + Subdomain Routing            │
                          │   + Rate Limiting                │
                          └──────────────┬──────────────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 │                       │                       │
    ┌────────────▼───┐     ┌────────────▼───┐     ┌────────────▼───┐
    │  Control Plane │     │  Tenant A      │     │  Tenant B      │
    │  (FastAPI)     │     │  ┌───────────┐ │     │  ┌───────────┐ │
    │                │     │  │  Odoo 19  │ │     │  │  Odoo 19  │ │
    │  - Tenant Mgmt │     │  │(headless) │ │     │  │(headless) │ │
    │  - Billing     │     │  │ + base_api│ │     │  │ + base_api│ │
    │  - Metering    │     │  └─────┬─────┘ │     │  └─────┬─────┘ │
    │  - Dashboards  │     │  ┌─────▼─────┐ │     │  ┌─────▼─────┐ │
    │  - Provisioning│     │  │ Postgres  │ │     │  │ Postgres  │ │
    │                │     │  │ (tenant_a)│ │     │  │ (tenant_b)│ │
    └────────┬───────┘     │  └───────────┘ │     │  └───────────┘ │
             │             └───────────────┘     └───────────────┘
    ┌────────▼───────┐
    │  Control DB    │      Odoo web UI (port 8069) is NOT exposed
    │  (PostgreSQL)  │      to end users. Only /api/v2/* routes
    │  - tenants     │      are proxied through to base_api.
    │  - plans       │
    │  - payments    │
    │  - api_logs    │
    │  - metrics     │
    └────────────────┘
```

### Core Components

| Component | Role |
|-----------|------|
| **Frontend UI** | Client-facing web application (React/Next.js). The **only** interface end-users interact with. Communicates exclusively with `base_api`. Deployed as a static SPA or SSR app behind the reverse proxy. |
| **Reverse Proxy** | SSL termination, subdomain-based routing to tenant containers, rate limiting. See [Section 7.2](#72-nginx-vs-traefik-comparison) for technology choice. |
| **Control Plane API** | Standalone service (FastAPI/Python) managing tenants, subscriptions, payments, metering |
| **Control Database** | Centralized PostgreSQL storing all platform-level data (tenants, plans, billing, API logs) |
| **Tenant Stacks** | Per-company Docker Compose stacks (Odoo headless + PostgreSQL), dynamically provisioned. Odoo web UI is disabled/blocked; only `base_api` endpoints are reachable. |
| **Subscription Enforcer** | Middleware in `base_api` that checks plan limits on every API call (calls Control Plane) |

---

## 4. Multi-Tenant Container Strategy

### 4.1 Isolation Model: Container-Per-Tenant

Each company gets its own Docker Compose stack:

```
tenant-acme/
├── docker-compose.yml      # Generated from template
├── .env                    # Tenant-specific config
└── data/
    ├── postgres/           # Persistent DB volume
    └── filestore/          # Odoo filestore
```

**Generated `docker-compose.yml` template for each tenant:**

```yaml
services:
  db:
    image: postgres:18
    environment:
      POSTGRES_DB: ${TENANT_DB}
      POSTGRES_USER: ${TENANT_DB_USER}
      POSTGRES_PASSWORD: ${TENANT_DB_PASSWORD}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data/pgdata
    networks:
      - tenant-internal

  odoo:
    image: ${ODOO_IMAGE}            # Pre-built Odoo 19 image from registry
    depends_on:
      db:
        condition: service_healthy
    environment:
      HOST: db
      USER: ${TENANT_DB_USER}
      PASSWORD: ${TENANT_DB_PASSWORD}
      TENANT_ID: ${TENANT_ID}       # Injected for enforcement middleware
      CONTROL_PLANE_URL: ${CONTROL_PLANE_URL}
      CONTROL_PLANE_TOKEN: ${CONTROL_PLANE_INTERNAL_TOKEN}
    volumes:
      - ./data/filestore:/var/lib/odoo/filestore
      - shared-addons:/opt/odoo/addons:ro    # Read-only shared addons
    networks:
      - tenant-internal
      - platform                             # For control plane communication
    labels:
      - "tenant.id=${TENANT_ID}"
      - "tenant.plan=${TENANT_PLAN}"

networks:
  tenant-internal:
    internal: true
  platform:
    external: true

volumes:
  shared-addons:
    external: true
```

### 4.2 Provisioning Flow

```
Company signs up → Payment confirmed → Control Plane triggers:
  1. Generate tenant ID (e.g., "acme-corp")
  2. Generate unique DB credentials
  3. Create tenant directory from template
  4. docker compose up -d (in tenant directory, with Traefik labels)
  5. Initialize Odoo DB with plan-appropriate modules (-i flag)
  6. Create admin user for the company
  7. Register tenant in Control DB (hostname, ports, plan, status)
  8. Traefik auto-discovers the new container via Docker labels
     (no manual proxy config needed)
  9. Return onboarding credentials to company
```

### 4.3 Networking

- Each tenant stack has an **internal network** (Odoo ↔ DB only).
- All Odoo containers also join a **shared `platform` network** for Control Plane communication.
- Only the Reverse Proxy exposes ports 80/443 to the internet.
- Tenant Odoo containers do **not** bind host ports — traffic flows exclusively through Nginx.

### 4.4 Resource Limits

Apply Docker resource constraints per plan:

| Resource | Basic | Mid | Full |
|----------|-------|-----|------|
| CPU limit | 1 core | 2 cores | 4 cores |
| Memory limit | 1 GB | 2 GB | 4 GB |
| Odoo workers | 2 | 4 | 8 |
| DB connections (`db_maxconn`) | 16 | 32 | 64 |

Implemented via `deploy.resources.limits` in docker-compose or Docker run flags.

---

## 5. Subscription Plans & Enforcement

### 5.1 Plan Definitions

| Feature | Basic | Mid | Full |
|---------|-------|-----|------|
| **Monthly Price** | $49/mo | $149/mo | $399/mo |
| **Max Users** | 5 | 25 | Unlimited |
| **Modules Included** | Contacts, CRM | Contacts, CRM, Sales, HR, Purchase | All modules |
| **API Calls/month** | 10,000 | 100,000 | Unlimited |
| **Storage** | 5 GB | 25 GB | 100 GB |
| **Analytics** | Basic dashboard | Full analytics | Full analytics + export |
| **Support** | Email (48h) | Email (24h) + Chat | Priority (4h) + Phone |
| **Custom Domain** | No (subdomain only) | Yes | Yes |
| **Data Export** | CSV only | CSV + Excel | CSV + Excel + API bulk |

### 5.2 Module-to-Plan Mapping

This mapping determines which Odoo modules get installed per plan and which API endpoints are accessible.

```python
PLAN_MODULES = {
    "basic": {
        "install": "base,base_api,contacts,crm,mail,web",
        "api_models": [
            "res.partner", "crm.lead", "res.users",
            "calendar.event",
        ],
    },
    "mid": {
        "install": (
            "base,base_api,api_doc,contacts,crm,sale,sale_management,"
            "hr,purchase,account,mail,calendar,web"
        ),
        "api_models": [
            "res.partner", "crm.lead", "res.users",
            "sale.order", "sale.order.line",
            "hr.employee", "hr.department",
            "purchase.order", "purchase.order.line",
            "account.move", "account.move.line",
            "product.template", "product.product",
            "calendar.event",
        ],
    },
    "full": {
        "install": (
            # Full module list from .env.example ODOO_INIT_MODULES
            "base,base_api,api_doc,account,sale,sale_management,crm,"
            "hr,purchase,stock,project,calendar,contacts,..."
        ),
        "api_models": "__all__",  # No restrictions
    },
}
```

### 5.3 Enforcement Points

Enforcement happens at **two levels**: the `base_api` middleware (runtime) and the Control Plane (provisioning).

#### A. At Provisioning Time

When a tenant is created, only the modules allowed by their plan are installed:

```
odoo-bin -d tenant_db -i $PLAN_MODULES[$plan]["install"] --stop-after-init
```

Modules not installed simply do not exist in that database — the models are unavailable.

#### B. At Runtime — `base_api` Middleware

Every API request to `base_api` passes through a new enforcement layer that:

1. **Checks subscription status** — Is the tenant's subscription active and paid?
2. **Checks user count** — Has the tenant exceeded their user limit?
3. **Checks model access** — Is the requested model allowed by their plan?
4. **Checks API quota** — Has the tenant exceeded their monthly API call limit?

```python
# Pseudo-code for enforcement middleware in base_api

class SubscriptionEnforcer:
    """Caches plan info from Control Plane, enforced on every API call."""

    def __init__(self, tenant_id, control_plane_url, control_plane_token):
        self.tenant_id = tenant_id
        self.cp_url = control_plane_url
        self.cp_token = control_plane_token
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes

    def check_access(self):
        """Called before every API endpoint. Returns (allowed, error_response)."""
        tenant_info = self._get_tenant_info()

        # 1. Subscription active?
        if tenant_info["status"] != "active":
            return False, {
                "error": "SUBSCRIPTION_INACTIVE",
                "message": "Your subscription is inactive. Please renew.",
            }

        # 2. Payment current?
        if tenant_info["payment_overdue"]:
            grace_days = tenant_info.get("grace_days_remaining", 0)
            if grace_days <= 0:
                return False, {
                    "error": "PAYMENT_OVERDUE",
                    "message": "Payment overdue. Access suspended.",
                }

        # 3. API quota
        if tenant_info["api_calls_used"] >= tenant_info["api_calls_limit"]:
            return False, {
                "error": "API_QUOTA_EXCEEDED",
                "message": "Monthly API call limit reached.",
            }

        return True, None

    def check_user_creation(self, current_user_count):
        """Called before creating a new user."""
        tenant_info = self._get_tenant_info()
        max_users = tenant_info["max_users"]

        if max_users != -1 and current_user_count >= max_users:
            return False, {
                "error": "USER_LIMIT_REACHED",
                "message": f"Plan allows {max_users} users. "
                           f"Current: {current_user_count}. Upgrade to add more.",
            }
        return True, None

    def check_model_allowed(self, model_name):
        """Called before any model-level API operation."""
        tenant_info = self._get_tenant_info()
        allowed = tenant_info["allowed_models"]

        if allowed == "__all__":
            return True, None
        if model_name not in allowed:
            return False, {
                "error": "MODULE_NOT_IN_PLAN",
                "message": f"Model '{model_name}' is not included in your plan. "
                           f"Upgrade to access this feature.",
            }
        return True, None

    def _get_tenant_info(self):
        """Fetch and cache tenant info from Control Plane."""
        now = time.time()
        if self._cache and now - self._cache.get("_ts", 0) < self._cache_ttl:
            return self._cache

        resp = requests.get(
            f"{self.cp_url}/internal/tenants/{self.tenant_id}/info",
            headers={"Authorization": f"Bearer {self.cp_token}"},
            timeout=5,
        )
        self._cache = resp.json()
        self._cache["_ts"] = now
        return self._cache
```

#### C. At User Creation

The `_create_user_with_groups` method in `simple_api.py` is extended to call `check_user_creation` before proceeding:

```python
def _create_user_with_groups(self, data):
    # Count current active users
    current_count = request.env['res.users'].sudo().search_count(
        [('active', '=', True), ('share', '=', False)]
    )

    allowed, error = self.enforcer.check_user_creation(current_count)
    if not allowed:
        return self._error_response(error["message"], 403, error["error"])

    # ... existing user creation logic ...
```

### 5.4 Grace Period Policy

When a payment fails:

| Day | Action |
|-----|--------|
| 0 | Payment fails — email notification sent |
| 1-3 | **Warning period** — Full access, daily email reminders |
| 4-7 | **Degraded access** — Read-only mode (no creates/updates/deletes) |
| 8-14 | **Suspended** — All API access blocked, only billing endpoints work |
| 15+ | **Data retention** — Container stopped, data retained for 90 days |
| 90+ | **Data deletion** — Backup created, tenant data purged |

---

## 6. Payment Verification & Access Control

### 6.1 Payment Integration

We integrate with **Stripe** as the primary payment processor (alternatives: Paddle, LemonSqueezy for simpler tax handling).

**Control Plane Payment Flow:**

```
1. Company selects plan on signup page
2. Stripe Checkout Session created → customer redirected to Stripe
3. Stripe webhook (checkout.session.completed) → Control Plane
4. Control Plane creates tenant record (status: provisioning)
5. Provisioning pipeline runs (Docker stack, DB init, Nginx config)
6. Tenant status → active
7. Onboarding email with credentials sent

Monthly billing:
1. Stripe sends invoice.payment_succeeded → Control Plane
2. Control Plane updates tenant.payment_status = "current"
3. Stripe sends invoice.payment_failed → Control Plane  
4. Control Plane starts grace period countdown
```

### 6.2 Control Plane — Payment Data Model

```sql
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            VARCHAR(63) UNIQUE NOT NULL,   -- subdomain identifier
    company_name    VARCHAR(255) NOT NULL,
    admin_email     VARCHAR(255) NOT NULL,
    plan            VARCHAR(20) NOT NULL CHECK (plan IN ('basic', 'mid', 'full')),
    status          VARCHAR(20) NOT NULL DEFAULT 'provisioning'
                    CHECK (status IN ('provisioning', 'active', 'suspended',
                                      'grace_period', 'cancelled', 'deleted')),
    stripe_customer_id      VARCHAR(255),
    stripe_subscription_id  VARCHAR(255),
    payment_status  VARCHAR(20) DEFAULT 'pending'
                    CHECK (payment_status IN ('current', 'pending', 'overdue', 'cancelled')),
    grace_period_end        TIMESTAMPTZ,
    container_host  VARCHAR(255),         -- Docker host if multi-host
    odoo_port       INTEGER,              -- Internal port mapping
    db_name         VARCHAR(63),
    db_user         VARCHAR(63),
    max_users       INTEGER NOT NULL,
    max_api_calls   INTEGER NOT NULL,     -- per month, -1 = unlimited
    storage_limit_gb INTEGER NOT NULL,
    custom_domain   VARCHAR(255),         -- NULL for basic plan
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE payments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID REFERENCES tenants(id),
    stripe_invoice_id   VARCHAR(255),
    amount_cents        INTEGER NOT NULL,
    currency            VARCHAR(3) DEFAULT 'USD',
    status              VARCHAR(20) NOT NULL,   -- succeeded, failed, refunded
    period_start        DATE,
    period_end          DATE,
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE plan_changes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES tenants(id),
    old_plan    VARCHAR(20),
    new_plan    VARCHAR(20),
    reason      VARCHAR(255),
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    changed_by  VARCHAR(255)
);
```

### 6.3 Access Verification Flow (Per Request)

```
Frontend UI → Reverse Proxy → Tenant Odoo Container → base_api controller
(React app)   (Traefik/Nginx)  (headless, port 8069       │
               only proxies     not exposed to users)      ▼
               /api/v2/* routes                    ┌─────────────────┐
                                                   │ Enforcement      │
                                                   │ Middleware        │
                                                   │                  │
                                                   │ 1. Is sub active?│
                                                   │ 2. Payment OK?   │
                                                   │ 3. API quota OK? │
                                                   │ 4. Model allowed?│
                                                   └────────┬─────────┘
                                                            │
                                               ┌────────────▼────────────┐
                                               │   Control Plane Cache   │
                                               │  (TTL: 5 min in-memory) │
                                               │  Fallback: HTTP to CP   │
                                               └─────────────────────────┘
```

The enforcement check is cached in-memory within each Odoo container with a 5-minute TTL to avoid latency on every request. The cache is invalidated immediately on plan changes via a webhook from the Control Plane.

---

## 7. Frontend UI & Client Interaction Model

### 7.1 Architecture Principle: Odoo Is Headless

End-users **never** see or touch the Odoo web interface. Odoo serves purely as a headless backend — its ORM, business logic, and database are consumed exclusively through `base_api` endpoints. This gives us:

- **Full control over UX** — the client experience is decoupled from Odoo's UI, so we can build a modern, branded interface.
- **Security** — the Odoo admin interface, XML-RPC, and JSON-RPC are not exposed. Attack surface is limited to `/api/v2/*`.
- **Consistency across tenants** — every company gets the same frontend regardless of which Odoo modules are installed behind the scenes.

### 7.2 System Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        END USERS                                │
│          (Company employees using the platform)                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼─────────────────────────────────────┐
│                    FRONTEND UI (React / Next.js)                │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  Login   │  │  CRM     │  │  Sales   │  │  Settings /    │  │
│  │  Page    │  │ Dashboard│  │ Dashboard│  │  User Mgmt     │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
│                                                                 │
│  Renders UI based on user's plan (modules hidden if not in plan)│
│  Calls base_api for ALL data and actions                        │
│  Hosted at: app.platform.example.com (SPA/SSR)                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ /api/v2/* calls
┌───────────────────────────▼─────────────────────────────────────┐
│                    REVERSE PROXY (Traefik / Nginx)              │
│  Routes to correct tenant Odoo container based on subdomain     │
│  Blocks all non-API routes (no /web, /xmlrpc, /jsonrpc)         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                    TENANT ODOO CONTAINER (headless)              │
│                                                                 │
│  base_api controller (/api/v2/*)  ←── ONLY accessible route    │
│  Odoo ORM / business logic                                      │
│  PostgreSQL (tenant-isolated)                                   │
│                                                                 │
│  /web, /xmlrpc, /jsonrpc  ←── BLOCKED at proxy level            │
└─────────────────────────────────────────────────────────────────┘
```

### 7.3 Frontend UI Responsibilities

| Responsibility | Detail |
|----------------|--------|
| **Authentication** | Login form → calls `POST /api/v2/auth/login` → stores session token |
| **Navigation & routing** | Shows/hides modules based on the user's `module_access` from `GET /api/v2/auth/me` |
| **Data display** | Fetches data from `base_api` (partners, orders, leads, etc.) and renders in custom UI components |
| **CRUD operations** | Forms submit to `POST /api/v2/create/<model>`, `PUT /api/v2/update/<model>/<id>`, etc. |
| **Analytics dashboards** | Calls `/api/v2/analytics/*` endpoints and renders charts |
| **User management** | Admin users can create/edit/delete users via the API; the UI enforces plan limits visually (shows "5/5 users — upgrade to add more") |
| **Plan-awareness** | The frontend reads the user's plan and hides features/modules not included. This is a UX layer — the backend enforcer is the real gate. |
| **Billing self-service** | Links to Stripe billing portal for plan changes, payment method updates, invoices |

### 7.4 What the Reverse Proxy Blocks

The reverse proxy **only** forwards requests matching `/api/v2/*` to the tenant's Odoo container. Everything else is rejected:

| Route Pattern | Action |
|---------------|--------|
| `/api/v2/*` | **Proxy** to tenant Odoo container |
| `/web`, `/web/*` | **Block** (403) — Odoo web UI not exposed |
| `/xmlrpc/*` | **Block** (403) — XML-RPC disabled |
| `/jsonrpc` | **Block** (403) — JSON-RPC disabled |
| `/longpolling/*` | **Block** unless explicitly needed by the frontend (WebSocket for live updates) |
| Everything else | **Block** (404) |

### 7.5 Frontend Deployment

The Frontend UI is a separate deployable artifact:

- **Option A (recommended for Phase 1):** Static SPA built with Next.js/React, served by the reverse proxy from `app.platform.example.com`. Single deployment serves all tenants — the app dynamically resolves which tenant API to call based on the user's authenticated session.
- **Option B (Phase 3+):** SSR with Next.js for SEO on marketing pages, with the app portion still being a client-side SPA.

The frontend is **tenant-agnostic** — it does not contain any tenant-specific code. It discovers the tenant context at login and directs all API calls to `<tenant-slug>.platform.example.com/api/v2/*`.

---

## 8. Company Onboarding & User Creation

### 8.1 New Company Signup — Full Flow

```
Step 1: DISCOVERY
  └─▶ Prospect visits platform.example.com (marketing site)
  └─▶ Views plan comparison, clicks "Start Free Trial" or "Subscribe"

Step 2: REGISTRATION
  └─▶ Signup form collects:
      • Company name
      • Admin email
      • Desired subdomain slug (e.g., "acme" → acme.platform.example.com)
      • Selected plan (Basic / Mid / Full)
      • Password for the admin account
  └─▶ Frontend submits to Control Plane: POST /signup

Step 3: PAYMENT
  └─▶ Control Plane creates Stripe Checkout Session
  └─▶ User redirected to Stripe-hosted payment page
  └─▶ On success: Stripe webhook → Control Plane
  └─▶ On trial: Skip payment, set tenant.status = "trial",
      trial_expires_at = NOW() + 14 days

Step 4: PROVISIONING (automated, ~2-5 minutes)
  └─▶ Control Plane provisioning pipeline:
      a. Generate tenant ID, DB credentials, internal auth token
      b. Create tenant directory from template
      c. docker compose up -d (Odoo + PostgreSQL containers)
      d. Wait for PostgreSQL healthcheck
      e. Initialize Odoo database:
         odoo-bin -d $TENANT_DB -i $PLAN_MODULES[$plan] --stop-after-init
      f. Create company admin user in Odoo:
         - login = admin email
         - password = chosen password (hashed)
         - groups = admin groups for the plan
         - auto-generate API key
      g. Update reverse proxy config (add upstream for new tenant)
      h. Reload reverse proxy

Step 5: ACTIVATION
  └─▶ Control Plane sets tenant.status = "active"
  └─▶ Registers subdomain in DNS (if dynamic DNS) or relies on
      wildcard *.platform.example.com

Step 6: ONBOARDING EMAIL
  └─▶ Email sent to admin with:
      • Login URL: app.platform.example.com
      • Company slug: "acme"
      • Credentials (login email + temporary password if generated)
      • API key (for programmatic access)
      • Quick start guide link
      • Support contact

Step 7: FIRST LOGIN
  └─▶ Admin opens app.platform.example.com
  └─▶ Enters company slug + email + password
  └─▶ Frontend calls POST /api/v2/auth/login on
      acme.platform.example.com
  └─▶ Session token returned → admin sees their dashboard
  └─▶ Guided onboarding wizard (optional): set up company info,
      invite first users, explore modules
```

### 8.2 Control Plane — Provisioning Data Model

```sql
CREATE TABLE provisioning_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed',
                                      'failed', 'retrying')),
    step            VARCHAR(50),            -- current step name
    steps_completed JSONB DEFAULT '[]',     -- list of completed steps with timestamps
    error_message   TEXT,
    attempt         INTEGER DEFAULT 1,
    max_attempts    INTEGER DEFAULT 3,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

If provisioning fails at any step, the job is retried up to 3 times. On final failure, the platform admin is alerted and can trigger manual provisioning or investigate.

### 8.3 User Creation Within a Tenant

Users within a company are created by the **company admin** through the Frontend UI, which calls the `base_api` user creation endpoint. The flow enforces plan limits at multiple layers.

**User Creation Flow:**

```
Company Admin (in Frontend UI)
  │
  │  Clicks "Add User" → fills in name, email, role/groups
  │
  ▼
Frontend UI
  │
  │  POST /api/v2/create/res.users
  │  Headers: session-token: <admin_session>
  │  Body: { name, login, email, group_names, ... }
  │
  ▼
base_api controller (_create_user_with_groups)
  │
  │  ┌─────────────────────────────────────────────┐
  │  │ ENFORCEMENT CHECK #1: Subscription active?  │
  │  │ → Calls SubscriptionEnforcer.check_access()  │
  │  │ → If inactive/overdue → 403 SUBSCRIPTION_*   │
  │  └─────────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────┐
  │  │ ENFORCEMENT CHECK #2: User limit reached?   │
  │  │ → Counts active internal users (share=False) │
  │  │ → Compares against plan max_users            │
  │  │ → Basic: 5, Mid: 25, Full: unlimited         │
  │  │ → If at limit → 403 USER_LIMIT_REACHED       │
  │  └─────────────────────────────────────────────┘
  │
  │  ┌─────────────────────────────────────────────┐
  │  │ ENFORCEMENT CHECK #3: Groups allowed?       │
  │  │ → Validates requested groups are within the  │
  │  │   modules allowed by the plan                │
  │  │ → Basic user can't get Sale Manager group    │
  │  │   if Sales module not in plan                │
  │  └─────────────────────────────────────────────┘
  │
  │  All checks pass:
  │  → Create user in Odoo (res.users.create)
  │  → Assign groups
  │  → Auto-generate temporary password
  │  → Auto-generate API key (optional)
  │
  ▼
Response to Frontend
  │  201 Created
  │  { id, name, login, credentials: { temp_password, api_key } }
  │
  ▼
Frontend UI shows credentials
  │  Admin copies/shares credentials with new user
  │  (or system sends welcome email — see below)
```

### 8.4 User Welcome Email (Optional Automation)

When a new user is created, the system can automatically send a welcome email:

```
Subject: Welcome to [Company Name] on [Platform Name]

Hi [User Name],

You've been added to [Company Name]'s workspace.

  Login URL:  app.platform.example.com
  Company:    [company-slug]
  Email:      [user-email]
  Temp Pass:  [temporary-password]

Please change your password on first login.

— The [Platform Name] Team
```

This is triggered by the Control Plane (not Odoo's mail system) to keep email delivery centralized and reliable (via SendGrid, AWS SES, or similar).

### 8.5 User Lifecycle Within a Tenant

| Event | Trigger | Enforcement |
|-------|---------|-------------|
| **Create user** | Admin via Frontend UI | Plan user limit checked. Blocked if at max. |
| **Deactivate user** | Admin toggles user active=False | Frees up a slot against the user limit. |
| **Reactivate user** | Admin toggles user active=True | User limit re-checked — blocked if already at max. |
| **Delete user** | Admin deletes user | Permanently frees the slot. User data retained per Odoo's archive policy. |
| **Change user role** | Admin updates groups | Group must belong to a module included in the plan. |
| **User self-service** | User changes own password, email, profile | Allowed for all users via `PUT /api/v2/users/<id>` (own profile only). |
| **Password reset** | Admin resets another user's password | Admin-only action via `POST /api/v2/users/<id>/reset-password`. |
| **API key generation** | Admin or user generates API key | Allowed for own key or admin-generated. Key count not limited. |

### 8.6 User Count Enforcement — Edge Cases

| Scenario | Behavior |
|----------|----------|
| Company on Basic (5 users), has 5 active users, tries to create 6th | `403 USER_LIMIT_REACHED` — must upgrade or deactivate a user |
| Company downgrades from Mid (25) to Basic (5) but has 15 users | **Existing users are NOT deleted.** Existing users continue to work. New user creation is blocked until count drops below 5. The admin sees a warning in the UI. |
| Company has 5 active + 3 deactivated users on Basic plan | Limit only counts active internal users (`active=True, share=False`). Count = 5, so new creation blocked. Reactivating a deactivated user would also be blocked (would make count = 6). |
| Company is in grace period (payment overdue) | User creation blocked regardless of count. |
| Two admins try to create a user simultaneously, both at count=4 (limit=5) | The enforcement check is done inside a transaction. One succeeds, the other gets `USER_LIMIT_REACHED` on retry. |

### 8.7 Company Admin vs Platform Admin

| Role | Scope | Capabilities |
|------|-------|-------------|
| **Company Admin** | Single tenant | Create/edit/delete users within their company, manage company settings, view company analytics. Uses the **Frontend UI** → **base_api**. |
| **Platform Admin** | All tenants | Provision/suspend/delete tenants, change plans, view cross-tenant metrics, manage billing. Uses the **Admin Dashboard** → **Control Plane API**. |

Company admins cannot see other tenants, access the Control Plane, or modify their own plan/billing (they use the Stripe billing portal for that). Platform admins can impersonate tenant admin users for debugging but this action is audit-logged.

---

## 9. Domain & Routing Strategy

### 9.1 Recommendation: Subdomain Model (Primary) + Custom Domain (Premium)

After evaluating the options, the **recommended approach** is a hybrid:

| Approach | Assigned to | Example |
|----------|-------------|---------|
| **Subdomain (default)** | All plans | `acme.platform.example.com` |
| **Custom domain (add-on)** | Mid & Full plans | `erp.acmecorp.com` |

**Why subdomains as default:**

- Single wildcard SSL certificate (`*.platform.example.com`) — simpler cert management
- Tenant isolation via Host-based routing
- No DNS delegation required from customers
- Central control over all routing
- Consistent branding and trust signals

**Why support custom domains for paid tiers:**

- Enterprise customers expect to use their own domain
- Adds perceived value to higher tiers
- Achievable with Let's Encrypt + automated CNAME verification

### 9.2 Nginx vs Traefik Comparison

This is a critical infrastructure decision. Both are production-proven, but they serve different operational models.

| Criteria | Nginx | Traefik | Winner for Us |
|----------|-------|---------|---------------|
| **Dynamic config (add/remove tenants)** | Requires config file rewrite + `nginx -s reload` on every tenant change. Can use Lua (OpenResty) for dynamic upstreams, but adds complexity. | **Native dynamic discovery.** Reads Docker labels in real-time — add a container with the right labels and Traefik routes to it automatically. Zero restarts. | **Traefik** |
| **Docker integration** | Manual. You manage upstream blocks and config files. Works, but requires a provisioning script to update configs. | **First-class.** Watches the Docker socket, auto-discovers containers. Tenant containers just need labels like `traefik.http.routers.acme.rule=Host(...)`. | **Traefik** |
| **SSL / Let's Encrypt** | Requires separate tooling (certbot, cron renewal). Wildcard certs need DNS challenge setup. Per-domain certs require scripting. | **Built-in ACME.** Automatic Let's Encrypt provisioning and renewal for both wildcard and per-domain certs. Supports HTTP and DNS challenges natively. | **Traefik** |
| **Performance (raw throughput)** | Industry-leading. Handles 100K+ req/s with minimal overhead. Battle-tested at massive scale. | Good for most workloads. Slightly higher overhead than Nginx. More than sufficient for < 1000 tenants. | **Nginx** (marginal) |
| **Rate limiting** | Mature, flexible `limit_req` and `limit_conn` modules. Per-IP, per-variable zones. | Available via middleware plugins. Less granular than Nginx's native modules but adequate. | **Nginx** (marginal) |
| **Learning curve** | Team likely already familiar. Config is declarative but static. | Newer paradigm. Label-based config can be unfamiliar but is simpler once understood. | Depends on team |
| **Observability** | Access logs, basic status page. Needs external tooling for metrics. | Built-in Prometheus metrics endpoint, built-in dashboard UI, tracing support. | **Traefik** |
| **Community & ecosystem** | Massive. Decades of production use. Huge knowledge base. | Smaller but fast-growing. Strong in Docker/Kubernetes ecosystems. | **Nginx** (slightly) |
| **Custom domain support** | Manual: generate server block + certbot for each custom domain. | Automatic: add a router rule + Traefik handles the cert. | **Traefik** |
| **WebSocket support** | Supported, requires explicit config per location. | Supported natively, auto-detected. | **Traefik** (simpler) |

#### Recommendation: **Traefik** for this project

For a multi-tenant platform where containers are created and destroyed dynamically, **Traefik is the stronger choice**:

1. **Zero-touch tenant routing.** When the provisioning pipeline spins up a new tenant container with Docker labels, Traefik automatically picks it up and routes traffic. No config rewrite, no reload, no downtime for other tenants.

2. **Automatic SSL.** Wildcard cert for `*.platform.example.com` via DNS challenge + per-domain certs for custom domains via HTTP challenge — all handled automatically.

3. **Built-in observability.** Prometheus metrics and the Traefik dashboard give us request rates, latencies, and error counts per tenant out of the box — feeding directly into our metering system.

4. **Simpler provisioning pipeline.** The provisioning script doesn't need to touch proxy config files at all. It just starts the container with the right labels and Traefik does the rest.

If the team is deeply invested in Nginx and concerned about Traefik's rate limiting granularity, a hybrid is possible: Traefik as the outer dynamic router + Nginx sidecar per tenant for fine-grained rate limiting. But for Phase 1, Traefik alone is sufficient.

### 9.3 Traefik Configuration (Recommended)

**`docker-compose.yml` for Traefik (platform-level):**

```yaml
services:
  traefik:
    image: traefik:v3
    command:
      - "--api.dashboard=true"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
      - "--certificatesresolvers.letsencrypt.acme.email=admin@platform.example.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.wildcard.acme.dnschallenge.provider=cloudflare"
      - "--metrics.prometheus=true"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks:
      - platform
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.dashboard.rule=Host(`admin.platform.example.com`)"
      - "traefik.http.routers.dashboard.service=api@internal"
      - "traefik.http.routers.dashboard.tls.certresolver=letsencrypt"
```

**Tenant container labels (added automatically during provisioning):**

```yaml
# In each tenant's docker-compose.yml, the Odoo service gets these labels.
# Traefik auto-discovers them — no proxy restart needed.
labels:
  - "traefik.enable=true"

  # Route: <slug>.platform.example.com/api/v2/* → this container:8069
  - "traefik.http.routers.${TENANT_ID}.rule=Host(`${TENANT_ID}.platform.example.com`) && PathPrefix(`/api/v2`)"
  - "traefik.http.routers.${TENANT_ID}.tls.certresolver=wildcard"
  - "traefik.http.routers.${TENANT_ID}.entrypoints=websecure"
  - "traefik.http.services.${TENANT_ID}.loadbalancer.server.port=8069"

  # Per-tenant rate limiting middleware
  - "traefik.http.middlewares.${TENANT_ID}-ratelimit.ratelimit.average=100"
  - "traefik.http.middlewares.${TENANT_ID}-ratelimit.ratelimit.burst=200"
  - "traefik.http.routers.${TENANT_ID}.middlewares=${TENANT_ID}-ratelimit"

  # Inject tenant ID header for tracing
  - "traefik.http.middlewares.${TENANT_ID}-headers.headers.customrequestheaders.X-Tenant-ID=${TENANT_ID}"
```

**Adding a custom domain (when a tenant configures one):**

```yaml
# Additional router rule — just update container labels and Traefik picks it up
labels:
  - "traefik.http.routers.${TENANT_ID}-custom.rule=Host(`erp.acmecorp.com`) && PathPrefix(`/api/v2`)"
  - "traefik.http.routers.${TENANT_ID}-custom.tls.certresolver=letsencrypt"
  - "traefik.http.routers.${TENANT_ID}-custom.entrypoints=websecure"
```

### 9.4 Nginx Configuration (Alternative)

If the team prefers Nginx over Traefik, this config achieves the same result but requires manual config management:

```nginx
# Wildcard subdomain — routes to tenant containers
server {
    listen 443 ssl http2;
    server_name ~^(?<tenant>[a-z0-9-]+)\.platform\.example\.com$;

    ssl_certificate     /etc/nginx/certs/wildcard.pem;
    ssl_certificate_key /etc/nginx/certs/wildcard.key;

    # Only allow /api/v2/* — block everything else
    location /api/v2/ {
        set $backend "";
        access_by_lua_block {
            local tenant_id = ngx.var.tenant
            local res = ngx.location.capture(
                "/internal/resolve/" .. tenant_id
            )
            if res.status ~= 200 then
                ngx.exit(ngx.HTTP_NOT_FOUND)
            end
            ngx.var.backend = res.body
        }

        proxy_pass http://$backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Tenant-ID $tenant;
    }

    # Block Odoo web UI, XML-RPC, JSON-RPC — only /api/v2/* is allowed
    location / {
        return 403 '{"error": "Not Found"}';
        add_header Content-Type application/json;
    }
}

# Control Plane admin UI + API
server {
    listen 443 ssl http2;
    server_name admin.platform.example.com;

    location / {
        proxy_pass http://control-plane:8000;
    }
}

# Frontend UI app
server {
    listen 443 ssl http2;
    server_name app.platform.example.com;

    location / {
        proxy_pass http://frontend-ui:3000;
    }
}

# Signup / marketing page
server {
    listen 443 ssl http2;
    server_name platform.example.com www.platform.example.com;

    location / {
        proxy_pass http://marketing-site:3000;
    }
}
```

Note: With Nginx, every tenant provisioning/removal requires a config file regeneration and `nginx -s reload`. This is graceful (no dropped connections) but adds a step that Traefik eliminates entirely.

### 9.5 Custom Domain Flow

For Mid/Full plan tenants who want a custom domain:

```
1. Tenant requests custom domain via Frontend UI (e.g., erp.acmecorp.com)
2. Control Plane generates a CNAME verification record:
   _verify.erp.acmecorp.com → acme.platform.example.com
3. Tenant adds CNAME in their DNS provider
4. Control Plane verifies DNS propagation (poll every 5 min, timeout 72h)
5. SSL certificate auto-issued:
   - Traefik: automatic via built-in ACME resolver
   - Nginx: certbot triggered by provisioning script
6. Custom domain goes live — routes to same tenant container
```

### 9.6 Single Sign-In Portal

The **main entry point** is the Frontend UI at `app.platform.example.com`:

```
1. User opens app.platform.example.com
2. Login form asks for: company slug (or email) + password
3. Frontend resolves company slug → tenant API URL
   (via Control Plane: GET /tenants/resolve?email=user@acme.com)
4. Frontend calls POST <slug>.platform.example.com/api/v2/auth/login
5. Authenticated against the tenant's own Odoo database
6. Session token returned → stored in browser
7. All subsequent API calls go to <slug>.platform.example.com/api/v2/*
8. Frontend renders the appropriate modules based on user's plan
```

The login page can also present a dropdown/autocomplete of the company slug if the email is associated with a tenant in the Control DB.

---

## 10. API Monitoring, Metering & Analytics

### 10.1 What We Track Per API Call

Every request through `base_api` is logged:

```sql
CREATE TABLE api_call_logs (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    method          VARCHAR(10) NOT NULL,       -- GET, POST, PUT, DELETE
    endpoint        VARCHAR(512) NOT NULL,       -- /api/v2/search/res.partner
    model           VARCHAR(128),               -- res.partner (if applicable)
    operation       VARCHAR(20),                -- read, create, write, unlink
    user_id         INTEGER,                    -- Odoo user ID within tenant
    status_code     SMALLINT NOT NULL,
    response_time_ms INTEGER NOT NULL,
    request_size_bytes INTEGER,
    response_size_bytes INTEGER,
    ip_address      INET,
    user_agent      VARCHAR(512),
    error_code      VARCHAR(50),                -- MISSING_API_KEY, ACCESS_DENIED, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Partition by month for performance
CREATE INDEX idx_api_logs_tenant_ts ON api_call_logs (tenant_id, timestamp);
CREATE INDEX idx_api_logs_endpoint ON api_call_logs (endpoint, timestamp);
```

### 10.2 How Logging Works

The API call logger is implemented as middleware in `base_api`. After each request completes, it asynchronously sends the log entry to the Control Plane:

```python
import threading
import requests

class ApiCallLogger:
    """Non-blocking API call logger that sends to Control Plane."""

    def __init__(self, control_plane_url, control_plane_token, tenant_id):
        self.cp_url = control_plane_url
        self.cp_token = control_plane_token
        self.tenant_id = tenant_id
        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._flush_interval = 10  # seconds

    def log(self, method, endpoint, model, operation, user_id,
            status_code, response_time_ms, request_size, response_size,
            ip_address, user_agent, error_code=None):
        entry = {
            "tenant_id": self.tenant_id,
            "method": method,
            "endpoint": endpoint,
            "model": model,
            "operation": operation,
            "user_id": user_id,
            "status_code": status_code,
            "response_time_ms": response_time_ms,
            "request_size_bytes": request_size,
            "response_size_bytes": response_size,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "error_code": error_code,
        }
        with self._buffer_lock:
            self._buffer.append(entry)
            if len(self._buffer) >= 50:
                self._flush()

    def _flush(self):
        """Send buffered logs to Control Plane in batch."""
        with self._buffer_lock:
            batch = self._buffer[:]
            self._buffer.clear()

        if batch:
            threading.Thread(
                target=self._send_batch, args=(batch,), daemon=True
            ).start()

    def _send_batch(self, batch):
        try:
            requests.post(
                f"{self.cp_url}/internal/api-logs",
                json={"logs": batch},
                headers={"Authorization": f"Bearer {self.cp_token}"},
                timeout=10,
            )
        except Exception:
            pass  # Log locally as fallback
```

### 10.3 Monthly Usage Counters

For efficient quota checking (avoiding COUNT queries on the log table), we maintain rolling counters:

```sql
CREATE TABLE api_usage_monthly (
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    year_month      VARCHAR(7) NOT NULL,     -- "2026-03"
    total_calls     BIGINT DEFAULT 0,
    read_calls      BIGINT DEFAULT 0,
    write_calls     BIGINT DEFAULT 0,
    delete_calls    BIGINT DEFAULT 0,
    failed_calls    BIGINT DEFAULT 0,
    total_response_ms BIGINT DEFAULT 0,      -- for avg latency calc
    PRIMARY KEY (tenant_id, year_month)
);
```

Incremented atomically on each API call via `UPDATE ... SET total_calls = total_calls + 1`.

### 10.4 Metering Dashboard Endpoints (Control Plane)

| Endpoint | Description |
|----------|-------------|
| `GET /admin/tenants/{id}/usage` | Current month API usage + quota |
| `GET /admin/tenants/{id}/usage/history` | Monthly usage over time |
| `GET /admin/tenants/{id}/usage/breakdown` | By endpoint, model, user |
| `GET /admin/tenants/{id}/usage/errors` | Error rate and top errors |
| `GET /admin/overview/usage` | Aggregate usage across all tenants |

---

## 11. Financial Tracking & Company Metrics

### 11.1 Revenue Metrics

The Control Plane calculates and exposes these financial KPIs:

| Metric | Description | Calculation |
|--------|-------------|-------------|
| **MRR** (Monthly Recurring Revenue) | Total monthly revenue from active subscriptions | SUM(plan_price) for active tenants |
| **ARR** (Annual Recurring Revenue) | Annualized recurring revenue | MRR × 12 |
| **ARPU** (Avg Revenue Per User) | Average revenue per tenant | MRR / active_tenant_count |
| **Churn Rate** | % of tenants cancelling per month | cancelled_this_month / active_start_of_month × 100 |
| **Net Revenue Retention** | Revenue retained including upsells | (MRR_end - new_MRR) / MRR_start × 100 |
| **LTV** (Lifetime Value) | Predicted total revenue per tenant | ARPU / monthly_churn_rate |
| **CAC Payback** | Months to recover acquisition cost | CAC / ARPU |
| **Revenue by Plan** | Revenue breakdown by Basic/Mid/Full | SUM(plan_price) GROUP BY plan |
| **Expansion Revenue** | Revenue from plan upgrades | SUM(new_plan_price - old_plan_price) for upgrades |
| **Contraction Revenue** | Revenue lost from downgrades | SUM(old_plan_price - new_plan_price) for downgrades |

### 11.2 Operational Metrics

| Metric | Description |
|--------|-------------|
| **Total Tenants** | Count by status (active, suspended, trial, etc.) |
| **Tenant Growth Rate** | New tenants / month |
| **Avg API Calls/Tenant** | Median and mean monthly API usage |
| **API Error Rate** | % of requests returning 4xx/5xx |
| **P50/P95/P99 Latency** | Response time percentiles across all tenants |
| **Storage Usage** | DB size + filestore size per tenant |
| **Active Users / Tenant** | Users who made at least 1 API call in last 30 days |
| **Feature Adoption** | Which modules/endpoints are most used per plan |
| **Peak Usage Hours** | Time-of-day heatmap of API calls |

### 11.3 Per-Tenant Health Score

A composite score (0-100) for each tenant, useful for Customer Success:

```python
def calculate_health_score(tenant):
    score = 100

    # Usage frequency (are they actively using the platform?)
    if tenant.api_calls_last_30d < 100:
        score -= 30  # Very low usage = churn risk
    elif tenant.api_calls_last_30d < 500:
        score -= 15

    # Feature breadth (using multiple modules?)
    modules_used = len(tenant.distinct_models_last_30d)
    modules_available = len(PLAN_MODULES[tenant.plan]["api_models"])
    adoption_rate = modules_used / modules_available
    if adoption_rate < 0.3:
        score -= 20

    # Payment health
    if tenant.payment_status == "overdue":
        score -= 25

    # User engagement (what % of licensed users are active?)
    if tenant.active_user_ratio < 0.5:
        score -= 10

    return max(score, 0)
```

### 11.4 Interesting Metrics We Can Derive

Beyond standard SaaS metrics, the Odoo data within each tenant (accessible via `base_api` analytics endpoints) gives us **unique platform-level insights**:

| Metric | Source | Value |
|--------|--------|-------|
| **Aggregate GMV** (Gross Merchandise Value) | `sale.order.amount_total` across tenants | Shows platform economic impact |
| **Avg Deal Size by Plan** | CRM `expected_revenue` per plan tier | Validates plan pricing alignment |
| **Industry Benchmarks** | Compare metrics across similar tenants | Selling point for prospects |
| **Module ROI Indicators** | Correlation between modules used and revenue growth | Guides upsell strategy |
| **Seasonal Usage Patterns** | API call volume by month/quarter | Capacity planning |
| **Time-to-Value** | Days from signup to first meaningful API call | Onboarding effectiveness |
| **Stickiness Score** | DAU/MAU ratio per tenant | Retention predictor |
| **Data Growth Rate** | DB size growth per tenant per month | Infrastructure cost forecasting |

---

## 12. Platform Admin Dashboard

### 12.1 Dashboard Sections

The Control Plane serves a web-based admin dashboard (React/Next.js or similar) for the platform operations team.

**Overview Page:**
- MRR / ARR gauge with trend
- Active tenants count and growth chart
- Revenue by plan (pie chart)
- Churn rate trend
- Top 5 tenants by API usage
- System health (all containers running, error rates)

**Tenants Page:**
- Searchable/filterable table of all tenants
- Per-tenant detail view:
  - Company info, plan, payment status
  - API usage chart (last 30 days)
  - User count vs. limit
  - Storage usage vs. limit
  - Health score
  - Action buttons: suspend, upgrade/downgrade, reset password, view logs

**Billing Page:**
- Revenue dashboard (MRR, ARR, churn)
- Payment history with status filters
- Failed payments requiring attention
- Upcoming renewals
- Plan change history

**API Analytics Page:**
- Global API call volume (time series)
- Breakdown by tenant, endpoint, status code
- Error rate heatmap
- Latency percentile charts
- Top consumers

**System Health Page:**
- Container status for all tenants
- Resource utilization (CPU, memory, disk)
- Database sizes
- Background job queue status
- Alert history

### 12.2 Alerting

The Control Plane sends alerts via email/Slack for:

| Alert | Trigger | Severity |
|-------|---------|----------|
| Payment failed | Stripe webhook | High |
| Tenant suspended | Grace period expired | High |
| API quota > 80% | Monthly usage counter | Medium |
| High error rate | > 5% errors in 1 hour for a tenant | Medium |
| Container down | Health check failure | Critical |
| Storage > 80% | Periodic check | Medium |
| Unusual API spike | > 3x normal volume in 1 hour | Medium |
| New signup | Tenant provisioned | Info |

---

## 13. Infrastructure & DevOps

### 13.1 Container Orchestration Options

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| **Docker Compose per tenant** (single host) | Simple, current setup extends naturally | Single point of failure, manual scaling | **Phase 1** — up to ~20 tenants |
| **Docker Swarm** | Built-in clustering, service discovery, simple | Limited ecosystem, declining community | Phase 2 if staying Docker-native |
| **Kubernetes** | Industry standard, auto-scaling, self-healing | Complexity, learning curve, cost | **Phase 3** — when tenant count > 50 |

### 13.2 Phase 1 — Single Host Architecture

For initial launch with < 20 tenants:

```
Server (8-core, 32 GB RAM, 500 GB SSD)
├── Nginx (reverse proxy)
├── Control Plane (FastAPI + PostgreSQL)
├── Tenant 1: Odoo + PostgreSQL
├── Tenant 2: Odoo + PostgreSQL
├── ...
└── Monitoring (Prometheus + Grafana)
```

Estimated capacity: ~20 tenants on a $200/mo dedicated server or equivalent cloud VM.

### 13.3 Phase 2 — Multi-Host with Docker Swarm or Manual Distribution

When a single host is insufficient:

```
Load Balancer (Nginx or cloud LB)
├── Host A: Control Plane + Tenants 1-15
├── Host B: Tenants 16-30
├── Host C: Tenants 31-45
└── Shared: NFS for addons, Centralized logging
```

### 13.4 Backup Strategy

| What | Frequency | Retention | Method |
|------|-----------|-----------|--------|
| Tenant PostgreSQL DBs | Every 6 hours | 30 days | `pg_dump` to S3 |
| Tenant filestores | Daily | 30 days | rsync to S3 |
| Control Plane DB | Every hour | 90 days | `pg_dump` to S3 |
| Full server snapshot | Weekly | 4 weeks | Cloud provider snapshot |

Backup script runs as a cron job, iterating over all tenant directories.

### 13.5 CI/CD Pipeline

```
Git push → CI (GitHub Actions / GitLab CI)
  1. Run tests (base_api unit tests)
  2. Build Odoo Docker image → push to registry (tagged with git SHA)
  3. On main branch merge:
     a. Update shared-addons volume
     b. Rolling restart of tenant containers (one at a time)
     c. Run DB migrations if needed (odoo-bin -u)
```

---

## 14. Security Considerations

### 14.1 Tenant Isolation

| Layer | Measure |
|-------|---------|
| **Network** | Each tenant DB is on an internal-only Docker network; Odoo containers only join the platform network for Control Plane calls |
| **Database** | Separate PostgreSQL instances per tenant (not just separate databases on shared PG) — strongest isolation |
| **Credentials** | Unique, auto-generated DB passwords per tenant (stored encrypted in Control DB) |
| **Filesystem** | Separate Docker volumes per tenant; filestore not shared |
| **API** | `X-Tenant-ID` header set by Nginx, not by client — prevents spoofing |

### 14.2 Secrets Management

- Tenant DB credentials: encrypted at rest in Control DB (AES-256, key from env var or Vault)
- Stripe keys: environment variables, never in code or DB
- Control Plane internal tokens: rotated monthly, one per tenant
- Odoo `admin_passwd`: disabled (`list_db = False`, `admin_passwd` set to random, `dbfilter` locks to tenant DB)

### 14.3 Rate Limiting

Applied at the reverse proxy level (per-IP and per-tenant):

**With Traefik (recommended):** configured via Docker labels per tenant (see [Section 9.3](#93-traefik-configuration-recommended)):

```yaml
# Per-tenant rate limit (set as container labels during provisioning)
- "traefik.http.middlewares.${TENANT_ID}-ratelimit.ratelimit.average=100"
- "traefik.http.middlewares.${TENANT_ID}-ratelimit.ratelimit.burst=200"
```

**With Nginx (alternative):**

```nginx
limit_req_zone $binary_remote_addr zone=per_ip:10m rate=30r/s;
limit_req_zone $tenant zone=per_tenant:10m rate=100r/s;

server {
    location /api/ {
        limit_req zone=per_ip burst=50 nodelay;
        limit_req zone=per_tenant burst=200 nodelay;
    }
}
```

### 14.4 Audit Trail

All administrative actions on the Control Plane are logged:

```sql
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       VARCHAR(255) NOT NULL,    -- admin email or "system"
    action      VARCHAR(100) NOT NULL,    -- tenant.create, tenant.suspend, plan.change, etc.
    target_type VARCHAR(50),              -- tenant, payment, plan
    target_id   UUID,
    details     JSONB,
    ip_address  INET,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 15. Data Architecture

### 15.1 Database Topology

```
┌─────────────────────────────────────────────────────┐
│                    Control Plane DB                  │
│  Tables: tenants, payments, plan_changes,           │
│          api_call_logs, api_usage_monthly,           │
│          audit_log, tenant_health_snapshots          │
│  Purpose: Platform-level data, billing, metering    │
└─────────────────────────────────────────────────────┘

┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Tenant A DB  │  │ Tenant B DB  │  │ Tenant C DB  │
│ (PostgreSQL) │  │ (PostgreSQL) │  │ (PostgreSQL) │
│              │  │              │  │              │
│ Standard Odoo│  │ Standard Odoo│  │ Standard Odoo│
│ tables +     │  │ tables +     │  │ tables +     │
│ api.session  │  │ api.session  │  │ api.session  │
└──────────────┘  └──────────────┘  └──────────────┘
```

### 15.2 Data Retention Policies

| Data Type | Active Retention | Archive | Delete |
|-----------|-----------------|---------|--------|
| API call logs (detailed) | 90 days | Roll up to daily aggregates, keep 2 years | After 2 years |
| API usage monthly | Indefinite | — | — |
| Payment records | Indefinite | — | — (legal requirement) |
| Audit logs | 1 year active | Archive to cold storage | After 7 years |
| Tenant backups (after cancellation) | 90 days | — | After 90 days |

---

## 16. Migration Path

### From Current State to Multi-Tenant

**Step 1: Prepare the Odoo image**
- Freeze current Odoo 19 source into a versioned Docker image
- Push to a private container registry
- Test that addons (`base_api`, `api_doc`) work from the image without bind mounts

**Step 2: Build the Control Plane**
- Standalone FastAPI service with its own PostgreSQL
- Implement tenant CRUD, plan management, internal APIs
- Implement Stripe integration (webhooks, checkout)

**Step 3: Build the Subscription Enforcer**
- New middleware layer in `base_api` (`subscription_enforcer.py`)
- Wire into every endpoint's authentication flow
- Add user count checks to user creation endpoint

**Step 4: Build the Provisioning Pipeline**
- Script/service that creates tenant directories from template
- Automates Docker Compose up, Odoo DB init, admin user creation
- Updates Nginx config and reloads

**Step 5: Build the Routing Layer**
- Nginx config with wildcard subdomain routing
- Tenant resolution (subdomain → container port mapping)
- SSL setup (wildcard cert or Let's Encrypt per custom domain)

**Step 6: Build the Admin Dashboard**
- React app consuming Control Plane APIs
- Tenant management, billing overview, API analytics

**Step 7: Migrate existing deployment**
- Current single-tenant becomes "Tenant #1"
- All new companies go through the signup → provisioning flow

---

## 17. Cost Estimation Model

### 17.1 Infrastructure Cost Per Tenant

| Component | Basic | Mid | Full |
|-----------|-------|-----|------|
| CPU allocation | 1 core | 2 cores | 4 cores |
| RAM allocation | 1 GB | 2 GB | 4 GB |
| Storage (DB + filestore) | 5 GB | 25 GB | 100 GB |
| **Estimated infra cost/month** | ~$10 | ~$25 | ~$60 |
| **Plan price** | $49 | $149 | $399 |
| **Gross margin** | ~80% | ~83% | ~85% |

### 17.2 Fixed Costs

| Item | Monthly Cost |
|------|-------------|
| Server(s) | $200-800 (scales with tenants) |
| Domain + SSL | $10 |
| Stripe fees | 2.9% + $0.30 per transaction |
| Monitoring (Grafana Cloud / Datadog) | $0-50 |
| Backup storage (S3) | $5-20 |
| **Total fixed** | ~$225-880/mo |

### 17.3 Break-Even Analysis

With average plan price of ~$150/mo and infrastructure cost of ~$25/tenant:
- Fixed costs: ~$500/mo
- Break-even: **4-5 paying tenants**
- At 20 tenants: ~$3,000/mo MRR, ~$2,000/mo profit
- At 50 tenants: ~$7,500/mo MRR, ~$5,500/mo profit

---

## 18. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Noisy neighbor** — one tenant consuming excessive resources | Medium | High | Docker resource limits, per-plan CPU/RAM caps, rate limiting |
| **Data breach** — cross-tenant data leakage | Low | Critical | Separate DB instances, network isolation, security audit |
| **Single host failure** — all tenants go down | Medium | Critical | Daily backups, move to multi-host (Phase 2), health monitoring |
| **Billing sync issues** — Stripe webhook lost | Low | High | Idempotent webhook handlers, reconciliation job every 6 hours |
| **Tenant provisioning failure** — container won't start | Low | Medium | Retry logic, manual provisioning fallback, monitoring alerts |
| **Odoo upgrade breaks addons** — base_api incompatible | Low | High | Pin Odoo version, thorough testing in CI before rolling out |
| **Runaway API costs** — tenant makes millions of calls | Low | Medium | Hard quota enforcement, automatic throttling at 90% |
| **Legal/compliance** — data residency requirements | Medium | Medium | Document data location, consider multi-region in Phase 3 |

---

## 19. Implementation Phases & Timeline

### Phase 1 — Foundation (Weeks 1-8)

| Week | Deliverable |
|------|-------------|
| 1-2 | Control Plane API: tenant model, plan definitions, internal APIs |
| 2-3 | Subscription Enforcer middleware in `base_api` |
| 3-4 | Tenant provisioning pipeline (template → Docker Compose → init) |
| 4-5 | Traefik setup: subdomain routing, auto-SSL, Docker label discovery |
| 5-6 | Stripe integration (checkout, webhooks, grace period logic) |
| 6-8 | Frontend UI: login flow, tenant resolution, core module screens (CRM, Contacts), user management. All data via `base_api`. |

**Milestone:** First tenant can sign up, pay, be provisioned, and use the platform through the Frontend UI + `base_api`.

### Phase 2 — Observability & Admin (Weeks 9-12)

| Week | Deliverable |
|------|-------------|
| 9-10 | API call logging middleware + Control Plane ingestion |
| 10-11 | Admin dashboard: tenant management, billing overview |
| 11-12 | API analytics dashboard, usage reports, alerting |

**Milestone:** Platform team can monitor all tenants, see financials, and manage subscriptions.

### Phase 3 — Polish & Scale (Weeks 13-16)

| Week | Deliverable |
|------|-------------|
| 13 | Custom domain support (CNAME verification, auto-SSL via Traefik) |
| 14 | Self-service signup flow (marketing page → Stripe → provisioning) |
| 14-15 | Frontend UI: remaining module screens (Sales, HR, Inventory, etc.), analytics dashboards |
| 15 | Health scoring, automated churn alerts, tenant benchmarks |
| 16 | Load testing, security audit, documentation |

**Milestone:** Platform ready for public launch.

### Phase 4 — Scale-Out (Weeks 17+)

- Multi-host distribution
- Kubernetes migration evaluation
- Multi-region support
- Advanced analytics (ML-based churn prediction)
- Marketplace for add-on modules
- Frontend UI: advanced features (data export, bulk operations, custom dashboards)

---

## 20. Open Decisions

These items need team alignment before implementation begins:

| # | Decision | Options | Recommendation | Status |
|---|----------|---------|----------------|--------|
| 1 | **Control Plane tech stack** | FastAPI (Python), NestJS (Node), Go | FastAPI — team already knows Python, aligns with Odoo | Proposed |
| 2 | **Admin dashboard framework** | React + Tailwind, Next.js, Vue | Next.js (SSR for admin pages, React ecosystem) | Proposed |
| 3 | **Payment processor** | Stripe, Paddle, LemonSqueezy | Stripe (most flexible, best API) | Proposed |
| 4 | **Container registry** | Docker Hub, GitHub Container Registry, self-hosted | GitHub Container Registry (free for private repos) | Proposed |
| 5 | **Monitoring stack** | Prometheus + Grafana, Datadog, self-built | Prometheus + Grafana (cost-effective, self-hosted) | Proposed |
| 6 | **Separate PG instance vs. shared PG with separate DBs** | Separate containers (stronger isolation) vs. shared (less resource overhead) | Separate containers (security > efficiency at this stage) | Proposed |
| 7 | **Trial period** | 14-day free trial, no trial (pay upfront), freemium tier | 14-day free trial of Mid plan | Proposed |
| 8 | **Plan pricing** | As proposed ($49/$149/$399) or adjusted | Needs market validation | Open |
| 9 | **Custom domain — which plans?** | Mid + Full only, or all plans | Mid + Full (value-add for premium) | Proposed |
| 10 | **Data residency** | Single region (US/EU) or multi-region | Single region initially, document for compliance | Proposed |
| 11 | **Reverse proxy** | Traefik vs Nginx | Traefik — dynamic Docker discovery, auto-SSL, zero-touch provisioning (see [Section 9.2](#92-nginx-vs-traefik-comparison)) | Proposed |
| 12 | **Frontend UI framework** | React + Vite, Next.js, Remix | Next.js — SSR for login/marketing, React SPA for app, same framework as admin dashboard | Proposed |
| 13 | **Frontend UI deployment** | Single SPA for all tenants vs per-tenant build | Single SPA — tenant context resolved at login, no per-tenant builds | Proposed |

---

## Appendix A: Control Plane API Surface

### External APIs (for tenants / signup)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/signup` | Create account + Stripe checkout |
| GET | `/plans` | List available plans and pricing |
| POST | `/billing/portal` | Redirect to Stripe billing portal |
| GET | `/billing/usage` | Current tenant's usage summary |

### Internal APIs (Odoo ↔ Control Plane, authenticated by internal token)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/internal/tenants/{id}/info` | Tenant plan, status, limits (for enforcer) |
| POST | `/internal/api-logs` | Batch ingest API call logs |
| POST | `/internal/tenants/{id}/heartbeat` | Container health report |
| PUT | `/internal/tenants/{id}/usage/increment` | Atomic API call counter increment |

### Admin APIs (for platform admin dashboard)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/tenants` | List all tenants with filters |
| GET | `/admin/tenants/{id}` | Tenant detail |
| POST | `/admin/tenants/{id}/suspend` | Suspend tenant |
| POST | `/admin/tenants/{id}/activate` | Activate tenant |
| PUT | `/admin/tenants/{id}/plan` | Change tenant plan |
| GET | `/admin/metrics/revenue` | MRR, ARR, churn, etc. |
| GET | `/admin/metrics/usage` | Aggregate API usage |
| GET | `/admin/tenants/{id}/usage` | Per-tenant usage detail |

---

## Appendix B: Environment Variables Reference

### Per-Tenant Container

| Variable | Example | Description |
|----------|---------|-------------|
| `TENANT_ID` | `acme-corp` | Unique tenant identifier |
| `TENANT_DB` | `tenant_acme_corp` | PostgreSQL database name |
| `TENANT_DB_USER` | `tenant_acme_user` | Database username |
| `TENANT_DB_PASSWORD` | `(generated)` | Database password |
| `CONTROL_PLANE_URL` | `http://control-plane:8000` | Internal CP URL |
| `CONTROL_PLANE_INTERNAL_TOKEN` | `(generated)` | Auth token for CP calls |
| `TENANT_PLAN` | `mid` | Current plan (basic/mid/full) |

### Control Plane

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Control Plane PostgreSQL connection string |
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `JWT_SECRET` | Secret for admin dashboard auth tokens |
| `ENCRYPTION_KEY` | AES key for encrypting tenant DB passwords |

---

## Appendix C: Tenant Lifecycle State Machine

```
                ┌──────────┐
     signup ──▶ │provisioning│
                └─────┬────┘
                      │ success
                      ▼
                ┌──────────┐
        ┌──────│  active   │◀─────────┐
        │      └─────┬────┘          │
        │            │ payment       │ payment
        │            │ failed        │ received
        │            ▼               │
        │      ┌──────────────┐      │
        │      │ grace_period │──────┘
        │      └─────┬────────┘
        │            │ grace expired
        │            ▼
        │      ┌──────────┐
        │      │ suspended │──── payment received ──▶ active
        │      └─────┬────┘
        │            │ 90 days
        │            ▼
        │      ┌──────────┐
        └─────▶│ cancelled │
               └─────┬────┘
                     │ 90 days (data retention)
                     ▼
               ┌──────────┐
               │  deleted  │
               └──────────┘
```

---

*This document is a living artifact. Update it as decisions are made and implementation progresses. Each section that moves from "Proposed" to "Decided" should be annotated with the date and decision-maker.*
