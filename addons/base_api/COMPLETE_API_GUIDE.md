# Odoo REST API v2 — Complete Documentation

**Version:** 2.0  
**Base URL:** `http://<host>:8069/api/v2`  
**Content-Type:** `application/json`  
**Module:** `base_api` (must be installed per database)

---

## Table of Contents

1. [Conventions](#1-conventions)
2. [Authentication](#2-authentication)
3. [Session Management](#3-session-management)
4. [User Management](#4-user-management)
5. [Generic CRUD](#5-generic-crud)
6. [Model & Field Discovery](#6-model--field-discovery)
7. [Partners & Contacts](#7-partners--contacts)
8. [Products](#8-products)
9. [Sales](#9-sales)
10. [CRM](#10-crm)
11. [HR & Employees](#11-hr--employees)
12. [Notifications & Messaging](#12-notifications--messaging)
13. [Settings & Configuration](#13-settings--configuration)
14. [Localization](#14-localization)
15. [Error Handling](#15-error-handling)
16. [Security & Permissions](#16-security--permissions)
17. [Appendix: Blocked Models](#17-appendix-blocked-models)
18. [Appendix: Common Odoo Models](#18-appendix-common-odoo-models)
19. [Analytics & Dashboards](#19-analytics--dashboards)

---

## 1. Conventions

### Standard Success Response

```json
{
  "success": true,
  "data": { ... },
  "message": "Human-readable description"
}
```

### Standard Error Response

```json
{
  "success": false,
  "error": {
    "message": "Human-readable error description",
    "code": "ERROR_CODE"
  }
}
```

### Pagination

Most list endpoints accept these query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Maximum records to return |
| `offset` | integer | `0` | Number of records to skip |

### Field Selection

Use the `fields` query parameter on `/search` and `/search/{model}/{id}` endpoints to request specific fields:

```
?fields=name,email,phone
```

If omitted, search returns `id`, `name`, `display_name`. Get-by-ID returns all fields.

### Dynamic Filtering

Any model field name can be used as a query parameter for exact-match filtering:

```
?partner_id=15&state=sale
```

When custom filter parameters are present, the default `active=true` filter is **not** applied. Only exact-match (`=`) filtering is supported; range and `ilike` queries are not available.

### Relational Field Format

Many-to-one fields are returned as `[id, "display_name"]`:

```json
"partner_id": [15, "Acme Corporation"],
"country_id": [235, "United States"]
```

### Authentication Headers

| Header | Used By | Description |
|--------|---------|-------------|
| `session-token` | Session auth | Token received from `/auth/login` |
| `api-key` | API key auth | Odoo native API key |

Endpoints that support both methods try session auth first, then fall back to API key.

---

## 2. Authentication

The API supports two authentication methods. **Session-based auth is recommended for frontend applications.**

### 2.1 Session-Based Authentication (Recommended)

Login with username/password to receive a session token. The token expires after 24 hours and can be refreshed.

**Flow:**  
`POST /auth/login` → receive `session_token` → include as `session-token` header → `POST /auth/refresh` to extend → `POST /auth/logout` to invalidate

### 2.2 API Key Authentication

Use an Odoo-native API key passed in the `api-key` header. API keys do not expire but can be revoked. Best suited for server-to-server integrations.

### 2.3 Endpoint Auth Support

| Auth Support | Endpoints |
|-------------|-----------|
| Session + API Key | `/search/*`, `/create/*`, `/update/*`, `/delete/*`, `/auth/me`, `/users/*`, `/groups`, `/models`, `/fields/*` |
| API Key only | `/partners`, `/products`, `/user/info`, `/auth/test` |
| No auth required | `/test` |

---

## 3. Session Management

### 3.1 Health Check

Check that the API module is installed and reachable. No authentication required.

```
GET /test
```

**Response:**

```json
{
  "success": true,
  "data": {
    "message": "API v2 is working!",
    "version": "2.0"
  },
  "message": "Basic test successful"
}
```

---

### 3.2 Login

Authenticate with username and password. Returns a session token and user profile.

```
POST /auth/login
```

**Request Body:**

```json
{
  "username": "admin",
  "password": "admin"
}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "session_token": "KTBXTNIvcLS6bpoQ4t8cDEu1BzGh03b1VxrFQRO5ON5FUVJg",
    "expires_at": "2026-03-23T15:56:46.326592",
    "user": {
      "id": 2,
      "name": "Mitchell Admin",
      "login": "admin",
      "email": "admin@yourcompany.example.com",
      "groups": ["Administrator", "Settings"]
    }
  },
  "message": "Login successful"
}
```

**Error codes:** `INVALID_CONTENT_TYPE`, `NO_DATA`, `INVALID_JSON`, `MISSING_CREDENTIALS`, `INVALID_CREDENTIALS`, `INACTIVE_USER`, `AUTH_FAILED`, `LOGIN_ERROR`

---

### 3.3 Refresh Session

Extend the session by 24 hours. Returns a **new** token (the old one is replaced). Can be called up to 1 hour after expiry (grace period).

```
POST /auth/refresh
```

**Headers:** `session-token: <current_token>`

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "session_token": "01WlpyLnbArHHiP5vO0KuW12v1NHTfM50nou04PqavY42e4b",
    "expires_at": "2026-03-24T16:11:59.947229",
    "user": {
      "id": 2,
      "name": "Mitchell Admin",
      "login": "admin",
      "email": "admin@yourcompany.example.com"
    }
  },
  "message": "Session refreshed successfully"
}
```

**Error codes:** `MISSING_SESSION_TOKEN`, `SESSION_NOT_REFRESHABLE`, `REFRESH_ERROR`

---

### 3.4 Logout

Invalidate the current session.

```
POST /auth/logout
```

**Headers:** `session-token: <token>`

**Response `200`:**

```json
{
  "success": true,
  "data": null,
  "message": "Logout successful"
}
```

---

### 3.5 Current User

Get the authenticated user's profile, groups, permission flags, and module access. Supports both session and API key auth.

The `module_access` map tells the frontend which navigation items to show/hide.

```
GET /auth/me
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2,
      "name": "Administrator",
      "login": "admin",
      "email": "admin@company.com",
      "active": true,
      "company_id": [1, "My Company"],
      "groups": [
        { "id": 24, "name": "Administrator" },
        { "id": 4, "name": "Role / Administrator" }
      ],
      "permissions": {
        "is_admin": true,
        "is_user": true,
        "can_manage_users": false
      },
      "module_access": {
        "crm":        { "accessible": true,  "label": "CRM",        "model": "crm.lead" },
        "sales":      { "accessible": true,  "label": "Sales",      "model": "sale.order" },
        "hr":         { "accessible": true,  "label": "Employees",  "model": "hr.employee" },
        "accounting": { "accessible": true,  "label": "Accounting", "model": "account.move" },
        "inventory":  { "accessible": true,  "label": "Inventory",  "model": "stock.picking" },
        "purchase":   { "accessible": true,  "label": "Purchase",   "model": "purchase.order" },
        "contacts":   { "accessible": true,  "label": "Contacts",   "model": "res.partner" },
        "products":   { "accessible": true,  "label": "Products",   "model": "product.template" },
        "project":    { "accessible": true,  "label": "Project",    "model": "project.project" },
        "calendar":   { "accessible": true,  "label": "Calendar",   "model": "calendar.event" }
      }
    }
  },
  "message": "User information retrieved"
}
```

**Example for a Sales-only user** (key differences):

```json
"module_access": {
  "crm":        { "accessible": true,  "label": "CRM",        "model": "crm.lead" },
  "sales":      { "accessible": true,  "label": "Sales",      "model": "sale.order" },
  "hr":         { "accessible": false, "label": "Employees",  "model": "hr.employee" },
  "accounting": { "accessible": true,  "label": "Accounting", "model": "account.move" },
  "inventory":  { "accessible": true,  "label": "Inventory",  "model": "stock.picking" },
  "purchase":   { "accessible": false, "label": "Purchase",   "model": "purchase.order" },
  "contacts":   { "accessible": true,  "label": "Contacts",   "model": "res.partner" },
  "products":   { "accessible": true,  "label": "Products",   "model": "product.template" },
  "project":    { "accessible": false, "label": "Project",    "model": "project.project" },
  "calendar":   { "accessible": true,  "label": "Calendar",   "model": "calendar.event" }
}
```

---

### 3.6 Module Access Check

Dedicated endpoint to check which functional modules the current user can access. Use this to show/hide navigation items, sidebar links, or dashboard tiles.

```
GET /modules/access
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user_id": 7,
    "module_access": {
      "crm":        { "accessible": true,  "label": "CRM",        "model": "crm.lead" },
      "sales":      { "accessible": true,  "label": "Sales",      "model": "sale.order" },
      "hr":         { "accessible": false, "label": "Employees",  "model": "hr.employee" },
      "accounting": { "accessible": true,  "label": "Accounting", "model": "account.move" },
      "inventory":  { "accessible": true,  "label": "Inventory",  "model": "stock.picking" },
      "purchase":   { "accessible": false, "label": "Purchase",   "model": "purchase.order" },
      "contacts":   { "accessible": true,  "label": "Contacts",   "model": "res.partner" },
      "products":   { "accessible": true,  "label": "Products",   "model": "product.template" },
      "project":    { "accessible": false, "label": "Project",    "model": "project.project" },
      "calendar":   { "accessible": true,  "label": "Calendar",   "model": "calendar.event" }
    }
  },
  "message": "Module access retrieved"
}
```

**Frontend usage pattern:**

```typescript
const { data } = await api.get('/modules/access');
const modules = data.module_access;

// Show/hide navigation items
const navItems = Object.entries(modules)
  .filter(([_, info]) => info.accessible)
  .map(([key, info]) => ({ key, label: info.label }));
```

**Module keys and what they check:**

| Key | Label | Checks Read Access To |
|-----|-------|-----------------------|
| `crm` | CRM | `crm.lead` |
| `sales` | Sales | `sale.order` |
| `hr` | Employees | `hr.employee` |
| `accounting` | Accounting | `account.move` |
| `inventory` | Inventory | `stock.picking` |
| `purchase` | Purchase | `purchase.order` |
| `contacts` | Contacts | `res.partner` |
| `products` | Products | `product.template` |
| `project` | Project | `project.project` |
| `calendar` | Calendar | `calendar.event` |

---

### 3.7 Test Auth (API Key Only)

Quick authentication validation. API key only.

```
GET /auth/test
```

**Headers:** `api-key: <key>`

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user_id": 2,
    "user_name": "Mitchell Admin",
    "user_login": "admin",
    "authenticated": true
  },
  "message": "Authentication test successful"
}
```

---

### 3.8 User Info (API Key Only)

Get current user info and API metadata. API key only.

```
GET /user/info
```

**Headers:** `api-key: <key>`

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2,
      "name": "Administrator",
      "login": "admin",
      "email": "admin@company.com",
      "active": true,
      "company_id": [1, "My Company"]
    },
    "api_version": "2.0",
    "database": "odoo19_db"
  },
  "message": "User information retrieved successfully"
}
```

---

## 4. User Management

All user management endpoints support both session and API key authentication. Permission levels vary by role.

### 4.1 List Users

```
GET /users
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Max records |
| `offset` | integer | `0` | Skip count |
| `search` | string | `""` | Search by name, login, or email |
| `active_only` | boolean | `true` | Only active users |

**Admin Response `200`** (includes groups, company, login_date):

```json
{
  "success": true,
  "data": {
    "users": [
      {
        "id": 2,
        "name": "Administrator",
        "login": "admin",
        "email": "admin@company.com",
        "active": true,
        "create_date": "2026-03-21T05:08:09.435235",
        "groups": ["Administrator", "Role / Administrator"],
        "company_id": "My Company",
        "login_date": "2026-03-21T22:47:42.502400"
      }
    ],
    "count": 4,
    "total_count": 4,
    "limit": 10,
    "offset": 0
  },
  "message": "Found 4 users"
}
```

**Non-admin Response `200`** (basic info only):

```json
{
  "success": true,
  "data": {
    "users": [
      {
        "id": 2,
        "name": "Administrator",
        "login": "admin",
        "email": "admin@company.com",
        "active": true,
        "create_date": "2026-03-21T05:08:09.435235"
      }
    ],
    "count": 4,
    "total_count": 4,
    "limit": 10,
    "offset": 0
  }
}
```

**Required permission:** `base.group_user` (Internal User)

---

### 4.2 Get User by ID

```
GET /users/{user_id}
```

Returns different detail levels based on who is requesting:

| Viewer | Fields Returned |
|--------|----------------|
| Admin viewing any user | id, name, email, active, create_date, login, phone, lang, tz, signature, company_id, **groups**, **company_ids**, **login_date** |
| User viewing own profile | id, name, email, active, create_date, login, phone, lang, tz, signature, company_id |
| User viewing other user | id, name, email, active, create_date |

**Admin Response `200` (viewing user 6):**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6,
      "name": "Regular User",
      "email": "user@test.com",
      "active": true,
      "create_date": "2026-03-21T05:09:22.242613",
      "login": "user@test.com",
      "phone": null,
      "lang": "en_US",
      "tz": null,
      "signature": "<div>Regular User</div>",
      "company_id": [1, "My Company"],
      "groups": [
        { "id": 1, "name": "Role / User", "full_name": "Role / User" }
      ],
      "company_ids": [{ "id": 1, "name": "My Company" }],
      "login_date": "2026-03-21T05:13:13.547871"
    }
  },
  "message": "User information retrieved"
}
```

---

### 4.3 Create User

```
POST /create/res.users
```

**Required permission:** Admin (`base.group_system` or `base.group_user_admin`)

**Request Body:**

```json
{
  "name": "John Smith",
  "login": "jsmith",
  "email": "john.smith@company.com",
  "group_names": ["User: Own Documents Only"],
  "auto_generate_credentials": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Full name |
| `login` | string | Yes | Username |
| `email` | string | No | Email address |
| `password` | string | No | Password (auto-generated if omitted and `auto_generate_credentials` is true) |
| `group_names` | string[] | No | Group names to assign |
| `group_ids` | integer[] | No | Group IDs to assign (alternative to `group_names`) |
| `auto_generate_credentials` | boolean | No | Default `true`. When true, generates a temporary password and API key |

**Response `201`:**

```json
{
  "success": true,
  "data": {
    "id": 15,
    "name": "John Smith",
    "login": "jsmith",
    "email": "john.smith@company.com",
    "groups": [{ "id": 22, "name": "User: Own Documents Only" }],
    "active": true,
    "create_date": "2026-03-22T10:00:00",
    "credentials": {
      "temporary_password": "xbsWYCnrYtJM",
      "api_key": "abc123def456...",
      "note": "Store these credentials securely - they won't be shown again"
    }
  },
  "message": "User created successfully with credentials"
}
```

---

### 4.4 Update User

```
PUT /users/{user_id}
```

**Editable fields by role:**

| Field | Self-edit | Admin-only |
|-------|----------|------------|
| `name` | Yes | Yes |
| `email` | Yes | Yes |
| `phone` | Yes | Yes |
| `signature` | Yes | Yes |
| `lang` | Yes | Yes |
| `tz` | Yes | Yes |
| `login` | No | Yes |
| `active` | No | Yes |
| `company_id` | No | Yes |
| `company_ids` | No | Yes |
| `group_names` / `group_ids` | No | Yes |

**Request Body:**

```json
{
  "name": "John Smith Updated",
  "phone": "+1-555-1234",
  "lang": "en_US"
}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6,
      "name": "John Smith Updated",
      "login": "jsmith",
      "email": "john@company.com",
      "phone": "+1-555-1234",
      "active": true,
      "lang": "en_US",
      "tz": null
    },
    "updated_fields": ["name", "phone"]
  },
  "message": "User updated successfully"
}
```

**Error `403`** (non-admin trying admin-only field):

```json
{
  "success": false,
  "error": {
    "message": "Access denied: Field 'login' requires admin rights",
    "code": "ADMIN_FIELD_ACCESS_DENIED"
  }
}
```

---

### 4.5 Change Password

```
PUT /users/{user_id}/password
```

| Scenario | Required Fields |
|----------|----------------|
| Changing own password (non-admin) | `old_password`, `new_password` |
| Admin changing any password | `new_password` |

**Request Body (own password):**

```json
{
  "old_password": "current_password",
  "new_password": "new_secure_password"
}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user_id": 6,
    "message": "Password changed successfully"
  },
  "message": "Password updated successfully"
}
```

---

### 4.6 Reset Password (Admin Only)

Generates a random temporary password for the user.

```
POST /users/{user_id}/reset-password
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user_id": 6,
    "temporary_password": "xbsWYCnrYtJM",
    "message": "Password has been reset. User should change it on first login."
  },
  "message": "Password reset successfully"
}
```

---

### 4.7 Generate API Key

Admin can generate for any user. Non-admin can generate for themselves only.

```
POST /users/{user_id}/api-key
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "user_id": 7,
    "user_name": "Sales User",
    "api_key": "416d662e83983a64ae607a034c7170072bc354cd",
    "note": "Store this API key securely - it will not be shown again"
  },
  "message": "API key generated successfully"
}
```

---

### 4.8 List User Groups (Admin Only)

Returns all assignable groups organized by category.

```
GET /groups
```

**Required permission:** `base.group_user_admin` or `base.group_system`

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "groups_by_category": {
      "Sales": [
        {
          "id": 24,
          "name": "Administrator",
          "full_name": "Sales / Administrator",
          "xml_id": "sales_team.group_sale_manager",
          "comment": "the user will have an access to the sales configuration as well as statistic reports.",
          "users_count": 1
        },
        {
          "id": 22,
          "name": "User: Own Documents Only",
          "full_name": "Sales / User: Own Documents Only",
          "xml_id": "sales_team.group_sale_salesman",
          "comment": "the user will have access to his own data in the sales application.",
          "users_count": 2
        }
      ],
      "Human Resources": [ ... ],
      "Accounting": [ ... ]
    },
    "total_groups": 17
  },
  "message": "Available groups retrieved"
}
```

---

## 5. Generic CRUD

These endpoints work with **any** Odoo model that the authenticated user has access to (except [blocked models](#17-appendix-blocked-models)). All support both session and API key auth.

### 5.1 Search Records

```
GET /search/{model}
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Max records |
| `offset` | integer | `0` | Skip count |
| `fields` | string | `"id,name,display_name"` | Comma-separated field names |
| `{field_name}` | any | — | Exact-match filter on any model field |

**Example:** `GET /search/res.partner?limit=5&fields=name,email,phone&is_company=true`

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 15,
        "name": "Acme Corporation",
        "email": "contact@acme.com",
        "phone": "+1-555-0123"
      }
    ],
    "count": 1,
    "model": "res.partner",
    "fields": ["id", "name", "email", "phone"],
    "total_count": 42
  },
  "message": "Found 1 records in res.partner"
}
```

**Error codes:** `MODEL_NOT_FOUND` (404), `ACCESS_DENIED` (403), `INVALID_FIELDS` (400), `SEARCH_ERROR` (400/500)

---

### 5.2 Get Record by ID

```
GET /search/{model}/{record_id}
```

Returns all fields by default. Use `?fields=` to limit.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fields` | string | all fields | Comma-separated field names |

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 15,
      "name": "Acme Corporation",
      "email": "contact@acme.com",
      "phone": "+1-555-0123",
      "country_id": [235, "United States"]
    },
    "model": "res.partner",
    "id": 15,
    "fields_returned": ["id", "name", "email", "phone", "country_id"],
    "total_fields_available": 127
  },
  "message": "Found record 15 in res.partner"
}
```

If the user lacks read access to some fields, the endpoint falls back to basic fields (`id`, `name`, `display_name`, `create_date`, `write_date`, `create_uid`, `write_uid`).

**Error codes:** `MODEL_NOT_FOUND` (404), `RECORD_NOT_FOUND` (404), `ACCESS_DENIED` (403)

---

### 5.3 Create Record

```
POST /create/{model}
```

**Headers:** `Content-Type: application/json`

**Request Body:** JSON object with field name/value pairs.

**Example — create a partner:**

```json
{
  "name": "Acme Corporation",
  "email": "contact@acme.com",
  "phone": "+1-555-0123",
  "is_company": true,
  "customer_rank": 1
}
```

**Response `201`:**

```json
{
  "success": true,
  "data": {
    "id": 42,
    "record": {
      "id": 42,
      "name": "Acme Corporation",
      "email": "contact@acme.com",
      "display_name": "Acme Corporation"
    }
  },
  "message": "Record created in res.partner"
}
```

For `res.users` creation, see [4.3 Create User](#43-create-user) for the special handling of groups and credentials.

**Error codes:** `INVALID_CONTENT_TYPE` (400), `NO_DATA` (400), `INVALID_JSON` (400), `MODEL_NOT_FOUND` (404), `ACCESS_DENIED` (403), `CREATE_ERROR` (400/500)

---

### 5.4 Update Record

```
PUT /update/{model}/{record_id}
```

**Headers:** `Content-Type: application/json`

**Request Body:** JSON object with fields to update.

```json
{
  "phone": "+1-555-9999",
  "email": "new@acme.com"
}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 42,
      "name": "Acme Corporation",
      "display_name": "Acme Corporation",
      "write_date": "2026-03-22 01:35:45.710210"
    },
    "updated_fields": ["phone", "email"]
  },
  "message": "Record 42 updated in res.partner"
}
```

**Error codes:** `INVALID_CONTENT_TYPE`, `NO_DATA`, `INVALID_JSON`, `MODEL_NOT_FOUND` (404), `RECORD_NOT_FOUND` (404), `ACCESS_DENIED` (403), `UPDATE_ERROR` (400/500)

---

### 5.5 Delete Record

```
DELETE /delete/{model}/{record_id}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "id": 42,
    "model": "res.partner"
  },
  "message": "Record 42 deleted from res.partner"
}
```

**Error codes:** `MODEL_NOT_FOUND` (404), `RECORD_NOT_FOUND` (404), `ACCESS_DENIED` (403), `DELETE_ERROR` (400/500)

---

### 5.6 Inventory Adjustment

Adjust inventory quantities for a product at a specific location. Creates proper stock moves via Odoo's inventory adjustment workflow.

```
POST /inventory/adjust
```

**Headers:** `Content-Type: application/json`

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_id` | integer | Yes | `product.product` ID |
| `new_quantity` | number | Yes | Target quantity |
| `location_id` | integer | No | Stock location ID (defaults to main warehouse stock location) |

**Example:**

```json
{
  "product_id": 23,
  "new_quantity": 50,
  "location_id": 8
}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "quant_id": 5,
    "old_quantity": 30,
    "new_quantity": 50,
    "diff": 20,
    "move": {
      "id": 42,
      "reference": "Product: Office Chair",
      "quantity": 20,
      "state": "done",
      "date": "2026-03-22 10:00:00",
      "location_id": [14, "Virtual Locations/Inventory Adjustment"],
      "location_dest_id": [8, "WH/Stock"]
    }
  },
  "message": "Inventory adjusted successfully"
}
```

If the quantity is unchanged, returns `diff: 0` and `move: null` with the message "No adjustment needed".

**Error codes:** `INVALID_CONTENT_TYPE` (400), `NO_DATA` (400), `INVALID_JSON` (400), `MISSING_FIELDS` (400), `PRODUCT_NOT_FOUND` (404), `NO_WAREHOUSE` (404), `LOCATION_NOT_FOUND` (404), `ACCESS_DENIED` (403), `INVENTORY_ADJUST_ERROR` (500)

---

## 6. Model & Field Discovery

### 6.1 List Accessible Models

Returns all models the authenticated user can read, excluding transient models and blocked models.

```
GET /models
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search` | string | `""` | Filter by model technical name or display name |
| `transient` | boolean | `false` | Include transient (wizard) models |

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "models": [
      {
        "model": "res.partner",
        "name": "Contact",
        "info": "",
        "field_count": 127
      },
      {
        "model": "crm.lead",
        "name": "Lead/Opportunity",
        "info": "",
        "field_count": 118
      }
    ],
    "count": 85
  },
  "message": "Found 85 accessible models"
}
```

---

### 6.2 Get Model Fields

Returns field metadata for a specific model.

```
GET /fields/{model}
```

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "model": "crm.lead",
    "fields": [
      {
        "name": "name",
        "description": "Opportunity",
        "type": "char",
        "required": true,
        "readonly": false,
        "help": "",
        "relation": "",
        "store": true
      },
      {
        "name": "partner_id",
        "description": "Customer",
        "type": "many2one",
        "required": false,
        "readonly": false,
        "help": "Linked partner",
        "relation": "res.partner",
        "store": true
      }
    ],
    "count": 118
  },
  "message": "Found 118 fields for model crm.lead"
}
```

---

## 7. Partners & Contacts

### 7.1 List Partners (Legacy Endpoint)

Convenience endpoint. API key auth only. For session auth compatibility, use `GET /search/res.partner` instead.

```
GET /partners
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Max records |
| `offset` | integer | `0` | Skip count |
| `customers_only` | boolean | `true` | Only partners with `customer_rank > 0` |

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "partners": [
      {
        "id": 15,
        "name": "Acme Corporation",
        "email": "contact@acme.com",
        "phone": "+1-555-0123",
        "is_company": true,
        "customer_rank": 1,
        "city": "Business City",
        "country": "United States"
      }
    ],
    "count": 1,
    "total_count": 42
  },
  "message": "Partners retrieved successfully"
}
```

### 7.2 Partner CRUD via Generic Endpoints

```
GET    /search/res.partner?fields=name,email,phone,city,country_id&is_company=true
GET    /search/res.partner/15?fields=name,email,phone,street,city,zip,country_id
POST   /create/res.partner
PUT    /update/res.partner/15
DELETE /delete/res.partner/15
```

**Create Partner Example:**

```json
{
  "name": "Acme Corporation",
  "email": "contact@acme.com",
  "phone": "+1-555-0123",
  "is_company": true,
  "customer_rank": 1,
  "street": "123 Business Ave",
  "city": "Business City",
  "zip": "12345"
}
```

**Key fields:** `name`, `email`, `phone`, `mobile`, `street`, `street2`, `city`, `zip`, `state_id`, `country_id`, `website`, `is_company`, `customer_rank`, `supplier_rank`, `vat`, `lang`, `tz`

---

## 8. Products

### 8.1 List Products (Legacy Endpoint)

Convenience endpoint. API key auth only. For session auth compatibility, use `GET /search/product.template` instead.

```
GET /products
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Max records |
| `offset` | integer | `0` | Skip count |
| `sale_ok` | boolean | `true` | Only saleable products |

**Response `200`:**

```json
{
  "success": true,
  "data": {
    "products": [
      {
        "id": 23,
        "name": "Office Chair",
        "default_code": "FURN_0001",
        "list_price": 295.00,
        "sale_ok": true,
        "category": "Office Furniture"
      }
    ],
    "count": 5,
    "total_count": 12
  },
  "message": "Products retrieved successfully"
}
```

### 8.2 Product CRUD via Generic Endpoints

```
GET    /search/product.template?fields=name,list_price,default_code,sale_ok,categ_id
GET    /search/product.template/23?fields=name,list_price,default_code,description
POST   /create/product.template
PUT    /update/product.template/23
DELETE /delete/product.template/23
```

**Create Product Example:**

```json
{
  "name": "API Product",
  "list_price": 99.99,
  "default_code": "API001",
  "sale_ok": true,
  "purchase_ok": true,
  "type": "consu"
}
```

**Key fields:** `name`, `default_code`, `list_price`, `standard_price`, `categ_id`, `sale_ok`, `purchase_ok`, `type`, `description`, `description_sale`, `image_1920`

**Related models:** `product.product` (variants), `product.category`, `product.attribute`, `product.pricelist`

---

## 9. Sales

All sales operations use the generic CRUD endpoints.

### 9.1 Sales Orders

```
GET    /search/sale.order?fields=name,partner_id,amount_total,state,date_order
GET    /search/sale.order/5?fields=name,partner_id,order_line,amount_total,state
POST   /create/sale.order
PUT    /update/sale.order/5
DELETE /delete/sale.order/5
```

**Create Sales Order Example:**

```json
{
  "partner_id": 15,
  "order_line": [
    [0, 0, {
      "product_id": 23,
      "product_uom_qty": 2,
      "price_unit": 295.00
    }]
  ]
}
```

**Key fields:** `name`, `partner_id`, `date_order`, `state`, `amount_total`, `amount_tax`, `amount_untaxed`, `user_id`, `team_id`, `order_line`, `invoice_status`, `pricelist_id`, `payment_term_id`

**Order states:** `draft` (Quotation), `sent` (Quotation Sent), `sale` (Sales Order), `done` (Locked), `cancel` (Cancelled)

**Related models:** `sale.order.line`

---

## 10. CRM

### 10.1 Leads & Opportunities

```
GET    /search/crm.lead?fields=name,partner_name,email_from,expected_revenue,stage_id,user_id
GET    /search/crm.lead/11?fields=name,partner_id,expected_revenue,stage_id,activity_ids,message_ids
POST   /create/crm.lead
PUT    /update/crm.lead/11
DELETE /delete/crm.lead/11
```

**Create Lead Example:**

```json
{
  "name": "New Business Opportunity",
  "partner_name": "Potential Customer",
  "email_from": "potential@customer.com",
  "phone": "+1-555-0199",
  "expected_revenue": 5000.00
}
```

**Key fields:** `name`, `partner_name`, `email_from`, `phone`, `expected_revenue`, `probability`, `stage_id`, `user_id`, `team_id`, `partner_id`, `date_deadline`, `priority`, `type`, `source_id`, `medium_id`, `campaign_id`, `activity_ids`, `message_ids`

**Related models:** `crm.stage`, `crm.team`, `crm.tag`, `crm.lost.reason`

### 10.2 CRM Stages

```
GET /search/crm.stage?fields=name,sequence,is_won
```

---

## 11. HR & Employees

### 11.1 Employees

```
GET    /search/hr.employee?fields=name,work_email,department_id,job_id,manager_id
GET    /search/hr.employee/1?fields=name,work_email,department_id,job_id,work_phone
POST   /create/hr.employee
PUT    /update/hr.employee/1
DELETE /delete/hr.employee/1
```

**Create Employee Example:**

```json
{
  "name": "Jane Doe",
  "work_email": "jane.doe@company.com",
  "department_id": 1,
  "job_id": 1
}
```

**Key fields:** `name`, `work_email`, `work_phone`, `mobile_phone`, `department_id`, `job_id`, `manager_id`, `company_id`, `employee_type`, `active`

### 11.2 Related Models

```
GET /search/hr.department?fields=name,manager_id,company_id
GET /search/hr.job?fields=name,department_id,no_of_recruitment
```

---

## 12. Notifications & Messaging

Odoo's messaging system is built on several interconnected models. All are accessible through the generic CRUD endpoints.

### 12.1 Messages (`mail.message`)

Messages power the chatter, inbox, and notification system.

```
GET /search/mail.message?fields=subject,body,author_id,date,message_type,model,res_id,record_name,starred,needaction,is_internal
```

**Useful Filters:**

```
GET /search/mail.message?needaction=true&fields=...         # Inbox items
GET /search/mail.message?starred=true&fields=...            # Starred messages
GET /search/mail.message?model=res.partner&res_id=9&fields=... # Messages on a specific record
GET /search/mail.message?message_type=comment&fields=...    # User-posted comments
```

**Key fields:**

| Field | Type | Description |
|-------|------|-------------|
| `subject` | char | Message subject |
| `body` | html | Message content (HTML) |
| `preview` | char | Plain text preview |
| `author_id` | many2one | Author `[id, "name"]` |
| `date` | datetime | Message date |
| `message_type` | selection | `email`, `comment`, `notification`, `user_notification`, `auto_comment` |
| `model` | char | Related model (e.g. `res.partner`) |
| `res_id` | integer | Related record ID |
| `record_name` | char | Related record display name |
| `subtype_id` | many2one | Message subtype `[id, "name"]` |
| `is_internal` | boolean | Internal/employee-only |
| `starred` | boolean | Starred by current user |
| `needaction` | boolean | Pending action for current user (inbox item) |
| `notification_ids` | one2many | Per-recipient notification records |
| `partner_ids` | many2many | Explicit recipients |
| `attachment_ids` | many2many | File attachments |
| `parent_id` | many2one | Parent message (threading) |

**Post a message on a record:**

```
POST /create/mail.message
```

```json
{
  "body": "<p>Important update about this customer</p>",
  "message_type": "comment",
  "model": "res.partner",
  "res_id": 9,
  "subtype_id": 1
}
```

Subtype IDs: `1` = Discussions (visible to followers), `2` = Note (internal only)

**Permissions:** Creating messages requires write access to the target model.

---

### 12.2 Activities (`mail.activity`)

Scheduled tasks/reminders assigned to users on specific records (calls, meetings, to-dos).

```
GET /search/mail.activity?fields=summary,note,date_deadline,user_id,activity_type_id,res_model,res_id,res_name,state
```

**Useful Filters:**

```
GET /search/mail.activity?user_id=3&fields=...              # My activities
GET /search/mail.activity?res_model=crm.lead&fields=...     # Activities on CRM leads
GET /search/mail.activity?res_model=crm.lead&res_id=11&fields=... # Activities on a specific lead
```

**Note:** The `state` field (`overdue`, `today`, `planned`) is a computed field. It may not work reliably as a search filter in all cases.

**Key fields:**

| Field | Type | Description |
|-------|------|-------------|
| `summary` | char | Activity title |
| `note` | html | Detailed description |
| `date_deadline` | date | Due date |
| `date_done` | date | Completion date |
| `state` | selection | `overdue`, `today`, `planned` (computed) |
| `user_id` | many2one | Assigned user `[id, "name"]` |
| `activity_type_id` | many2one | Type `[id, "Call"]` |
| `res_model` | char | Related model name |
| `res_model_id` | many2one | Related model (from `ir.model`) |
| `res_id` | integer | Related record ID |
| `res_name` | char | Related record display name |
| `feedback` | text | Completion feedback |
| `can_write` | boolean | Whether current user can edit |
| `icon` | char | FontAwesome icon (e.g. `fa-phone`) |

**Create an activity:**

```
POST /create/mail.activity
```

**Important:** You need `res_model_id` (the ID from `ir.model`), not just the model name string.

Step 1 — Look up the model ID:

```
GET /search/ir.model?model=crm.lead&fields=id,model
```

Step 2 — Create the activity:

```json
{
  "summary": "Follow up with client",
  "note": "<p>Call to discuss contract terms</p>",
  "activity_type_id": 2,
  "date_deadline": "2026-03-25",
  "user_id": 2,
  "res_model_id": 512,
  "res_id": 11
}
```

**Activity Type IDs:**

| ID | Name | Icon | Category |
|----|------|------|----------|
| 1 | Email | `fa-envelope` | default |
| 2 | Call | `fa-phone` | phonecall |
| 3 | Meeting | `fa-users` | meeting |
| 4 | To-Do | `fa-check` | default |
| 5 | Document | `fa-upload` | upload_file |

**Update and delete:**

```
PUT    /update/mail.activity/1  → {"summary": "Updated summary"}
DELETE /delete/mail.activity/1
```

**Permissions:** Users can only update/delete their own activities unless they are admin.

---

### 12.3 Notifications (`mail.notification`)

Per-recipient delivery tracking for messages.

```
GET /search/mail.notification?fields=mail_message_id,res_partner_id,notification_type,notification_status,is_read,read_date,failure_type,failure_reason
```

**Useful Filters:**

```
GET /search/mail.notification?is_read=false&fields=...              # Unread
GET /search/mail.notification?notification_status=exception&fields=... # Failed
```

**Key fields:**

| Field | Type | Description |
|-------|------|-------------|
| `mail_message_id` | many2one | The message `[id, "subject"]` |
| `res_partner_id` | many2one | Recipient `[id, "name"]` |
| `notification_type` | selection | `inbox` or `email` |
| `notification_status` | selection | `ready`, `sent`, `bounce`, `exception`, `canceled` |
| `is_read` | boolean | Read status |
| `read_date` | datetime | When read |
| `failure_type` | selection | `mail_smtp`, `mail_email_invalid`, etc. |
| `failure_reason` | text | Failure details |

---

### 12.4 Followers (`mail.followers`)

Tracks who is subscribed to which record and what notification subtypes they receive.

```
GET /search/mail.followers?fields=partner_id,res_model,res_id,name,email,subtype_ids
GET /search/mail.followers?res_model=crm.lead&res_id=11&fields=partner_id,name,email,subtype_ids
```

---

### 12.5 Activity Types (`mail.activity.type`)

```
GET /search/mail.activity.type?fields=name,summary,icon,category,delay_count,delay_unit,res_model,chaining_type
```

---

### 12.6 Message Subtypes (`mail.message.subtype`)

```
GET /search/mail.message.subtype?fields=name,description,internal,hidden,default,res_model,sequence
```

**Common subtypes:** `1` Discussions, `2` Note, `3` Activities, `9` Stage Changed, `10` Opportunity Won, `23` Sale Order Confirmed

---

### 12.7 Common Workflows

**Notification Inbox:**

```
1. GET /search/mail.message?needaction=true&fields=subject,body,author_id,date,message_type,model,res_id,record_name
2. GET /search/mail.notification?is_read=false&fields=mail_message_id,res_partner_id,notification_status
3. GET /search/mail.message?starred=true&fields=subject,body,author_id,date,model,res_id,record_name
```

**Activity Dashboard:**

```
1. GET /search/mail.activity?user_id={my_id}&fields=summary,date_deadline,activity_type_id,res_model,res_name,state
2. Group/sort by state (overdue → today → planned) and by res_model
```

**Record Chatter:**

```
1. GET /search/mail.message?model=res.partner&res_id=9&fields=subject,body,author_id,date,message_type,starred
2. GET /search/mail.followers?res_model=res.partner&res_id=9&fields=partner_id,name,email
3. GET /search/mail.activity?res_model=res.partner&res_id=9&fields=summary,date_deadline,user_id,activity_type_id,state
```

---

## 13. Settings & Configuration

Most settings endpoints are **admin-only**. Non-admin users receive `ACCESS_DENIED` (403).

### 13.1 System Configuration (`res.config.settings`)

```
GET /search/res.config.settings                              # List (admin only)
GET /search/res.config.settings/1?fields=company_name,...    # Full detail (249 fields)
PUT /update/res.config.settings/1                            # Update (admin only)
```

**Useful fields to query:**

```
?fields=company_name,currency_id,sale_tax_id,purchase_tax_id,auth_signup_reset_password,show_effect,active_user_count,company_country_code
```

### 13.2 System Parameters (`ir.config_parameter`)

Key-value configuration store. Admin only.

```
GET /search/ir.config_parameter?fields=key,value
PUT /update/ir.config_parameter/9  → {"value": "True"}
```

### 13.3 Company Settings (`res.company`)

Readable by all users. Writable by admin only.

```
GET /search/res.company/1?fields=name,currency_id,country_id,street,city,zip,phone,email,website,vat
PUT /update/res.company/1  → {"phone": "+1-555-0100"}    # admin only
```

### 13.4 Settings Field Metadata

```
GET /fields/res.config.settings    # Admin only, returns 249 fields
```

### 13.5 Discover Settings Models

```
GET /models?search=config    # Admin sees 3 models, regular users see 1
```

### 13.6 Access Matrix

| Endpoint | Admin | Internal User |
|----------|-------|---------------|
| `GET /search/res.config.settings` | Full | Denied |
| `PUT /update/res.config.settings/{id}` | Full | Denied |
| `GET /search/ir.config_parameter` | Full | Denied |
| `PUT /update/ir.config_parameter/{id}` | Full | Denied |
| `GET /search/res.company/{id}` | Full | Read-only |
| `PUT /update/res.company/{id}` | Full | Denied |

---

## 14. Localization

The API provides access to Odoo's localization modules for country-specific tax systems, charts of accounts, and compliance rules.

### 14.1 Taxes by Country

```
GET /search/account.tax?fields=name,amount,type_tax_use,country_id,description&country_id=197
```

### 14.2 Chart of Accounts

```
GET /search/account.account?fields=code,name,account_type&country_id=115
```

### 14.3 Fiscal Positions

```
GET /search/account.fiscal.position?fields=name,country_id,auto_apply,sequence
```

### 14.4 Currencies

```
GET /search/res.currency?fields=name,symbol,position,active
GET /search/res.currency.rate?fields=name,rate,currency_id,company_id
```

### 14.5 Countries

```
GET /search/res.country?fields=name,code,currency_id
```

---

## 15. Error Handling

### 15.1 HTTP Status Codes

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `201` | Created (for `POST /create/*`) |
| `400` | Bad request (invalid input, validation error) |
| `401` | Authentication required or failed |
| `403` | Access denied (insufficient permissions) |
| `404` | Model or record not found |
| `500` | Internal server error |

### 15.2 Error Response Format

```json
{
  "success": false,
  "error": {
    "message": "Human-readable error description",
    "code": "ERROR_CODE"
  }
}
```

### 15.3 Error Code Reference

**Authentication Errors:**

| Code | Status | Description |
|------|--------|-------------|
| `MISSING_API_KEY` | 401 | No `api-key` header provided |
| `INVALID_API_KEY` | 403 | API key is invalid |
| `INACTIVE_USER` | 403 | User account is deactivated |
| `AUTH_ERROR` | 500 | Unexpected authentication error |
| `MISSING_SESSION_TOKEN` | 401 | No `session-token` header provided |
| `INVALID_SESSION` | 401 | Session token is invalid or expired |
| `SESSION_AUTH_ERROR` | 500 | Unexpected session auth error |
| `INVALID_CONTENT_TYPE` | 400 | Content-Type must be `application/json` |
| `MISSING_CREDENTIALS` | 400 | Username or password missing |
| `INVALID_CREDENTIALS` | 401 | Wrong username/password |
| `AUTH_FAILED` | 401 | Authentication failed |
| `LOGIN_ERROR` | 500 | Unexpected login error |

**Session Errors:**

| Code | Status | Description |
|------|--------|-------------|
| `SESSION_NOT_REFRESHABLE` | 401 | Session expired beyond 1-hour grace period |
| `REFRESH_ERROR` | 500 | Unexpected refresh error |
| `LOGOUT_ERROR` | 500 | Unexpected logout error |

**CRUD Errors:**

| Code | Status | Description |
|------|--------|-------------|
| `MODEL_NOT_FOUND` | 404 | Model does not exist |
| `RECORD_NOT_FOUND` | 404 | Record does not exist |
| `ACCESS_DENIED` | 403 | User lacks permission |
| `INVALID_FIELDS` | 400 | No valid field names provided |
| `NO_DATA` | 400 | Request body is empty |
| `INVALID_JSON` | 400 | Request body is not valid JSON |
| `SEARCH_ERROR` | 400/500 | Error during search |
| `CREATE_ERROR` | 400/500 | Error during create |
| `UPDATE_ERROR` | 400/500 | Error during update |
| `DELETE_ERROR` | 400/500 | Error during delete |
| `GET_RECORD_ERROR` | 400/500 | Error getting record by ID |

**User Management Errors:**

| Code | Status | Description |
|------|--------|-------------|
| `USER_NOT_FOUND` | 404 | Target user does not exist |
| `ADMIN_FIELD_ACCESS_DENIED` | 403 | Non-admin trying to update admin-only field |
| `NO_VALID_FIELDS` | 400 | No updatable fields in request |
| `MISSING_PASSWORD` | 400 | `new_password` not provided |
| `MISSING_OLD_PASSWORD` | 400 | `old_password` required for own password change |
| `INVALID_OLD_PASSWORD` | 401 | Old password is incorrect |
| `USER_CREATE_ERROR` | 500 | Error creating user |
| `USER_UPDATE_ERROR` | 500 | Error updating user |
| `PASSWORD_CHANGE_ERROR` | 500 | Error changing password |
| `PASSWORD_RESET_ERROR` | 500 | Error resetting password |
| `API_KEY_GENERATION_ERROR` | 500 | Error generating API key |

---

## 16. Security & Permissions

### 16.1 Role-Based Access Control

The API enforces Odoo's native security model:

1. **Model-level ACLs** — determines which models a user can read/write/create/delete
2. **Record-level rules** — filters which specific records a user can see
3. **Group membership** — determines all capabilities

### 16.2 Permission Matrix — User Management

| Action | Admin | Internal User |
|--------|-------|---------------|
| List users | All fields + groups | Basic fields only |
| View own profile | Full | Extended (login, phone, lang, tz, company) |
| View other user | Full | Basic (name, email, active, create_date) |
| Update own profile | All fields | name, email, phone, signature, lang, tz |
| Update other user | All fields | Denied |
| Change own password | Yes | Yes (requires `old_password`) |
| Change other password | Yes | Denied |
| Reset password | Yes | Denied |
| Generate own API key | Yes | Yes |
| Generate other API key | Yes | Denied |
| Create user | Yes | Denied |
| List groups | Yes | Denied |

### 16.3 Permission Matrix — Data Models

| Model | Admin | Internal User | Sales User |
|-------|-------|---------------|------------|
| `res.partner` | Full | Full | Full |
| `product.template` | Full | Read | Read |
| `sale.order` | Full | Depends on groups | Own documents |
| `crm.lead` | Full | Depends on groups | Own leads |
| `hr.employee` | Full | Denied (unless HR group) | Denied |
| `account.move` | Full | Denied (unless Accounting group) | Read |
| `res.config.settings` | Full | Denied | Denied |
| `ir.config_parameter` | Full | Denied | Denied |
| `res.company` | Full | Read-only | Read-only |

### 16.4 Blocked Models

These models cannot be accessed via the generic CRUD endpoints regardless of user permissions:

- `api.session`
- `ir.cron`
- `ir.rule`
- `ir.model.access`
- `res.users.apikeys`
- `ir.attachment`
- `base.module.update`

### 16.5 Session Security

- Session tokens are 48 characters, cryptographically random
- Tokens are stored as SHA-256 hashes (never in plaintext)
- Sessions expire after 24 hours
- Refresh is allowed up to 1 hour after expiry
- Refreshing issues a new token and invalidates the old one
- Responses containing credentials include `Cache-Control: no-store` headers
- `last_activity` is updated on each authenticated request

---

## 17. Appendix: Blocked Models

The following models are blocked from all generic CRUD endpoints (`/search`, `/create`, `/update`, `/delete`). Accessing them returns `403 ACCESS_DENIED`.

| Model | Reason |
|-------|--------|
| `api.session` | Contains hashed session tokens |
| `ir.cron` | Scheduled actions (code execution risk) |
| `ir.rule` | Security rules (information disclosure) |
| `ir.model.access` | ACL definitions (information disclosure) |
| `res.users.apikeys` | API key hashes |
| `ir.attachment` | File system access |
| `base.module.update` | Module installation control |

---

## 18. Appendix: Common Odoo Models

### Core

| Model | Description |
|-------|-------------|
| `res.partner` | Contacts, customers, suppliers |
| `res.users` | System users |
| `res.company` | Companies |
| `res.country` | Countries |
| `res.currency` | Currencies |
| `res.groups` | User groups |
| `res.lang` | Languages |

### CRM

| Model | Description |
|-------|-------------|
| `crm.lead` | Leads and opportunities |
| `crm.stage` | Pipeline stages |
| `crm.team` | Sales teams |
| `crm.tag` | CRM tags |
| `crm.lost.reason` | Lost opportunity reasons |

### Sales

| Model | Description |
|-------|-------------|
| `sale.order` | Sales orders / quotations |
| `sale.order.line` | Order line items |

### Products

| Model | Description |
|-------|-------------|
| `product.template` | Product templates |
| `product.product` | Product variants |
| `product.category` | Product categories |
| `product.attribute` | Product attributes (size, color, etc.) |
| `product.pricelist` | Pricelists |

### Accounting

| Model | Description |
|-------|-------------|
| `account.move` | Journal entries, invoices, bills |
| `account.move.line` | Journal entry lines |
| `account.payment` | Payments |
| `account.account` | Chart of accounts |
| `account.journal` | Journals |
| `account.tax` | Taxes |
| `account.fiscal.position` | Fiscal positions |

### HR

| Model | Description |
|-------|-------------|
| `hr.employee` | Employees |
| `hr.department` | Departments |
| `hr.job` | Job positions |
| `hr.contract` | Employment contracts |
| `hr.contract.type` | Contract types |

### Messaging & Activities

| Model | Description |
|-------|-------------|
| `mail.message` | Messages, notes, notifications |
| `mail.notification` | Per-recipient delivery tracking |
| `mail.activity` | Scheduled tasks / reminders |
| `mail.activity.type` | Activity type definitions |
| `mail.followers` | Record subscriptions |
| `mail.message.subtype` | Notification categories |

### Inventory

| Model | Description |
|-------|-------------|
| `stock.picking` | Transfers / deliveries |
| `stock.move` | Stock movements |
| `stock.quant` | Stock quantities |
| `stock.location` | Stock locations |
| `stock.warehouse` | Warehouses |
| `stock.lot` | Lots / serial numbers |

### Calendar

| Model | Description |
|-------|-------------|
| `calendar.event` | Calendar events / meetings |
| `calendar.attendee` | Event attendees |

### System

| Model | Description |
|-------|-------------|
| `ir.model` | Database models (for discovery) |
| `ir.model.fields` | Model field metadata |
| `ir.config_parameter` | System key-value parameters |
| `res.config.settings` | System configuration |

---

## 19. Analytics & Dashboards

All analytics endpoints are **permission-aware** — only data the authenticated user has access to is returned. Each endpoint supports time windows, optional filters, and returns a consistent response shape.

**Authentication:** Session token only (header: `session-token: <token>`). API keys are not accepted for analytics endpoints.

### 19.1 Common Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from` | `YYYY-MM-DD` | First day of current month | Start of period |
| `to` | `YYYY-MM-DD` | Today | End of period (inclusive) |
| `timezone` | String | `UTC` | IANA timezone for period boundaries |
| `company_id` | Integer | — | Filter by company |
| `team_id` | Integer | — | Filter by sales/CRM team |
| `owner_id` | Integer | — | Filter by responsible user |

**Previous period** is auto-calculated: same duration, shifted back immediately before `from`. For example, if `from=2026-03-01&to=2026-03-31`, the previous period is `2026-01-29` to `2026-02-28`.

### 19.2 Standard Analytics Response Shape

Every analytics endpoint returns the same structure:

```json
{
  "success": true,
  "data": {
    "kpis": {
      "<kpi_name>": {
        "current": 42,
        "previous": 35,
        "delta": 7,
        "delta_percent": 20.0
      }
    },
    "breakdowns": {
      "by_<dimension>": [
        { "id": 1, "label": "Category A", "count": 15, "value": 25000.00 }
      ]
    },
    "chart": {
      "labels": ["January 2026", "February 2026", "March 2026"],
      "series": [
        { "label": "Count", "data": [10, 15, 17] },
        { "label": "amount_total", "data": [5000, 8000, 12000] }
      ]
    },
    "alerts": [
      {
        "type": "warning|danger|info",
        "title": "Overdue activities",
        "message": "5 overdue follow-ups on leads",
        "count": 5
      }
    ],
    "meta": {
      "generated_at": "2026-03-22T14:30:00.000000",
      "period": {
        "from": "2026-03-01",
        "to": "2026-03-31",
        "previous_from": "2026-01-29",
        "previous_to": "2026-02-28"
      },
      "period_label": "2026-03-01 to 2026-03-31",
      "timezone": "UTC"
    }
  },
  "message": "..."
}
```

**KPI fields:**
- `current` — value for the requested period
- `previous` — value for the auto-calculated previous period
- `delta` — `current - previous`
- `delta_percent` — percentage change (100.0 if previous is 0 and current > 0)
- Snapshot KPIs (e.g. `total_employees`) set `previous`, `delta`, `delta_percent` to `null`

**Alert types:** `danger` (requires action), `warning` (attention needed), `info` (informational)

### 19.3 Dashboard Summary

```
GET /api/v2/analytics/dashboard/summary
```

Cross-module overview returning key KPIs from every module the user has access to.

**Auth:** Session token

**Response `data.kpis`** — keyed by module:

```json
{
  "kpis": {
    "crm": {
      "total_leads": { "current": 42, "previous": 35, "delta": 7, "delta_percent": 20.0 },
      "expected_revenue": { "current": 150000, "previous": 120000, "delta": 30000, "delta_percent": 25.0 }
    },
    "sales": {
      "total_orders": { "current": 18, "previous": 12, "delta": 6, "delta_percent": 50.0 },
      "total_revenue": { "current": 95000, "previous": 78000, "delta": 17000, "delta_percent": 21.8 }
    },
    "invoicing": { ... },
    "inventory": { ... },
    "purchase": { ... },
    "hr": {
      "total_employees": { "current": 45, "previous": null, "delta": null, "delta_percent": null },
      "new_hires": { "current": 3, "previous": 1, "delta": 2, "delta_percent": 200.0 }
    },
    "project": { ... }
  },
  "accessible_modules": ["crm", "sales", "invoicing", "inventory", "purchase", "hr", "project"],
  "alerts": [ ... ],
  "meta": { ... }
}
```

Modules where the underlying Odoo app is not installed or the user lacks read access are **omitted** from the response.

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/dashboard/summary?from=2026-03-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.4 CRM Overview

```
GET /api/v2/analytics/crm/overview
```

**Auth:** Session token  
**Model:** `crm.lead`  
**Date field:** `create_date`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_leads` | Count of leads/opportunities created |
| `expected_revenue` | Sum of expected revenue |
| `won` | Count of won opportunities |
| `win_rate` | Win percentage (won / total × 100) |

**Breakdowns:** `by_stage` — pipeline stages with count and revenue per stage

**Chart series:** Monthly lead count and revenue

**Alerts:** Overdue CRM activities (via `mail.activity`)

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/crm/overview?from=2026-01-01&to=2026-03-31&team_id=1' \
  -H "session-token: <token>"
```

### 19.5 Sales Overview

```
GET /api/v2/analytics/sales/overview
```

**Auth:** Session token  
**Model:** `sale.order`  
**Date field:** `date_order`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_orders` | Count of all sale orders |
| `total_revenue` | Sum of `amount_total` |
| `avg_order_value` | Average order value |
| `confirmed_orders` | Orders in state `sale` |
| `draft_quotations` | Orders in state `draft` |

**Breakdowns:** `by_state` — order states with revenue per state

**Chart series:** Monthly order count and revenue

**Alerts:** Overdue activities; pending quotations if > 5 drafts

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/sales/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.6 Invoicing Overview

```
GET /api/v2/analytics/invoicing/overview
```

**Auth:** Session token  
**Model:** `account.move` (filtered to `out_invoice` and `out_refund`)  
**Date field:** `invoice_date`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_invoices` | Count of customer invoices/credit notes |
| `total_amount` | Sum of `amount_total` |
| `amount_paid` | Total amount paid (total - residual) |
| `amount_due` | Total outstanding (sum of `amount_residual` on posted invoices) |

**Breakdowns:** `by_payment_state` — `not_paid`, `in_payment`, `paid`, `partial`, `reversed`

**Chart series:** Monthly invoice count and amount

**Alerts:** Overdue invoices (posted, not paid, past `invoice_date_due`)

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/invoicing/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.7 Inventory Overview

```
GET /api/v2/analytics/inventory/overview
```

**Auth:** Session token  
**Model:** `stock.picking`  
**Date field:** `scheduled_date`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_transfers` | Count of transfers in period |
| `done` | Completed transfers |
| `waiting` | Transfers in `waiting`, `confirmed`, or `assigned` |
| `late` | Transfers past scheduled date and not done/cancelled (snapshot, no delta) |

**Breakdowns:** `by_state` — `draft`, `waiting`, `confirmed`, `assigned`, `done`, `cancel`

**Chart series:** Monthly transfer count

**Alerts:** Late transfers past scheduled date

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/inventory/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.8 Purchases Overview

```
GET /api/v2/analytics/purchases/overview
```

**Auth:** Session token  
**Model:** `purchase.order`  
**Date field:** `date_order`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_orders` | Count of purchase orders |
| `total_amount` | Sum of `amount_total` |
| `draft` | Draft/RFQ count |
| `confirmed` | Confirmed POs (state `purchase`) |

**Breakdowns:** `by_state` — states with amount per state

**Chart series:** Monthly PO count and amount

**Alerts:** Pending RFQs if > 3 drafts

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/purchases/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.9 HR Overview

```
GET /api/v2/analytics/hr/overview
```

**Auth:** Session token  
**Model:** `hr.employee`  
**Date field:** `create_date` (for new hires)

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_employees` | Current headcount snapshot (no delta) |
| `new_hires` | Employees created in period (with delta) |
| `departments` | Total department count snapshot (no delta) |

**Breakdowns:** `by_department` — active employees per department

**Chart series:** Monthly new hire count

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/hr/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

### 19.10 Projects Overview

```
GET /api/v2/analytics/projects/overview
```

**Auth:** Session token  
**Model:** `project.task`  
**Date field:** `create_date`

**KPIs:**
| KPI | Description |
|-----|-------------|
| `total_tasks` | Tasks created in period |
| `closed` | Tasks in folded (done) stages |
| `overdue` | Tasks past deadline and not closed (snapshot) |

**Breakdowns:**
- `by_stage` — task stages with counts
- `by_project` — tasks grouped by project

**Chart series:** Monthly task creation count

**Alerts:** Overdue tasks past deadline

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/analytics/projects/overview?from=2026-01-01&to=2026-03-31' \
  -H "session-token: <token>"
```

---

## 20. Project & Task API (Session Token)

Project and task data use the generic CRUD endpoints with these models:

- **Projects model:** `project.project`
- **Tasks model:** `project.task`

Use the same session token you get from `POST /auth/login`:

```bash
-H "session-token: <token>"
```

### 20.1 List Projects

```
GET /search/project.project
```

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/search/project.project?limit=20&fields=id,name,partner_id,user_id,stage_id,date_start,date' \
  -H "session-token: <token>"
```

---

### 20.2 Get One Project

```
GET /search/project.project/{id}
```

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/search/project.project/1?fields=id,name,description,partner_id,user_id,stage_id' \
  -H "session-token: <token>"
```

---

### 20.3 Create Project

```
POST /create/project.project
```

**Headers:** `Content-Type: application/json` + `session-token`
**Required role/group:** Project Administrator (`project.group_project_manager`) or equivalent admin rights.

**Example:**

```bash
curl -s -X POST 'http://localhost:8069/api/v2/create/project.project' \
  -H "session-token: <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Customer Portal Revamp",
    "partner_id": 59,
    "description": "<p>Modernize portal experience and workflows.</p>"
  }'
```

---

### 20.4 List Tasks

```
GET /search/project.task
```

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/search/project.task?limit=20&fields=id,name,project_id,user_ids,stage_id,priority,date_deadline' \
  -H "session-token: <token>"
```

---

### 20.5 Get One Task

```
GET /search/project.task/{id}
```

**Example:**

```bash
curl -s 'http://localhost:8069/api/v2/search/project.task/1?fields=id,name,project_id,description,user_ids,stage_id,date_deadline' \
  -H "session-token: <token>"
```

---

### 20.6 Create Task

```
POST /create/project.task
```

**Headers:** `Content-Type: application/json` + `session-token`
**Required role/group:** Project User (`project.group_project_user`) or Project Administrator (`project.group_project_manager`).

**Example:**

```bash
curl -s -X POST 'http://localhost:8069/api/v2/create/project.task' \
  -H "session-token: <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Define API contract with frontend",
    "project_id": 1,
    "priority": "1",
    "description": "<p>Document request/response schema and edge cases.</p>"
  }'
```

**Optional assignment fields for task creation:**
- `user_ids`: many2many command format, example `[[6, 0, [31, 32]]]`
- `date_deadline`: deadline datetime string
- `tag_ids`: many2many command format

---

## Quick Reference: All Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/test` | None | Health check |
| `POST` | `/auth/login` | None | Login, get session token |
| `POST` | `/auth/refresh` | Session | Refresh session token |
| `POST` | `/auth/logout` | Session | Invalidate session |
| `GET` | `/auth/me` | Both | Current user + permissions + module access |
| `GET` | `/modules/access` | Both | Check which modules user can access |
| `GET` | `/auth/test` | API Key | Test API key auth |
| `GET` | `/user/info` | API Key | User info + API metadata |
| `GET` | `/users` | Both | List users (paginated) |
| `GET` | `/users/{id}` | Both | Get user detail |
| `PUT` | `/users/{id}` | Both | Update user profile |
| `PUT` | `/users/{id}/password` | Both | Change password |
| `POST` | `/users/{id}/reset-password` | Both (Admin) | Reset password |
| `POST` | `/users/{id}/api-key` | Both | Generate API key |
| `GET` | `/groups` | Both (Admin) | List assignable groups |
| `GET` | `/partners` | API Key | List partners (legacy) |
| `GET` | `/products` | API Key | List products (legacy) |
| `GET` | `/search/{model}` | Both | Search records |
| `GET` | `/search/{model}/{id}` | Both | Get record by ID |
| `POST` | `/create/{model}` | Both | Create record |
| `PUT` | `/update/{model}/{id}` | Both | Update record |
| `DELETE` | `/delete/{model}/{id}` | Both | Delete record |
| `POST` | `/inventory/adjust` | Both | Adjust inventory quantities |
| `GET` | `/search/project.project` | Both | List/search projects |
| `POST` | `/create/project.project` | Both | Create project |
| `GET` | `/search/project.task` | Both | List/search tasks |
| `POST` | `/create/project.task` | Both | Create task |
| `GET` | `/fields/{model}` | Both | Get model field metadata |
| `GET` | `/models` | Both | List accessible models |
| `GET` | `/analytics/dashboard/summary` | Session | Cross-module KPI dashboard |
| `GET` | `/analytics/crm/overview` | Session | CRM leads, pipeline, revenue |
| `GET` | `/analytics/sales/overview` | Session | Sales orders, revenue, quotations |
| `GET` | `/analytics/invoicing/overview` | Session | Invoices, payments, overdue |
| `GET` | `/analytics/inventory/overview` | Session | Transfers, late shipments |
| `GET` | `/analytics/purchases/overview` | Session | Purchase orders, RFQs |
| `GET` | `/analytics/hr/overview` | Session | Headcount, new hires, departments |
| `GET` | `/analytics/projects/overview` | Session | Tasks, overdue, projects |
