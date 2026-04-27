# Build Prompt: API Metering + Tenant Provisioning

Copy everything below the line into a new Claude Code conversation.

---

You are implementing two features for a multi-tenant SaaS platform built on Odoo 19:

1. **API Metering** — Track API call counts per tenant per month, enforce quotas
2. **Tenant Provisioning** — Auto-create Docker stacks (Odoo + PostgreSQL) when a new tenant is registered

The platform has two projects:
- **Control Plane** at `/Users/cheickcisse/Projects/control-plane/` — FastAPI service managing plans, tenants, billing
- **Odoo** at `/Users/cheickcisse/Projects/odoo/` — Odoo 19 with `addons/base_api/` custom REST API

Before starting, read these files to understand the existing code:
- `/Users/cheickcisse/Projects/control-plane/app/services/tenant_service.py` — current tenant logic, `build_tenant_info()` has hardcoded `usage: {"api_calls_this_month": 0}`
- `/Users/cheickcisse/Projects/control-plane/app/models/` — existing Plan, Tenant, PlanChange models
- `/Users/cheickcisse/Projects/control-plane/app/routers/internal.py` — the `/internal/tenants/{slug}/info` endpoint
- `/Users/cheickcisse/Projects/control-plane/app/routers/admin_tenants.py` — tenant CRUD endpoints
- `/Users/cheickcisse/Projects/odoo/addons/base_api/services/subscription_enforcer.py` — enforcer with `check_api_quota()` (reads usage but no real data exists)
- `/Users/cheickcisse/Projects/odoo/addons/base_api/controllers/simple_api.py` — all API endpoints (calls `_enforce_subscription()` but NOT `check_api_quota()`)
- `/Users/cheickcisse/Projects/odoo/docker-compose.yml` — current single-tenant Docker setup
- `/Users/cheickcisse/Projects/odoo/Dockerfile` — Odoo Docker image build
- `/Users/cheickcisse/Projects/odoo/docker/entrypoint.sh` — entrypoint that parses env vars and generates odoo.conf
- `/Users/cheickcisse/Projects/odoo/MULTI_TENANT_SAAS_PLAN.md` sections 4.2 (provisioning flow), 4.4 (resource limits), 5.2 (module-to-plan mapping)

---

## PART 1: API Metering & Usage Tracking

### 1.1 Control Plane — New Table: `api_usage_monthly`

Create a new Alembic migration (`migrations/versions/003_api_usage_monthly.py`):

```sql
CREATE TABLE api_usage_monthly (
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    year_month      VARCHAR(7) NOT NULL,      -- "2026-04"
    total_calls     BIGINT NOT NULL DEFAULT 0,
    read_calls      BIGINT NOT NULL DEFAULT 0,
    write_calls     BIGINT NOT NULL DEFAULT 0,
    delete_calls    BIGINT NOT NULL DEFAULT 0,
    failed_calls    BIGINT NOT NULL DEFAULT 0,
    total_response_ms BIGINT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, year_month)
);
```

Create the corresponding SQLAlchemy model at `app/models/api_usage.py`. Add it to `app/models/__init__.py`.

### 1.2 Control Plane — New Endpoint: `PUT /internal/tenants/{slug}/usage/increment`

Add to `app/routers/internal.py`. This is called by Odoo's `base_api` after each API request.

**Request:**
```json
{
    "calls": 1,
    "call_type": "read",
    "response_ms": 45,
    "failed": false
}
```

**Logic:**
- Look up the tenant by slug
- Get the current `year_month` as `datetime.now(UTC).strftime("%Y-%m")`
- Upsert into `api_usage_monthly`: if row exists for this tenant+month, increment counters. If not, insert with initial values.
- Use SQLAlchemy's `insert().on_conflict_do_update()` for atomic upsert
- Return `{"success": true, "total_calls": <new_total>}`
- Authenticated by `INTERNAL_API_KEY` (same as other internal endpoints)

**Performance note:** This endpoint is called on every API request from every tenant. Keep it fast:
- Single upsert query, no joins
- No extra validation beyond auth
- Return immediately after the upsert

### 1.3 Control Plane — Batch Increment Endpoint: `POST /internal/usage/batch`

For better performance, also add a batch endpoint that accepts multiple increments at once:

```json
{
    "entries": [
        {"tenant_slug": "acme", "calls": 5, "read_calls": 3, "write_calls": 2, "failed_calls": 0, "response_ms": 230},
        {"tenant_slug": "acme", "calls": 3, "read_calls": 3, "write_calls": 0, "failed_calls": 1, "response_ms": 180}
    ]
}
```

This allows the Odoo enforcer to buffer calls and send them in batches.

### 1.4 Control Plane — Update `build_tenant_info()` with Real Usage

In `app/services/tenant_service.py`, replace the hardcoded `usage: {"api_calls_this_month": 0}` with a real query:

```python
async def get_current_month_usage(db: AsyncSession, tenant_id: UUID) -> dict:
    year_month = datetime.now(timezone.utc).strftime("%Y-%m")
    result = await db.execute(
        select(ApiUsageMonthly).where(
            ApiUsageMonthly.tenant_id == tenant_id,
            ApiUsageMonthly.year_month == year_month,
        )
    )
    usage = result.scalar_one_or_none()
    if usage is None:
        return {"api_calls_this_month": 0}
    return {
        "api_calls_this_month": usage.total_calls,
        "read_calls": usage.read_calls,
        "write_calls": usage.write_calls,
        "delete_calls": usage.delete_calls,
        "failed_calls": usage.failed_calls,
        "avg_response_ms": round(usage.total_response_ms / max(usage.total_calls, 1)),
    }
```

Call this in `build_tenant_info()` and pass the result as the `usage` field.

**Important:** `build_tenant_info()` is currently a sync function. You'll need to make it async (it's called from an async router, so this should be straightforward). Add the `db` session as a parameter.

### 1.5 Control Plane — Admin Usage Endpoints

Add to `app/routers/admin_tenants.py`:

`GET /admin/tenants/{tenant_id}/usage` — returns current month usage for a tenant
`GET /admin/tenants/{tenant_id}/usage/history` — returns last 12 months of usage

### 1.6 Control Plane — Pydantic Schemas

Add to `app/schemas/`:
- `UsageIncrement` — request schema for the increment endpoint
- `UsageBatchRequest` — request schema for the batch endpoint  
- `UsageResponse` — response schema for usage data

### 1.7 Control Plane — Tests

Add `tests/test_usage.py`:
- Test increment endpoint (single call)
- Test increment creates new row for new month
- Test increment updates existing row (totals accumulate)
- Test batch increment
- Test `GET /internal/tenants/{slug}/info` returns real usage after increments
- Test that `build_tenant_info` includes real usage

### 1.8 Odoo `base_api` — API Call Counter

Add a new file: `addons/base_api/services/api_call_logger.py`

This is a lightweight, non-blocking logger that buffers API call counts and periodically flushes them to the Control Plane.

```python
"""Non-blocking API call logger.

Buffers API call counts in memory and flushes to the Control Plane
in batches every N calls or every M seconds, whichever comes first.

This runs inside each Odoo container. It uses the same env vars as
the subscription enforcer (TENANT_ID, CONTROL_PLANE_URL, CONTROL_PLANE_TOKEN).
"""

import logging
import os
import threading
import time
import requests

_logger = logging.getLogger(__name__)


class ApiCallLogger:
    _instance = None
    _lock = threading.Lock()

    FLUSH_INTERVAL = 30       # seconds between flushes
    FLUSH_THRESHOLD = 50      # flush after this many buffered calls

    def __init__(self, tenant_id, control_plane_url, control_plane_token):
        self.tenant_id = tenant_id
        self.cp_url = control_plane_url.rstrip('/')
        self.cp_token = control_plane_token
        self._buffer = {"calls": 0, "read_calls": 0, "write_calls": 0,
                        "delete_calls": 0, "failed_calls": 0, "response_ms": 0}
        self._buffer_lock = threading.Lock()
        self._start_flush_timer()

    @classmethod
    def get_instance(cls):
        """Singleton. Returns None if env vars not set."""
        if cls._instance is not None:
            return cls._instance
        tenant_id = os.environ.get('TENANT_ID', '').strip()
        cp_url = os.environ.get('CONTROL_PLANE_URL', '').strip()
        cp_token = os.environ.get('CONTROL_PLANE_TOKEN', '').strip()
        if not tenant_id or not cp_url or not cp_token:
            return None
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(tenant_id, cp_url, cp_token)
        return cls._instance

    def log_call(self, method, status_code, response_time_ms):
        """Record a single API call. Non-blocking."""
        with self._buffer_lock:
            self._buffer["calls"] += 1
            self._buffer["response_ms"] += response_time_ms
            if method in ('GET', 'HEAD', 'OPTIONS'):
                self._buffer["read_calls"] += 1
            elif method == 'DELETE':
                self._buffer["delete_calls"] += 1
            else:
                self._buffer["write_calls"] += 1
            if status_code >= 400:
                self._buffer["failed_calls"] += 1
            if self._buffer["calls"] >= self.FLUSH_THRESHOLD:
                self._flush_async()

    def _flush_async(self):
        """Flush buffer to Control Plane in a background thread."""
        with self._buffer_lock:
            if self._buffer["calls"] == 0:
                return
            batch = self._buffer.copy()
            self._buffer = {"calls": 0, "read_calls": 0, "write_calls": 0,
                            "delete_calls": 0, "failed_calls": 0, "response_ms": 0}
        threading.Thread(target=self._send, args=(batch,), daemon=True).start()

    def _send(self, batch):
        """Send buffered counts to the Control Plane."""
        try:
            requests.put(
                f"{self.cp_url}/internal/tenants/{self.tenant_id}/usage/increment",
                json=batch,
                headers={"Authorization": f"Bearer {self.cp_token}"},
                timeout=5,
            )
        except Exception as e:
            _logger.warning("Failed to send usage data to Control Plane: %s", e)

    def _start_flush_timer(self):
        """Periodically flush the buffer."""
        def _timer_loop():
            while True:
                time.sleep(self.FLUSH_INTERVAL)
                self._flush_async()
        t = threading.Thread(target=_timer_loop, daemon=True)
        t.start()
```

### 1.9 Odoo `base_api` — Wire Logger into API Endpoints

In `addons/base_api/controllers/simple_api.py`, add a helper method to `SimpleApiController`:

```python
def _log_api_call(self, method, status_code, start_time):
    """Log an API call to the usage tracker. Non-blocking, best-effort."""
    from odoo.addons.base_api.services.api_call_logger import ApiCallLogger
    logger = ApiCallLogger.get_instance()
    if logger is not None:
        import time
        response_ms = int((time.time() - start_time) * 1000)
        logger.log_call(method, status_code, response_ms)
```

Then, in every authenticated endpoint, capture timing and log after the response:

```python
import time as _time

@http.route('/api/v2/search/<string:model>', ...)
def search_model(self, model):
    _start = _time.time()
    # ... existing auth + enforcement + business logic ...
    response = self._json_response(...)
    self._log_api_call('GET', response.status_code, _start)
    return response
```

**Do this for ALL endpoints that go through authentication.** The pattern is:
1. Record `_start = _time.time()` at the top
2. At the end (before returning), call `self._log_api_call(method, status_code, _start)`
3. Also log on error responses

**Simpler alternative if the endpoints are too many:** Instead of modifying every endpoint, create a wrapper. Read the existing code structure first and pick the approach that causes the fewest changes. If there's a common auth pattern you can hook into, use that.

### 1.10 Odoo `base_api` — Wire `check_api_quota()` into Enforcement

In `simple_api.py`, add a new helper:

```python
def _enforce_api_quota(self):
    """Check API call quota. Returns None if OK, or error response if exceeded."""
    enforcer = self._get_enforcer()
    if enforcer is None:
        return None
    allowed, error = enforcer.check_api_quota()
    if not allowed:
        return self._error_response(error['message'], error['status_code'], error['code'])
    return None
```

Then call it alongside the existing `_enforce_subscription()` in every authenticated endpoint:

```python
# Existing enforcement
sub_error = self._enforce_subscription()
if sub_error:
    return sub_error

# NEW: API quota enforcement
quota_error = self._enforce_api_quota()
if quota_error:
    return quota_error
```

---

## PART 2: Tenant Provisioning Pipeline

### 2.1 Control Plane — Tenant Directory Template

Create `templates/` directory in the control-plane project root with a Jinja2 template for per-tenant docker-compose files.

**`templates/docker-compose.tenant.yml.j2`:**

```yaml
# Auto-generated by Control Plane provisioning for tenant: {{ tenant_id }}
# Do not edit manually.

services:
  db:
    image: postgres:18
    environment:
      POSTGRES_DB: {{ db_name }}
      POSTGRES_USER: {{ db_user }}
      POSTGRES_PASSWORD: {{ db_password }}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {{ db_user }} -d {{ db_name }}"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - internal
    deploy:
      resources:
        limits:
          cpus: "{{ resources.cpu }}"
          memory: {{ resources.memory }}

  odoo:
    image: {{ odoo_image }}
    depends_on:
      db:
        condition: service_healthy
    environment:
      DB_HOST: db
      DB_PORT: "5432"
      DB_USER: {{ db_user }}
      DB_PASSWORD: {{ db_password }}
      DB_NAME: {{ db_name }}
      TENANT_ID: {{ tenant_id }}
      CONTROL_PLANE_URL: {{ control_plane_url }}
      CONTROL_PLANE_TOKEN: {{ internal_token }}
    volumes:
      - ./data/filestore:/var/lib/odoo/filestore
    networks:
      - internal
      - saas-net
    deploy:
      resources:
        limits:
          cpus: "{{ resources.cpu }}"
          memory: {{ resources.memory }}

networks:
  internal:
    driver: bridge
  saas-net:
    external: true

```

### 2.2 Control Plane — Configuration

Add to `app/config.py`:

```python
# Provisioning
TENANTS_BASE_DIR: str = "/data/tenants"             # Where tenant directories are created
ODOO_IMAGE: str = "odoo-odoo"                        # Docker image for Odoo (pre-built, from registry or local)
CONTROL_PLANE_INTERNAL_URL: str = "http://control-plane-app-1:8000"  # URL tenants use to reach CP
ODOO_SOURCE_DIR: str = "/Users/cheickcisse/Projects/odoo"  # Path to Odoo source (for building image)
```

Read `ODOO_IMAGE` from the Odoo project — run `docker compose config` on the Odoo project to find the image name, or check the Dockerfile.

### 2.3 Control Plane — Plan Resources & Modules Mapping

Create `app/services/plan_resources.py`:

```python
"""Resource limits and module lists per plan.

Maps plan slugs to Docker resource constraints and Odoo module lists.
These are used during tenant provisioning to configure the container and
initialize the database.
"""

PLAN_RESOURCES = {
    "basic": {"cpu": "1", "memory": "1G", "workers": 2, "db_maxconn": 16},
    "mid":   {"cpu": "2", "memory": "2G", "workers": 4, "db_maxconn": 32},
    "full":  {"cpu": "4", "memory": "4G", "workers": 8, "db_maxconn": 64},
}

PLAN_MODULES = {
    "basic": "base,base_api,contacts,crm,mail,calendar,web",
    "mid":   "base,base_api,api_doc,contacts,crm,sale,sale_management,hr,purchase,account,mail,calendar,web,product",
    "full":  (
        "base,base_api,api_doc,debt_management,account,sale,sale_management,crm,hr,purchase,"
        "stock,project,calendar,contacts,product,mail,web"
    ),
}

def get_resources(plan_slug: str) -> dict:
    return PLAN_RESOURCES.get(plan_slug, PLAN_RESOURCES["basic"])

def get_modules(plan_slug: str) -> str:
    return PLAN_MODULES.get(plan_slug, PLAN_MODULES["basic"])
```

### 2.4 Control Plane — Provisioning Service

Create `app/services/provisioning_service.py`. This is the core of the provisioning pipeline.

```python
"""Tenant provisioning pipeline.

Creates a per-tenant Docker stack (Odoo + PostgreSQL), initializes the database,
creates the admin user, and updates the tenant record in the Control Plane.

Each tenant gets its own directory under TENANTS_BASE_DIR:
    /data/tenants/<tenant-slug>/
    ├── docker-compose.yml    (generated from template)
    └── data/
        ├── postgres/         (PostgreSQL data volume)
        └── filestore/        (Odoo filestore)
"""
```

Implement:

```python
import asyncio
import os
import secrets
import string
import subprocess
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from app.config import get_settings

_logger = logging.getLogger(__name__)

def _generate_password(length=32):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def _generate_internal_token(length=48):
    return secrets.token_urlsafe(length)

async def provision_tenant(tenant, plan, db) -> dict:
    """Full provisioning pipeline for a new tenant.
    
    Args:
        tenant: Tenant ORM object (already created in DB with status='provisioning')
        plan: Plan ORM object
        db: AsyncSession
    
    Returns:
        dict with provisioning results: {"container_host", "odoo_port", "db_name",
              "db_user", "db_password", "internal_token", "admin_password"}
    
    Raises:
        ProvisioningError on failure at any step
    """
    settings = get_settings()
    tenant_slug = tenant.slug
    
    # Step 1: Generate credentials
    db_name = f"tenant_{tenant_slug.replace('-', '_')}"
    db_user = f"tenant_{tenant_slug.replace('-', '_')}_user"
    db_password = _generate_password()
    internal_token = _generate_internal_token()
    admin_password = _generate_password(16)
    
    # Step 2: Create tenant directory
    tenant_dir = Path(settings.TENANTS_BASE_DIR) / tenant_slug
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "data" / "postgres").mkdir(parents=True, exist_ok=True)
    (tenant_dir / "data" / "filestore").mkdir(parents=True, exist_ok=True)
    
    # Step 3: Render docker-compose.yml from template
    from app.services.plan_resources import get_resources, get_modules
    resources = get_resources(plan.slug)
    
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("docker-compose.tenant.yml.j2")
    compose_content = template.render(
        tenant_id=tenant_slug,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        odoo_image=settings.ODOO_IMAGE,
        control_plane_url=settings.CONTROL_PLANE_INTERNAL_URL,
        internal_token=internal_token,
        resources=resources,
    )
    
    compose_file = tenant_dir / "docker-compose.yml"
    compose_file.write_text(compose_content)
    
    # Step 4: Start the containers
    _run_compose(tenant_dir, ["up", "-d"])
    
    # Step 5: Wait for PostgreSQL to be healthy
    await _wait_for_healthy(tenant_dir, "db", timeout=60)
    
    # Step 6: Initialize Odoo database with plan modules
    modules = get_modules(plan.slug)
    _run_compose(tenant_dir, [
        "exec", "-T", "odoo",
        "python3", "/opt/odoo/odoo-bin",
        "-c", "/etc/odoo/odoo.conf",
        "-d", db_name,
        "-i", modules,
        "--stop-after-init",
        "--no-http",
    ])
    
    # Step 7: Create admin user
    # Use Odoo's shell or direct DB insert to create the company admin.
    # The simplest approach: use the Odoo RPC or the base_api endpoint once the container restarts.
    # For now, Odoo creates a default 'admin' user. We can update the password via the DB.
    _run_compose(tenant_dir, [
        "exec", "-T", "db",
        "psql", "-U", db_user, "-d", db_name,
        "-c", f"UPDATE res_users SET login = '{tenant.admin_email}' WHERE id = 2;"
    ])
    
    # Step 8: Restart Odoo (to pick up initialized DB)
    _run_compose(tenant_dir, ["restart", "odoo"])
    await _wait_for_healthy(tenant_dir, "odoo", timeout=120)
    
    # Step 9: Get container info
    container_name = f"{tenant_slug}-odoo-1"
    
    return {
        "container_host": container_name,
        "odoo_port": 8069,
        "db_name": db_name,
        "db_user": db_user,
        "db_password": db_password,
        "internal_token": internal_token,
        "admin_password": admin_password,
    }


def _run_compose(tenant_dir: Path, args: list[str]):
    """Run a docker compose command in a tenant directory."""
    cmd = ["docker", "compose", "-f", str(tenant_dir / "docker-compose.yml"),
           "--project-name", tenant_dir.name] + args
    _logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        _logger.error("Command failed: %s\nstderr: %s", " ".join(cmd), result.stderr)
        raise ProvisioningError(f"Docker command failed: {result.stderr[:500]}")
    return result


async def _wait_for_healthy(tenant_dir: Path, service: str, timeout: int = 60):
    """Wait for a Docker Compose service to be healthy."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = subprocess.run(
            ["docker", "compose", "-f", str(tenant_dir / "docker-compose.yml"),
             "--project-name", tenant_dir.name,
             "ps", service, "--format", "json"],
            capture_output=True, text=True
        )
        if '"healthy"' in result.stdout or '"running"' in result.stdout:
            return
        await asyncio.sleep(3)
    raise ProvisioningError(f"Service {service} did not become healthy within {timeout}s")


class ProvisioningError(Exception):
    pass
```

### 2.5 Control Plane — Provisioning Job Model

Create a new migration (`migrations/versions/004_provisioning_jobs.py`):

```sql
CREATE TABLE provisioning_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    step            VARCHAR(50),
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Create the SQLAlchemy model at `app/models/provisioning_job.py`.

### 2.6 Control Plane — Provisioning Endpoint

Add to `app/routers/admin_tenants.py`:

`POST /admin/tenants/{tenant_id}/provision`

This endpoint:
1. Looks up the tenant (must exist, status must be `provisioning`)
2. Creates a `provisioning_jobs` record with status `pending`
3. Kicks off the provisioning pipeline in a background task (`asyncio.create_task` or `BackgroundTasks`)
4. Returns immediately with `{"status": "provisioning", "job_id": "..."}`
5. The background task:
   a. Updates job status to `running`
   b. Calls `provision_tenant()`
   c. On success: updates tenant record with `container_host`, `odoo_port`, `db_name`, `internal_token`, status → `active`. Updates job status to `completed`
   d. On failure: updates job with error message, status → `failed`. Tenant stays `provisioning`

`GET /admin/tenants/{tenant_id}/provision/status` — returns the latest provisioning job for the tenant.

### 2.7 Control Plane — Deprovisioning

Add to `app/services/provisioning_service.py`:

```python
async def deprovision_tenant(tenant) -> None:
    """Stop and remove a tenant's Docker stack. Data volumes are kept for retention period."""
    settings = get_settings()
    tenant_dir = Path(settings.TENANTS_BASE_DIR) / tenant.slug
    if tenant_dir.exists():
        _run_compose(tenant_dir, ["down"])  # stops containers, keeps volumes
        _logger.info("Deprovisioned tenant %s (data retained at %s)", tenant.slug, tenant_dir)

async def destroy_tenant(tenant) -> None:
    """Fully destroy a tenant's Docker stack and data. Irreversible."""
    settings = get_settings()
    tenant_dir = Path(settings.TENANTS_BASE_DIR) / tenant.slug
    if tenant_dir.exists():
        _run_compose(tenant_dir, ["down", "-v"])  # destroys containers AND volumes
        import shutil
        shutil.rmtree(tenant_dir)
        _logger.info("Destroyed tenant %s and all data", tenant.slug)
```

Wire into existing suspend/activate endpoints:
- `POST /admin/tenants/{id}/suspend` → calls `deprovision_tenant()` (stops containers, keeps data)
- `POST /admin/tenants/{id}/activate` → if containers are stopped, starts them again with `docker compose up -d`

### 2.8 Control Plane — Docker Compose Updates

The Control Plane's `docker-compose.yml` needs access to Docker socket and the tenants directory. Update:

```yaml
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      # ... existing vars ...
      TENANTS_BASE_DIR: /data/tenants
      ODOO_IMAGE: odoo-odoo
      CONTROL_PLANE_INTERNAL_URL: http://control-plane-app-1:8000
    volumes:
      - ./app:/app/app
      - ./templates:/app/templates            # Jinja2 templates
      - /var/run/docker.sock:/var/run/docker.sock  # Docker socket for provisioning
      - tenants-data:/data/tenants             # Tenant directories
    networks:
      - default
      - saas-net
```

Add to volumes:
```yaml
volumes:
  cp-db-data:
  tenants-data:
```

**Security note:** Mounting the Docker socket gives the Control Plane container full Docker control. This is necessary for provisioning but should be locked down in production.

### 2.9 Control Plane — Add `jinja2` to requirements.txt

Add `jinja2>=3.1.0` to `requirements.txt`.

### 2.10 Control Plane — Tests

Add `tests/test_provisioning.py`:
- Test template rendering (provide variables, check output YAML)
- Test credential generation (uniqueness, length)
- Test provisioning job status transitions

Add `tests/test_usage.py` (from Part 1):
- Test all usage endpoints

**Note:** Full integration tests for provisioning (actually spinning up Docker containers) are complex. Focus on unit tests for template rendering, credential generation, and the provisioning service's logic with mocked subprocess calls.

---

## IMPORTANT CONSTRAINTS

- The Odoo Docker image must already be built before provisioning. The provisioning pipeline does NOT build the image — it pulls it from a registry or uses a locally built one. The current `Dockerfile` in the Odoo project builds `odoo-odoo`. Use this image name.
- Each tenant's docker-compose uses the `saas-net` external network (already created).
- The provisioning pipeline runs `docker compose` commands as subprocesses. This is the simplest approach for Phase 1. In Phase 2+, this could be replaced with the Docker SDK for Python.
- For the Odoo image to be available to tenant containers, it must be built first: `cd /Users/cheickcisse/Projects/odoo && docker compose build`
- Do not modify the Odoo Dockerfile or entrypoint.sh. The existing entrypoint already handles env var parsing and odoo.conf generation.
- The provisioning is triggered explicitly via `POST /admin/tenants/{id}/provision`, NOT automatically on tenant creation. This lets the admin review the tenant record before provisioning.
- API metering is best-effort — if the Control Plane is temporarily unreachable, usage counts are lost (acceptable for Phase 1). The enforcer's cached quota check still works with slightly stale data.
