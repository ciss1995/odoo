# Security Audit — `base_api` Module

**Date:** 2026-03-21
**Scope:** `addons/base_api/` — controllers, models, security ACLs
**Out of scope:** Odoo core (maintained by Odoo SA)

---

## Summary

| Severity | Count | Done | Open | Status |
|----------|-------|------|------|--------|
| P0 — Critical | 3 | 3 | 0 | Closed |
| P1 — High | 4 | 4 | 0 | Closed |
| P2 — Medium | 6 | 6 | 0 | Closed |
| **Total** | **13** | **13** | **0** | |

---

## Findings

### P0-1: Session tokens readable by any internal user

**Status:** DONE (2026-03-21)

**File:** `controllers/simple_api.py` (search endpoint) + `security/ir.model.access.csv`

**Problem:** The `api.session` model stores session tokens in a plaintext `Char` field. The ACL file grants read access to `base.group_user`:

```
access_api_session_user,api.session.user,model_api_session,base.group_user,1,0,0,0
```

Because there is no model allowlist on the generic search endpoint, any internal user can call:

```
GET /api/v2/search/api.session?fields=token,user_id
```

This returns every active session token, including admin sessions. An attacker with any internal user account can hijack any other user's session, including administrators.

**Impact:** Full privilege escalation. Any internal user becomes admin.

**Fix implemented:**

1. Remove the `token` field from API-readable fields, or
2. Add a model blocklist to the search endpoint that prevents querying `api.session`, or
3. Remove read ACL for `base.group_user` on `api.session`

Applied:

```csv
# BEFORE
access_api_session_user,api.session.user,model_api_session,base.group_user,1,0,0,0

# AFTER
# line removed; only admin ACL remains:
access_api_session_admin,api.session.admin,model_api_session,base.group_system,1,1,1,1
```

**Breaking change for frontend?** NO. The frontend uses `session-token` header for auth — it never queries the `api.session` model directly. No client-side changes needed.

---

### P0-2: Session tokens stored in plaintext

**Status:** DONE (2026-03-21)

**File:** `models/api_session.py`

**Problem:** The `token` field is a plain `Char` field:

```python
token = fields.Char(string='Session Token', required=True, index=True)
```

If the database is compromised (SQL injection, backup leak, hosting breach), all active session tokens are immediately usable. Unlike Odoo's native `res.users.apikeys` which hashes keys, session tokens are stored raw.

**Impact:** Database breach = all sessions compromised instantly.

**Fix implemented:** Session tokens are hashed before storage and compared by hash on authenticate/refresh/logout.

```python
import hashlib

class ApiSession(models.Model):
    _name = 'api.session'

    token = fields.Char(string='Token Hash', required=True, index=True)

    @staticmethod
    def _hash_token(raw_token):
        return hashlib.sha256(raw_token.encode()).hexdigest()
```

Controller updated to hash incoming `session-token` and search/write using hashed values.

**Breaking change for frontend?** NO. The frontend sends the raw token in the `session-token` header — that does not change. The backend just hashes it before comparing. The login response still returns the raw token. No client-side changes needed.

---

### P0-3: No model blocklist on generic CRUD endpoints

**Status:** DONE (2026-03-21)

**File:** `controllers/simple_api.py` — `search_model`, `create_record`, `update_record`, `delete_record`

**Problem:** The generic endpoints accept any model name. If a user has the right ACL, they can read/write sensitive internal models:

- `api.session` — session tokens (see P0-1)
- `ir.config_parameter` — may contain OAuth secrets, database UUID
- `ir.cron` — scheduled actions (could be modified to run malicious code)
- `ir.rule` — security rules (reading reveals the access control logic)
- `res.users.apikeys` — API key hashes (information disclosure)

**Impact:** Data exposure and potential system compromise through sensitive model access.

**Fix implemented:** Added a blocklist of models that cannot be accessed via generic API endpoints:

```python
BLOCKED_MODELS = {
    'api.session',
    'ir.cron',
    'ir.rule',
    'ir.model.access',
    'res.users.apikeys',
    'ir.attachment',  # file system access
    'base.module.update',
}

def _is_model_blocked(self, model_name):
    return model_name in BLOCKED_MODELS
```

Checks are now enforced in `search_model`, `get_record_by_id`, `create_record`, `update_record`, `delete_record`, and filtered from `list_models`.

**Breaking change for frontend?** DEPENDS. If the frontend queries `api.session` for session management, that would break. However, the frontend uses the `session-token` header — it should never need to query `api.session` directly. If it does query `ir.cron` or `ir.rule` (unlikely), those calls would break. **Review frontend code for any direct model queries against these blocked models before deploying.**

---

### P1-1: Record-level access rules not explicitly enforced

**Status:** DONE (2026-03-21)

**File:** `controllers/simple_api.py` — `_check_model_access`

**Problem:** The access check only calls `check_access_rights()` (model-level ACL). It does not call `check_access_rule()` (record-level rules). While Odoo's ORM may enforce record rules during `.search()`, `.write()`, and `.unlink()`, the errors are caught by the generic `except Exception` block and returned as 500 errors rather than proper 403 ACCESS_DENIED responses.

This means:
- A user with `write` access on `mail.activity` could potentially modify another user's activities
- The error would appear as a generic 500 `UPDATE_ERROR` instead of a clear access denial

**Impact:** Unclear access boundaries; security errors masked as server errors.

**Fix implemented:** `AccessError` is now explicitly handled in CRUD/search/get endpoints and returned as `ACCESS_DENIED` (403) instead of generic 500 errors.

```python
from odoo.exceptions import AccessError, MissingError, ValidationError

# In update_record, delete_record, etc.:
try:
    record.write(data)
except AccessError as e:
    return self._error_response("Access denied", 403, "ACCESS_DENIED")
except (MissingError, ValidationError) as e:
    return self._error_response(str(e), 400, "UPDATE_ERROR")
except Exception as e:
    _logger.error("Error: %s", str(e))
    return self._error_response("Error updating record", 500, "UPDATE_ERROR")
```

**Breaking change for frontend?** NO. This changes error codes from `UPDATE_ERROR`/`DELETE_ERROR` (500) to `ACCESS_DENIED` (403) in cases where the user lacks record-level permissions. The frontend should already handle 403 responses. If anything, this improves the frontend experience by providing clearer error messages.

---

### P1-2: No rate limiting on login endpoint

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `user_login`

**Problem:** `POST /api/v2/auth/login` has no rate limiting, account lockout, or progressive delay. An attacker can brute-force passwords at network speed.

**Impact:** Password compromise through brute-force attacks.

**Fix:** Implement rate limiting. Options (from simplest to most robust):

**Option A — In-memory counter (simplest, no dependencies):**

```python
import time
from collections import defaultdict

_login_attempts = defaultdict(list)
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes

def _check_rate_limit(self, identifier):
    now = time.time()
    attempts = _login_attempts[identifier]
    # Remove old attempts
    _login_attempts[identifier] = [t for t in attempts if now - t < WINDOW_SECONDS]
    if len(_login_attempts[identifier]) >= MAX_ATTEMPTS:
        return False
    _login_attempts[identifier].append(now)
    return True
```

**Option B — Nginx/reverse proxy rate limiting (recommended for production):**

```nginx
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

location /api/v2/auth/login {
    limit_req zone=login burst=3 nodelay;
    proxy_pass http://odoo;
}
```

**Breaking change for frontend?** NO for Option B (infrastructure only). For Option A, the frontend would receive a new `RATE_LIMITED` error code (429) after too many failed attempts. The frontend should handle this by showing a "too many attempts" message. This is an additive change — successful logins are unaffected.

---

### P1-3: Plaintext credentials returned in API responses

**Status:** DONE (2026-03-21)

**File:** `controllers/simple_api.py` — `_create_user_with_groups`, `reset_user_password`

**Problem:** When creating users or resetting passwords, plaintext credentials are returned in the HTTP response body:

```python
credentials['temporary_password'] = temp_password
credentials['api_key'] = api_key
```

```python
'temporary_password': temp_password,
```

If HTTP response bodies are logged by a reverse proxy, load balancer, or application monitoring tool, these credentials are permanently exposed in logs.

**Impact:** Credential leakage through log files.

**Fix implemented:**
- For production: ensure HTTPS is enforced (prevents network sniffing)
- Added `Cache-Control: no-store, no-cache, must-revalidate` and `Pragma: no-cache` headers to responses containing credentials
- Consider not returning passwords at all — instead, force a password reset flow via email
- For API keys: this is acceptable since the key is shown once by design (Odoo's native behavior)

```python
response = request.make_response(
    json.dumps(response_data, default=str),
    headers=[
        ('Content-Type', 'application/json'),
        ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ('Pragma', 'no-cache'),
    ]
)
```

**Breaking change for frontend?** NO. The response format stays the same. The added headers are transparent to the frontend. If you choose to stop returning `temporary_password`, the frontend would need to update its user-creation flow to show a "check email" message instead of displaying the password. **Discuss with frontend team before removing password from response.**

---

### P2-1: Broad exception swallowing masks security errors

**Status:** DONE (2026-03-21)

**File:** `controllers/simple_api.py` — all endpoints

**Problem:** Every endpoint wraps its logic in `except Exception`, which catches Odoo's `AccessError` and converts it to a generic 500 error. This makes it impossible to distinguish between "user lacks permission" and "server crashed."

Example:

```python
except Exception as e:
    _logger.error("Error updating %s/%s: %s", model, record_id, str(e))
    return self._error_response("Error updating record", 500, "UPDATE_ERROR")
```

**Impact:** Security denials are invisible in monitoring; debugging access issues is difficult.

**Fix implemented:** Added explicit handling for `AccessError` and `(MissingError, ValidationError)` before generic exception fallback in generic CRUD/search/get endpoints.

```python
from odoo.exceptions import AccessError, MissingError, ValidationError

try:
    # ... operation ...
except AccessError:
    return self._error_response("Access denied", 403, "ACCESS_DENIED")
except (MissingError, ValidationError) as e:
    return self._error_response(str(e), 400, "UPDATE_ERROR")
except Exception as e:
    _logger.error("Unexpected error: %s", str(e))
    return self._error_response("Internal server error", 500, "INTERNAL_ERROR")
```

**Breaking change for frontend?** MINIMAL. Some operations that previously returned 500 with `UPDATE_ERROR` or `DELETE_ERROR` will now return 403 with `ACCESS_DENIED` or 400 with `VALIDATION_ERROR`. The frontend should already handle 403/400 responses. **If the frontend matches on specific error codes like `UPDATE_ERROR` to display messages, those code paths would need updating.** In practice, this is unlikely to cause issues.

---

### P2-2: `sudo()` overuse in user management

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `list_users`, `get_user`, `update_user`, `_create_user_with_groups`

**Problem:** Multiple user management endpoints use `.sudo()` for operations that could respect Odoo's security model:

```python
users = request.env['res.users'].sudo().search(domain, ...)
target_user.sudo().write(update_data)
user = request.env['res.users'].sudo().create(data)
```

This bypasses Odoo's record rules entirely. For example, `list_users` with `.sudo()` returns ALL users (including portal users) to any `base.group_user`, even if Odoo's record rules would normally restrict visibility.

**Impact:** Users may see more data than intended by Odoo's security design.

**Fix:** For read operations, remove `.sudo()` and let Odoo's record rules filter naturally. For write operations on users, `.sudo()` is often necessary (Odoo restricts user writes to admins by default), but the permission check should happen before the `.sudo()` call:

```python
# Read — let Odoo filter
users = request.env['res.users'].search(domain, ...)

# Write — check first, then sudo
if not is_admin:
    return self._error_response("Access denied", 403, "ACCESS_DENIED")
target_user.sudo().write(update_data)
```

**Breaking change for frontend?** POSSIBLE. If the frontend currently displays a list of all users (including portal/public users) and relies on the `.sudo()` behavior, removing it would reduce the visible user list to only those the current user's record rules allow. **The frontend user list may show fewer users after this fix.** This is technically the correct behavior, but could be perceived as a regression. Test the user list page after this change.

---

### P1-4: IDOR in update_record and delete_record

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `update_record`, `delete_record`

**Problem:** Both endpoints used `browse(record_id)` to fetch records without applying the user's scope domain. By contrast, `get_record_by_id` correctly applies `_get_record_scope_domain()`. A user could update or delete records outside their allowed scope by guessing record IDs (e.g., a salesperson modifying another team's leads).

**Impact:** Privilege escalation — users can modify/delete records they should not have access to.

**Fix implemented:** Replaced `browse(record_id)` with `search([('id', '=', record_id)] + scope_domain, limit=1)` in both endpoints, matching the pattern already used in `get_record_by_id`.

**Breaking change for frontend?** NO. Out-of-scope records now return 404 instead of succeeding. The frontend should already handle 404 responses.

---

### P2-3: Information disclosure in inventory error messages

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `inventory_adjust`, `inventory_decrement`

**Problem:** Generic `except Exception` handlers returned raw exception text to the client: `f"Error adjusting inventory: {str(e)}"`. Database constraint names, table names, and internal paths could leak.

**Impact:** Information disclosure aiding further attacks.

**Fix implemented:** Error responses now return generic messages. Exception details are logged server-side only.

**Breaking change for frontend?** NO.

---

### P2-4: Unbounded limit/offset parameters (DoS risk)

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `list_partners`, `list_products`, `search_model`, `list_users`

**Problem:** The `limit` and `offset` query parameters accepted any integer with no upper bound. A request like `?limit=1000000` could force the ORM to load millions of records into memory.

**Impact:** Denial of Service through resource exhaustion.

**Fix implemented:** Added `_parse_pagination()` helper that caps `limit` to 1000, floors `offset` at 0, and returns a 400 error for non-integer values. All four list endpoints now use this helper.

**Breaking change for frontend?** UNLIKELY. Only affects requests with `?limit=` exceeding 1000, which would be capped silently. Invalid (non-integer) values now return 400 instead of 500.

---

### P2-5: Incomplete BLOCKED_MODELS list

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `BLOCKED_MODELS`

**Problem:** Several sensitive Odoo models were missing from the blocklist, allowing information disclosure:
- `ir.config_parameter` — OAuth secrets, database UUID, feature flags
- `ir.module.module` — installed module enumeration
- `ir.actions.server` — executable server actions
- `base.automation` — automation workflows
- `ir.model.data` — XML IDs revealing internal structure

**Impact:** Information disclosure about system configuration.

**Fix implemented:** Added all five models to `BLOCKED_MODELS`.

**Breaking change for frontend?** VERIFY. If the frontend queries any of these models directly, those calls will now return `ACCESS_DENIED`. Review frontend code before deploying.

---

### P2-6: `sudo()` overuse in user management (reads)

**Status:** DONE (2026-04-09)

**File:** `controllers/simple_api.py` — `list_users`, `get_user`, `update_user`

**Problem:** See P2-2 above. All three endpoints used `.sudo()` unconditionally, bypassing Odoo's record rules for all users.

**Fix implemented:**
- `list_users`: `sudo()` now only used for admins. Managers use regular env with their scoped domain.
- `get_user`: `sudo()` only for admins. Other users go through Odoo's record rules.
- `update_user`: Permission check moved before `sudo()` call. `sudo()` retained for write (required by Odoo) but only after authorization is verified.

**Breaking change for frontend?** POSSIBLE. Manager-level user list may return fewer users. Test the user management UI with manager accounts.

---

## Fix Priority and Implementation Order

| Order | Finding | Effort | Breaking? | Frontend Changes? | Status |
|-------|---------|--------|-----------|-------------------|--------|
| 1 | P0-1: Block `api.session` from search | 5 min | No | None | DONE |
| 2 | P0-3: Add model blocklist | 30 min | Check first | Review if frontend queries blocked models | DONE |
| 3 | P1-1: Explicit AccessError handling | 1 hr | No (improves errors) | None | DONE |
| 4 | P2-1: Fix exception handling | 1 hr | Minimal | Update error code handling if needed | DONE |
| 5 | P0-2: Hash session tokens | 2 hr | No | None | DONE |
| 6 | P1-3: Cache-Control headers on credentials | 15 min | No | None | DONE |
| 7 | P1-2: Rate limiting on login | 1-2 hr | No (additive) | Handle 429 error code | DONE |
| 8 | P2-2: Remove sudo() from reads | 1 hr | Possible | Test user list for fewer results | DONE |
| 9 | P1-4: IDOR in update/delete | 30 min | No | None | DONE |
| 10 | P2-3: Error message info disclosure | 15 min | No | None | DONE |
| 11 | P2-4: Unbounded limit/offset | 30 min | Possible | Requests over 1000 now capped | DONE |
| 12 | P2-5: Expand model blocklist | 15 min | Possible | Verify no frontend queries to newly blocked models | DONE |
| 13 | P2-6: sudo() overuse in user reads | 30 min | Possible | Manager user list may show fewer results | DONE |

### Recommended deployment approach

All 13 findings are implemented. Frontend team should:
1. Handle `429 RATE_LIMITED` error code on login form and all API calls
2. Verify no frontend code queries newly blocked models (`ir.config_parameter`, `ir.module.module`, `ir.actions.server`, `base.automation`, `ir.model.data`)
3. Test manager-level user list (may return fewer users without sudo)
4. Note that `?limit=` is now capped at 1000

---

## Frontend Impact Summary

The client currently uses `session-token` header for all API calls. Here is the impact assessment:

| Fix | Frontend needs code changes? | Details |
|-----|------------------------------|---------|
| P0-1: Block api.session reads | **No** | Implemented. Frontend uses `session-token` header, never queries the model |
| P0-2: Hash session tokens | **No** | Implemented. Frontend sends raw token; backend hashes internally |
| P0-3: Model blocklist | **Verify** | Implemented. If frontend queries blocked models directly, those calls now fail with `ACCESS_DENIED` |
| P1-1: AccessError handling | **No** | Implemented. 403 instead of 500 for access errors |
| P1-2: Rate limiting | **Small** | Handle 429 status / `RATE_LIMITED` error code on login form and API calls |
| P1-3: Cache-Control headers | **No** | Implemented. Transparent to frontend |
| P1-4: IDOR in update/delete | **No** | Scope enforcement added. Out-of-scope records now return 404 instead of succeeding |
| P2-1: Exception handling | **Unlikely** | Implemented in generic endpoints; security denials now return explicit access errors |
| P2-2: Remove sudo() reads | **Test** | User list may return fewer results — verify UI handles this gracefully |
| P2-3: Error message sanitization | **No** | Generic messages returned; no frontend impact |
| P2-4: Limit cap (1000) | **Unlikely** | Only affects requests with `?limit=` > 1000 |
| P2-5: Expanded blocklist | **Verify** | Check if frontend queries `ir.config_parameter`, `ir.module.module`, etc. |

**Bottom line:** All findings are **done**. Frontend should handle 429 errors and verify no queries to newly blocked models.

---

## Validation

- Full API regression suite updated and executed after fixes.
- Result: **244 passed, 0 failed out of 244 tests** (as of 2026-03-21).
- Fixes 7-13 added 2026-04-09 — rerun test suite to validate.
