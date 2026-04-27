# Membership & Module Access System — Implementation Plan

> **Status:** ALL PHASES COMPLETE (A, B, C implemented; D done in frontend repos)
> **Date:** 2026-04-04 (last updated: 2026-04-04)
> **Relation:** Implements Section 5 of `MULTI_TENANT_SAAS_PLAN.md`

---

## 1. Architecture: How the Control Plane Relates to the SaaS

The Control Plane is **not** a layer inside Odoo. It is a **separate service** that sits above and orchestrates all tenant Odoo instances. Think of it as the landlord of an apartment building — it doesn't live in any apartment, but it decides who gets a key, which rooms they can access, and when the lease expires.

```
                    ┌──────────────────────────────┐
                    │        CONTROL PLANE          │
                    │      (FastAPI + its own DB)    │
                    │                                │
                    │  Knows about:                  │
                    │  - All tenants                 │
                    │  - All plans/memberships       │
                    │  - Billing state               │
                    │  - Who can access what          │
                    │                                │
                    │  Does NOT know about:          │
                    │  - Individual CRM leads         │
                    │  - Sale orders                  │
                    │  - Employee records             │
                    │  (That's tenant-level data)     │
                    └─────────┬──────────────────────┘
                              │
                              │  HTTP (internal network)
                              │  "Tenant X has plan Mid,
                              │   25 users max,
                              │   modules: crm,sales,hr"
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
            ▼                 ▼                 ▼
     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
     │  Tenant A   │  │  Tenant B   │  │  Tenant C   │
     │  (Odoo +    │  │  (Odoo +    │  │  (Odoo +    │
     │  base_api)  │  │  base_api)  │  │  base_api)  │
     │             │  │             │  │             │
     │  Has its    │  │  Has its    │  │  Has its    │
     │  own DB,    │  │  own DB,    │  │  own DB,    │
     │  own users, │  │  own users, │  │  own users, │
     │  own data   │  │  own data   │  │  own data   │
     └─────────────┘  └─────────────┘  └─────────────┘
```

### The relationship in plain terms

- The **Control Plane** is a standalone FastAPI app with its own PostgreSQL database. It lives in its own directory, its own Docker container, its own repo (or folder). It is the "management layer" of the entire SaaS.

- Each **tenant** is a full Odoo instance (the current project). When a tenant's `base_api` receives an API request, it asks the Control Plane: "What plan is this tenant on? What modules are allowed? How many users can they have?" The Control Plane answers, and `base_api` enforces it.

- The **multi-tenant SaaS plan** describes the whole system. This document focuses narrowly on the **membership/module-access piece** — the part where each tenant has a plan that controls what they can do.

### Does the Control Plane sit "above" one SaaS?

Yes, exactly. One Control Plane manages N tenants. If you eventually run multiple SaaS products (e.g., an Odoo-based ERP SaaS and some other product), they could share a Control Plane or have separate ones. But for now: **1 Control Plane → N Odoo tenant instances**.

---

## 2. Project Structure

The Control Plane is a **separate project**, not inside the Odoo folder. Recommended layout:

```
/Users/cheickcisse/Projects/
│
├── odoo/                          # EXISTING — Odoo source + base_api
│   ├── addons/base_api/           #   Modified: add subscription enforcer
│   ├── docker-compose.yml         #   Modified: per-tenant template
│   └── ...
│
├── control-plane/                 # NEW — FastAPI service
│   ├── app/
│   │   ├── main.py                #   FastAPI entry point
│   │   ├── models/                #   SQLAlchemy models (plans, tenants, etc.)
│   │   ├── routers/               #   API routes (admin, internal, public)
│   │   ├── services/              #   Business logic (provisioning, billing)
│   │   └── config.py              #   Settings from env vars
│   ├── migrations/                #   Alembic DB migrations
│   ├── tests/
│   ├── Dockerfile
│   ├── docker-compose.yml         #   Control Plane + its own PostgreSQL
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/                      # LATER — React/Next.js app
    └── ...
```

Why separate folders:
- Different tech stack (FastAPI vs Odoo)
- Different deployment lifecycle (you deploy the Control Plane once; you deploy N Odoo instances)
- Different databases
- Clean separation of concerns

---

## 3. Data Model: Plans & Memberships

### 3.1 The `plans` Table (in Control Plane DB)

This is the core of the customizability. Plans are data, not code.

```sql
CREATE TABLE plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            VARCHAR(50) UNIQUE NOT NULL,     -- 'basic', 'mid', 'full', 'custom-acme'
    name            VARCHAR(100) NOT NULL,            -- 'Basic Plan'
    description     TEXT,

    -- Limits
    max_users       INTEGER NOT NULL DEFAULT 5,       -- -1 = unlimited
    max_api_calls   INTEGER NOT NULL DEFAULT 10000,   -- per month, -1 = unlimited
    storage_limit_gb INTEGER NOT NULL DEFAULT 5,

    -- Module access: array of module keys from MODULE_ACCESS_MAP
    -- e.g. {'contacts','crm'} or {'__all__'} for full access
    allowed_modules TEXT[] NOT NULL,

    -- Pricing
    price_cents     INTEGER NOT NULL,                 -- 4900 = $49.00
    currency        VARCHAR(3) NOT NULL DEFAULT 'USD',
    billing_interval VARCHAR(10) NOT NULL DEFAULT 'monthly',  -- 'monthly', 'yearly'

    -- State
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,    -- can new tenants pick this plan?
    is_public       BOOLEAN NOT NULL DEFAULT TRUE,    -- shown on pricing page? (false = custom/private plan)

    -- Extensibility
    metadata        JSONB NOT NULL DEFAULT '{}',      -- future fields without migrations
    -- Example metadata: {"support_level": "email_48h", "custom_domain": false, "data_export": ["csv"]}

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.2 The `tenants` Table (in Control Plane DB)

Each tenant references a plan. When you change a tenant's plan, you just update the FK.

```sql
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            VARCHAR(63) UNIQUE NOT NULL,       -- subdomain: 'acme-corp'
    company_name    VARCHAR(255) NOT NULL,
    admin_email     VARCHAR(255) NOT NULL,

    -- Membership
    plan_id         UUID NOT NULL REFERENCES plans(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'provisioning'
                    CHECK (status IN ('provisioning','active','trial',
                                      'grace_period','suspended','cancelled','deleted')),

    -- Billing
    stripe_customer_id      VARCHAR(255),
    stripe_subscription_id  VARCHAR(255),
    payment_status  VARCHAR(20) DEFAULT 'pending'
                    CHECK (payment_status IN ('current','pending','overdue','cancelled')),
    grace_period_end TIMESTAMPTZ,
    trial_expires_at TIMESTAMPTZ,

    -- Infrastructure
    container_host  VARCHAR(255),
    odoo_port       INTEGER,
    db_name         VARCHAR(63),
    internal_token  VARCHAR(255),                      -- for CP <-> tenant auth

    -- Overrides (optional per-tenant customization ON TOP of plan)
    max_users_override      INTEGER,                   -- NULL = use plan default
    max_api_calls_override  INTEGER,                   -- NULL = use plan default
    extra_modules           TEXT[] DEFAULT '{}',        -- modules added beyond the plan

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**The override columns are the customizability escape hatch.** If Acme Corp is on the "Mid" plan but you want to give them 5 extra users and access to "project", you set `max_users_override = 30` and `extra_modules = {'project'}` instead of creating a whole new plan. The effective limits become:

```
effective_max_users    = tenant.max_users_override    ?? tenant.plan.max_users
effective_modules      = tenant.plan.allowed_modules  ∪  tenant.extra_modules
effective_api_calls    = tenant.max_api_calls_override ?? tenant.plan.max_api_calls
```

### 3.3 Plan Change History (audit trail)

```sql
CREATE TABLE plan_changes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    old_plan_id UUID REFERENCES plans(id),
    new_plan_id UUID NOT NULL REFERENCES plans(id),
    reason      VARCHAR(255),
    changed_by  VARCHAR(255) NOT NULL,                -- admin email or 'system' or 'stripe_webhook'
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 4. The Internal API (Control Plane → Tenant Communication)

The only way tenants talk to the Control Plane is through one endpoint. This is the contract between the two systems.

### 4.1 `GET /internal/tenants/{tenant_id}/info`

Called by the `base_api` enforcer inside each Odoo container. Returns everything the tenant needs to enforce access.

**Request:**
```
GET /internal/tenants/acme-corp/info
Authorization: Bearer <internal_token>
```

**Response:**
```json
{
    "tenant_id": "acme-corp",
    "status": "active",
    "payment_status": "current",
    "payment_overdue": false,
    "grace_days_remaining": null,

    "plan": {
        "slug": "mid",
        "name": "Mid Tier",
        "allowed_modules": ["contacts", "crm", "sales", "hr", "purchase", "accounting", "products", "calendar"],
        "max_users": 25,
        "max_api_calls": 100000
    },

    "effective": {
        "allowed_modules": ["contacts", "crm", "sales", "hr", "purchase", "accounting", "products", "calendar", "project"],
        "max_users": 30,
        "max_api_calls": 100000
    },

    "usage": {
        "api_calls_this_month": 42371
    }
}
```

The `plan` block is the raw plan. The `effective` block includes tenant-level overrides (`extra_modules`, `max_users_override`). The enforcer in `base_api` uses `effective`.

### 4.2 `POST /internal/tenants/{tenant_id}/invalidate`

Called by the Control Plane TO the tenant's Odoo container when a plan changes. Tells the enforcer to clear its cache so the next request fetches fresh data.

```
POST https://acme-corp.platform.example.com/api/v2/internal/invalidate-cache
Authorization: Bearer <internal_token>
```

This is a push from Control Plane → tenant (the reverse direction of 4.1).

---

## 5. Changes to `base_api` (Inside the Odoo Project)

### 5.1 New File: `addons/base_api/models/subscription_enforcer.py`

A class that caches plan info and provides check methods. Instantiated once per Odoo worker process, using env vars `TENANT_ID`, `CONTROL_PLANE_URL`, `CONTROL_PLANE_TOKEN`.

Methods:
- `get_effective_plan()` → cached plan info from Control Plane
- `check_subscription_active()` → is the tenant active and paid?
- `check_user_limit(current_count)` → can they create another user?
- `check_module_allowed(module_key)` → is this module in their plan?
- `check_api_quota()` → have they hit the monthly limit?
- `invalidate_cache()` → clear the cached plan info

### 5.2 Modify: `addons/base_api/controllers/simple_api.py`

**A. Extend `_get_module_access()` (existing method)**

Currently returns `{module_key: {accessible, label, model}}`. Change to also include `in_plan`:

```python
def _get_module_access(self):
    enforcer = self._get_enforcer()
    plan_modules = enforcer.get_effective_plan()["effective"]["allowed_modules"]

    result = {}
    for key, info in MODULE_ACCESS_MAP.items():
        model_name = info['model']
        # existing check: does model exist and user has access?
        accessible = (model_name in request.env
                      and self._check_model_access(model_name, 'read'))
        # new check: is this module in the tenant's plan?
        in_plan = ('__all__' in plan_modules) or (key in plan_modules)

        result[key] = {
            'accessible': accessible and in_plan,  # both must be true
            'in_plan': in_plan,                     # frontend uses this to show/hide
            'label': info['label'],
            'model': model_name,
        }
    return result
```

**B. Add enforcement to auth flow**

After successful authentication in `_authenticate()` / `_authenticate_session()`, call `enforcer.check_subscription_active()`. If inactive, return 403 immediately.

**C. Add enforcement to user creation**

In `_create_user_with_groups()`, before creating the user, count active internal users and call `enforcer.check_user_limit(count)`.

**D. Add enforcement to model-level endpoints**

In `search`, `create`, `update`, `delete` — resolve the model to a module key using `MODULE_ACCESS_MAP`, then call `enforcer.check_module_allowed(module_key)`.

**E. Extend `GET /api/v2/auth/me` response**

Add plan info so the frontend gets everything it needs in one call:

```python
# Added to the /auth/me response:
"plan": {
    "slug": "mid",
    "name": "Mid Tier",
    "max_users": 30,
    "current_users": 12,
    "can_create_users": True,      # current_users < max_users
    "allowed_modules": ["contacts", "crm", "sales", "hr", "purchase"]
},
"module_access": { ... }          # from _get_module_access()
```

**F. New internal endpoint: `POST /api/v2/internal/invalidate-cache`**

Authenticated by the internal token (not user session). Calls `enforcer.invalidate_cache()`. Used by the Control Plane to push plan changes instantly.

### 5.3 New File: `addons/base_api/utils/module_resolver.py`

A reverse lookup: given an Odoo model name, return the module key. Built from `MODULE_ACCESS_MAP`:

```python
# MODEL_TO_MODULE = {'crm.lead': 'crm', 'sale.order': 'sales', ...}
def resolve_module_key(model_name):
    return MODEL_TO_MODULE.get(model_name, None)
```

If a model doesn't map to any module key (e.g., `res.partner` maps to `contacts`), the enforcer can either allow it (safe default for utility models) or block it.

---

## 6. How the Frontend Uses This

The frontend (React/Next.js) calls `GET /api/v2/auth/me` on login and stores the result.

### What the frontend does with `module_access`:

| `in_plan` | `accessible` | Frontend behavior |
|-----------|-------------|-------------------|
| `true`    | `true`      | Show the module normally (nav item, dashboard card, routes) |
| `true`    | `false`     | Module is in plan but user lacks Odoo group permissions — show as "no permission" or hide |
| `false`   | `—`         | **Hide the module entirely** from nav, dashboard, routes. Optionally show a locked/upgrade badge |

### What the frontend does with `plan`:

- Show user count: "12 / 30 users" with a progress bar
- When `can_create_users` is `false`, disable the "Add User" button and show "User limit reached — upgrade your plan"
- Show current plan name in settings/account page
- "Upgrade" button links to Stripe billing portal (or Control Plane `/billing/portal`)

---

## 7. Task Breakdown

### Phase A: Control Plane Foundation

| # | Task | Detail | Depends on |
|---|------|--------|------------|
| A1 | **Create `control-plane/` project** | Init FastAPI project, Dockerfile, docker-compose with PostgreSQL, .env.example, Alembic for migrations | — |
| A2 | **Define `plans` table + seed data** | Alembic migration. Seed with 3 plans: basic (contacts,crm / 5 users / $49), mid (+sales,hr,purchase,accounting,products / 25 users / $149), full (__all__ / unlimited / $399) | A1 |
| A3 | **Define `tenants` table** | Alembic migration. FK to plans, override columns, status enum | A2 |
| A4 | **Define `plan_changes` table** | Alembic migration. Audit trail for plan changes | A3 |
| A5 | **Build `GET /internal/tenants/{tenant_id}/info`** | The core endpoint. Joins tenant + plan, computes effective limits (applying overrides), returns JSON | A3 |
| A6 | **Build admin CRUD for plans** | `GET/POST/PUT /admin/plans` — list, create, update plans. Protected by admin auth | A2 |
| A7 | **Build admin CRUD for tenants** | `GET/POST/PUT /admin/tenants` — list, create, update tenants (including plan assignment, overrides). `POST /admin/tenants/{id}/change-plan` with audit log | A4 |
| A8 | **Build plan change push** | When a plan changes (A7), the Control Plane POSTs to the tenant's `/api/v2/internal/invalidate-cache` endpoint | A5, B3 |

### Phase B: `base_api` Enforcement (Inside Odoo Project)

| # | Task | Detail | Depends on |
|---|------|--------|------------|
| B1 | **Create `subscription_enforcer.py`** | New file in `addons/base_api/models/`. Class with cache, TTL, check methods. Reads `TENANT_ID`, `CONTROL_PLANE_URL`, `CONTROL_PLANE_TOKEN` from env vars. Fetches from A5 endpoint | A5 |
| B2 | **Create `module_resolver.py`** | New file in `addons/base_api/utils/`. Reverse map from Odoo model name → module key. Built from `MODULE_ACCESS_MAP` | — |
| B3 | **Add `POST /api/v2/internal/invalidate-cache`** | New endpoint in `simple_api.py`. Authenticated by internal token, not session. Calls `enforcer.invalidate_cache()` | B1 |
| B4 | **Wire enforcer into auth flow** | Modify `_authenticate()` and `_authenticate_session()` in `simple_api.py`. After successful auth, call `enforcer.check_subscription_active()`. Return 403 if inactive | B1 |
| B5 | **Wire enforcer into user creation** | Modify `_create_user_with_groups()`. Count active internal users, call `enforcer.check_user_limit()`. Return 403 with `USER_LIMIT_REACHED` if at max | B1 |
| B6 | **Wire enforcer into model endpoints** | Modify `search`, `create`, `update`, `delete` routes. Use module_resolver (B2) to map model → module key, call `enforcer.check_module_allowed()`. Return 403 with `MODULE_NOT_IN_PLAN` if blocked | B1, B2 |
| B7 | **Modify `_get_module_access()`** | Add `in_plan` field to response. Merge enforcer's allowed_modules with existing accessible check | B1 |
| B8 | **Extend `GET /api/v2/auth/me`** | Add `plan` block (slug, name, max_users, current_users, can_create_users, allowed_modules) and full `module_access` to response | B1, B7 |

### Phase C: Integration & Testing

| # | Task | Detail | Depends on |
|---|------|--------|------------|
| C1 | **Docker networking** | Ensure Control Plane and tenant Odoo containers share a Docker network so they can reach each other via internal HTTP | A1 |
| C2 | **Env var wiring** | Add `TENANT_ID`, `CONTROL_PLANE_URL`, `CONTROL_PLANE_TOKEN` to the tenant docker-compose template and .env.example | B1 |
| C3 | **Test: basic plan tenant** | Provision a tenant on basic plan. Verify: can access contacts+crm, cannot access sales/hr/purchase. User creation blocked at 5 | A5, B4-B8 |
| C4 | **Test: plan upgrade** | Change tenant from basic → mid via admin API. Verify: cache invalidated, sales/hr/purchase become accessible, user limit raised to 25 | A7, A8 |
| C5 | **Test: per-tenant override** | Add `extra_modules = {'project'}` to a mid tenant. Verify project becomes accessible without changing the plan | A7, B6 |
| C6 | **Test: user limit edge cases** | At max users: create blocked. Deactivate a user: create unblocked. Reactivate: blocked again if at max | B5 |

### Phase D: Frontend Integration (Later — After Frontend Exists)

| # | Task | Detail |
|---|------|--------|
| D1 | **Store plan in app state** | On login, call `GET /api/v2/auth/me`, store `plan` and `module_access` in React context/zustand |
| D2 | **Conditional navigation** | Show/hide nav items based on `module_access[key].in_plan` |
| D3 | **User limit UI** | Show "X / Y users" in user management. Disable "Add User" when `can_create_users` is false |
| D4 | **Upgrade prompts** | When user navigates to a module not in plan, show gated page with upgrade CTA |
| D5 | **Plan info in settings** | Show current plan, link to Stripe billing portal for self-service upgrade |

---

## 8. Execution Order

```
Week 1:  A1 → A2 → A3 → A4 (Control Plane skeleton + tables)
Week 2:  A5 → A6 → A7 (Control Plane APIs)
Week 2:  B1 → B2 (enforcer + resolver in base_api, can build in parallel with A6-A7)
Week 3:  B3 → B4 → B5 → B6 → B7 → B8 (wire enforcer into all base_api endpoints)
Week 3:  A8 (push invalidation, needs B3 done)
Week 4:  C1 → C2 → C3 → C4 → C5 → C6 (integration tests)
Later:   D1 → D2 → D3 → D4 → D5 (frontend, when it exists)
```

---

## 9. Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Plans stored in DB, not code | Data-driven | Add/edit/remove plans without code deploys. Custom plans per tenant. |
| Per-tenant overrides (`extra_modules`, `max_users_override`) | Override columns on `tenants` table | Avoids creating a new plan for every one-off customization |
| Module gating uses module keys, not Odoo model names | Module keys (`crm`, `sales`) from `MODULE_ACCESS_MAP` | One module key maps to many models. Gating at module level is simpler and matches how users think |
| Control Plane is a separate project/service | Separate FastAPI app | Different tech stack, different lifecycle, different DB. Clean boundary |
| Enforcer caches with 5-min TTL + push invalidation | Balance performance vs freshness | 99% of requests use cache. Plan changes push-invalidate instantly |
| Backend is the real gate, frontend just hides UI | Defense in depth | Even if frontend is bypassed, API rejects unauthorized access |
| `GET /auth/me` returns plan + module_access | Single call on login | Frontend gets everything it needs without extra round-trips |
