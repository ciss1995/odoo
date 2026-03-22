# Settings API Guide

## Overview

This guide documents all settings-related API endpoints in the `base_api` module, what each user role can do, and the actual JSON responses returned for each role.

**Base URL:** `http://localhost:8069`
**Authentication:** API key (`api-key` header) or session token (`session-token` header)

---

## User Roles

The system has 3 distinct roles based on Odoo group membership:

| User | Login | Role | Key Groups |
|------|-------|------|------------|
| Administrator (id=2) | `admin` | System Admin | `base.group_system` (Role / Administrator) + all module admins |
| Manager User (id=5) | `manager@test.com` | Internal User | `base.group_user` (Role / User) |
| Regular User (id=6) | `user@test.com` | Internal User | `base.group_user` (Role / User) |
| Sales User (id=7) | `sales@test.com` | Internal User + Sales | `base.group_user` + `sales_team.group_sale_salesman` |

---

## Permissions by Role

### Administrator (`base.group_system`)

| Capability | Allowed | Endpoint |
|------------|---------|----------|
| View system settings (`res.config.settings`) | YES | `GET /api/v2/search/res.config.settings` |
| View full settings detail (249 fields) | YES | `GET /api/v2/search/res.config.settings/{id}` |
| Update system settings | YES | `PUT /api/v2/update/res.config.settings/{id}` |
| View system parameters (`ir.config_parameter`) | YES | `GET /api/v2/search/ir.config_parameter` |
| Update system parameters | YES | `PUT /api/v2/update/ir.config_parameter/{id}` |
| View company settings | YES | `GET /api/v2/search/res.company/{id}` |
| Update company settings | YES | `PUT /api/v2/update/res.company/{id}` |
| List all assignable groups by category | YES | `GET /api/v2/groups` |
| List all users (with groups, company, login_date) | YES | `GET /api/v2/users` |
| View any user's full profile (groups, companies, login_date) | YES | `GET /api/v2/users/{id}` |
| Update any user's profile | YES | `PUT /api/v2/users/{id}` |
| Update admin-only fields (login, active, company_id) | YES | `PUT /api/v2/users/{id}` |
| Assign/change user groups | YES | `PUT /api/v2/users/{id}` with `group_names` or `group_ids` |
| Reset any user's password | YES | `POST /api/v2/users/{id}/reset-password` |
| Change any user's password | YES | `PUT /api/v2/users/{id}/password` |
| Generate API key for any user | YES | `POST /api/v2/users/{id}/api-key` |
| Create new users | YES | `POST /api/v2/create/res.users` |
| Discover settings-related models | YES | `GET /api/v2/models?search=config` |
| View settings field metadata | YES | `GET /api/v2/fields/res.config.settings` |

### Internal User (`base.group_user` - Regular User / Manager User)

| Capability | Allowed | Endpoint |
|------------|---------|----------|
| View system settings (`res.config.settings`) | NO | ACCESS_DENIED |
| Update system settings | NO | ACCESS_DENIED |
| View system parameters (`ir.config_parameter`) | NO | ACCESS_DENIED |
| Update system parameters | NO | ACCESS_DENIED |
| View company settings (read-only) | YES | `GET /api/v2/search/res.company/{id}` |
| Update company settings | NO | ACCESS_DENIED |
| List groups | NO | ACCESS_DENIED |
| List users (basic info only: name, login, email, active) | YES | `GET /api/v2/users` |
| View own profile (login, phone, lang, tz, signature, company) | YES | `GET /api/v2/users/{id}` |
| View other users (name, email, active, create_date only) | YES | `GET /api/v2/users/{id}` |
| Update own profile (name, email, phone, signature, lang, tz) | YES | `PUT /api/v2/users/{id}` |
| Update other users | NO | ACCESS_DENIED |
| Update admin-only fields (login, active, company_id) | NO | ACCESS_DENIED |
| Assign/change groups | NO | ACCESS_DENIED |
| Reset another user's password | NO | ACCESS_DENIED |
| Change own password (requires old_password) | YES | `PUT /api/v2/users/{id}/password` |
| Generate own API key | YES | `POST /api/v2/users/{id}/api-key` |
| Discover models (limited to accessible ones) | YES | `GET /api/v2/models` |
| View settings field metadata | YES | `GET /api/v2/fields/res.config.settings` (reads from `ir.model.fields`) |

### Sales User (`base.group_user` + `sales_team.group_sale_salesman`)

Same as Internal User above, plus access to sales-specific data via `/api/v2/search/sale.order`, etc. No additional settings privileges.

---

## Self-Editable Fields by Role

| Field | Admin | Regular/Sales (own profile) |
|-------|-------|-----------------------------|
| `name` | Yes | Yes |
| `email` | Yes | Yes |
| `phone` | Yes | Yes |
| `signature` | Yes | Yes |
| `lang` | Yes | Yes |
| `tz` | Yes | Yes |
| `login` | Yes | No (admin-only) |
| `active` | Yes | No (admin-only) |
| `company_id` | Yes | No (admin-only) |
| `company_ids` | Yes | No (admin-only) |
| `group_names` / `group_ids` | Yes | No (admin-only) |
| `password` | Yes (any user) | Yes (own, needs old_password) |

---

## Settings READ Endpoints

### 1. `GET /api/v2/auth/me` - Current User Info + Permissions

Returns the authenticated user's profile, groups, and permission flags.

```bash
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/auth/me"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2,
      "name": "Administrator",
      "login": "admin",
      "email": false,
      "active": true,
      "company_id": [1, "My Company"],
      "groups": [
        {"id": 24, "name": "Administrator"},
        {"id": 30, "name": "Administrator"},
        {"id": 59, "name": "Administrator"},
        {"id": 38, "name": "Administrator"},
        {"id": 21, "name": "Create"},
        {"id": 39, "name": "Admin"},
        {"id": 46, "name": "Administrator"},
        {"id": 4, "name": "Role / Administrator"}
      ],
      "permissions": {
        "is_admin": true,
        "is_user": true,
        "can_manage_users": false
      }
    }
  },
  "message": "User information retrieved"
}
```

**Regular User Response:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6,
      "name": "Regular User",
      "login": "user@test.com",
      "email": "user@test.com",
      "active": true,
      "company_id": [1, "My Company"],
      "groups": [
        {"id": 1, "name": "Role / User"}
      ],
      "permissions": {
        "is_admin": false,
        "is_user": true,
        "can_manage_users": false
      }
    }
  },
  "message": "User information retrieved"
}
```

**Sales User Response:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 7,
      "name": "Sales User",
      "login": "sales@test.com",
      "email": "sales@test.com",
      "active": true,
      "company_id": [1, "My Company"],
      "groups": [
        {"id": 22, "name": "User: Own Documents Only"},
        {"id": 1, "name": "Role / User"}
      ],
      "permissions": {
        "is_admin": false,
        "is_user": true,
        "can_manage_users": false
      }
    }
  },
  "message": "User information retrieved"
}
```

---

### 2. `GET /api/v2/user/info` - Basic User Info

```bash
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/user/info"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2,
      "name": "Administrator",
      "login": "admin",
      "email": false,
      "active": true,
      "company_id": [1, "My Company"]
    },
    "api_version": "2.0",
    "database": "odoo19_db"
  },
  "message": "User information retrieved successfully"
}
```

**Regular User Response:**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6,
      "name": "Regular User",
      "login": "user@test.com",
      "email": "user@test.com",
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

### 3. `GET /api/v2/groups` - List Assignable Groups

Requires `base.group_user_admin` or `base.group_system`.

```bash
curl -H "api-key: YOUR_ADMIN_API_KEY" "http://localhost:8069/api/v2/groups"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "groups_by_category": {
      "Supply Chain": [
        {"id": 46, "name": "Administrator", "full_name": "Inventory / Administrator", "xml_id": "stock.group_stock_manager", "comment": "", "users_count": 1},
        {"id": 45, "name": "User", "full_name": "Inventory / User", "xml_id": "stock.group_stock_user", "comment": "", "users_count": 1},
        {"id": 59, "name": "Administrator", "full_name": "Purchase / Administrator", "xml_id": "purchase.group_purchase_manager", "comment": "", "users_count": 1},
        {"id": 58, "name": "User", "full_name": "Purchase / User", "xml_id": "purchase.group_purchase_user", "comment": "", "users_count": 1}
      ],
      "Master Data": [
        {"id": 8, "name": "Allowed", "full_name": "Export / Allowed", "xml_id": "base.group_allow_export", "comment": "", "users_count": 1},
        {"id": 9, "name": "Creation", "full_name": "Contact / Creation", "xml_id": "base.group_partner_manager", "comment": "", "users_count": 1},
        {"id": 21, "name": "Create", "full_name": "Products / Create", "xml_id": "product.group_product_manager", "comment": "", "users_count": 1}
      ],
      "Marketing": [
        {"id": 15, "name": "Canned Response Administrator", "full_name": "Canned Responses / Canned Response Administrator", "xml_id": "mail.group_mail_canned_response_admin", "comment": "", "users_count": 1}
      ],
      "Accounting": [
        {"id": 34, "name": "Validate bank account", "full_name": "Bank / Validate bank account", "xml_id": "account.group_validate_bank_account", "comment": "", "users_count": 1},
        {"id": 30, "name": "Administrator", "full_name": "Accounting / Administrator", "xml_id": "account.group_account_manager", "comment": "Full access, including configuration rights.", "users_count": 1},
        {"id": 27, "name": "Invoicing", "full_name": "Accounting / Invoicing", "xml_id": "account.group_account_invoice", "comment": "Invoices, payments and basic invoice reporting.", "users_count": 1}
      ],
      "Productivity": [
        {"id": 39, "name": "Admin", "full_name": "Dashboard / Admin", "xml_id": "spreadsheet_dashboard.group_dashboard_manager", "comment": "", "users_count": 1}
      ],
      "Human Resources": [
        {"id": 38, "name": "Administrator", "full_name": "Employees / Administrator", "xml_id": "hr.group_hr_manager", "comment": "The user will have access to the human resources configuration as well as statistic reports.", "users_count": 1},
        {"id": 37, "name": "Officer: Manage all employees", "full_name": "Employees / Officer: Manage all employees", "xml_id": "hr.group_hr_user", "comment": "The user will be able to create and edit employees.", "users_count": 1}
      ],
      "Sales": [
        {"id": 24, "name": "Administrator", "full_name": "Sales / Administrator", "xml_id": "sales_team.group_sale_manager", "comment": "the user will have an access to the sales configuration as well as statistic reports.", "users_count": 1},
        {"id": 23, "name": "User: All Documents", "full_name": "Sales / User: All Documents", "xml_id": "sales_team.group_sale_salesman_all_leads", "comment": "the user will have access to all records of everyone in the sales application.", "users_count": 1},
        {"id": 22, "name": "User: Own Documents Only", "full_name": "Sales / User: Own Documents Only", "xml_id": "sales_team.group_sale_salesman", "comment": "the user will have access to his own data in the sales application.", "users_count": 2}
      ]
    },
    "total_groups": 17
  },
  "message": "Available groups retrieved"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied: User management required",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 4. `GET /api/v2/users` - List All Users

```bash
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/users"
```

**Admin Response** (includes groups, company, login_date):

```json
{
  "success": true,
  "data": {
    "users": [
      {
        "id": 2, "name": "Administrator", "login": "admin", "email": false,
        "active": true, "create_date": "2026-03-21T05:08:09.435235",
        "groups": ["Administrator", "Administrator", "Administrator", "Administrator", "Create", "Admin", "Administrator", "Role / Administrator"],
        "company_id": "My Company",
        "login_date": "2026-03-21T22:47:42.502400"
      },
      {
        "id": 5, "name": "Manager User", "login": "manager@test.com", "email": "manager@test.com",
        "active": true, "create_date": "2026-03-21T05:09:22.242613",
        "groups": ["Role / User"],
        "company_id": "My Company",
        "login_date": "2026-03-21T05:12:44.602740"
      },
      {
        "id": 6, "name": "Regular User", "login": "user@test.com", "email": "user@test.com",
        "active": true, "create_date": "2026-03-21T05:09:22.242613",
        "groups": ["Role / User"],
        "company_id": "My Company",
        "login_date": "2026-03-21T05:13:13.547871"
      },
      {
        "id": 7, "name": "Sales User", "login": "sales@test.com", "email": "sales@test.com",
        "active": true, "create_date": "2026-03-21T05:09:22.242613",
        "groups": ["User: Own Documents Only", "Role / User"],
        "company_id": "My Company",
        "login_date": null
      }
    ],
    "count": 4, "total_count": 4, "limit": 10, "offset": 0
  },
  "message": "Found 4 users"
}
```

**Regular / Sales User Response** (basic info only, no groups/company/login_date):

```json
{
  "success": true,
  "data": {
    "users": [
      {"id": 2, "name": "Administrator", "login": "admin", "email": false, "active": true, "create_date": "2026-03-21T05:08:09.435235"},
      {"id": 5, "name": "Manager User", "login": "manager@test.com", "email": "manager@test.com", "active": true, "create_date": "2026-03-21T05:09:22.242613"},
      {"id": 6, "name": "Regular User", "login": "user@test.com", "email": "user@test.com", "active": true, "create_date": "2026-03-21T05:09:22.242613"},
      {"id": 7, "name": "Sales User", "login": "sales@test.com", "email": "sales@test.com", "active": true, "create_date": "2026-03-21T05:09:22.242613"}
    ],
    "count": 4, "total_count": 4, "limit": 10, "offset": 0
  },
  "message": "Found 4 users"
}
```

---

### 5. `GET /api/v2/users/{id}` - User Detail

```bash
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/users/2"
```

**Admin viewing own profile (id=2):**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2, "name": "Administrator", "email": false, "active": true,
      "create_date": "2026-03-21T05:08:09.435235",
      "login": "admin", "phone": false, "lang": "en_US", "tz": "America/Toronto",
      "signature": "<div>Administrator</div>",
      "company_id": [1, "My Company"],
      "groups": [
        {"id": 24, "name": "Administrator", "full_name": "Sales / Administrator"},
        {"id": 30, "name": "Administrator", "full_name": "Accounting / Administrator"},
        {"id": 59, "name": "Administrator", "full_name": "Purchase / Administrator"},
        {"id": 38, "name": "Administrator", "full_name": "Employees / Administrator"},
        {"id": 21, "name": "Create", "full_name": "Products / Create"},
        {"id": 39, "name": "Admin", "full_name": "Dashboard / Admin"},
        {"id": 46, "name": "Administrator", "full_name": "Inventory / Administrator"},
        {"id": 4, "name": "Role / Administrator", "full_name": "Role / Administrator"}
      ],
      "company_ids": [{"id": 1, "name": "My Company"}],
      "login_date": "2026-03-21T22:47:42.502400"
    }
  },
  "message": "User information retrieved"
}
```

**Admin viewing another user (id=6):**

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6, "name": "Regular User", "email": "user@test.com", "active": true,
      "create_date": "2026-03-21T05:09:22.242613",
      "login": "user@test.com", "phone": false, "lang": "en_US", "tz": false,
      "signature": "<div>Regular User</div>",
      "company_id": [1, "My Company"],
      "groups": [
        {"id": 1, "name": "Role / User", "full_name": "Role / User"}
      ],
      "company_ids": [{"id": 1, "name": "My Company"}],
      "login_date": "2026-03-21T05:13:13.547871"
    }
  },
  "message": "User information retrieved"
}
```

**Regular User viewing own profile (id=6)** (no groups, no companies, no login_date):

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6, "name": "Regular User", "email": "user@test.com", "active": true,
      "create_date": "2026-03-21T05:09:22.242613",
      "login": "user@test.com", "phone": false, "lang": "en_US", "tz": false,
      "signature": "<div>Regular User</div>",
      "company_id": [1, "My Company"]
    }
  },
  "message": "User information retrieved"
}
```

**Regular User viewing another user (id=2)** (minimal info only):

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 2, "name": "Administrator", "email": false, "active": true,
      "create_date": "2026-03-21T05:08:09.435235"
    }
  },
  "message": "User information retrieved"
}
```

---

### 6. `GET /api/v2/search/res.config.settings` - System Configuration

```bash
curl -H "api-key: YOUR_ADMIN_API_KEY" "http://localhost:8069/api/v2/search/res.config.settings"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "records": [{"id": 1, "display_name": "Settings"}],
    "count": 1, "model": "res.config.settings",
    "fields": ["id", "display_name"],
    "total_count": 1
  },
  "message": "Found 1 records in res.config.settings"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied for model 'res.config.settings'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 7. `GET /api/v2/search/res.config.settings/1` - Full Settings Detail

Returns all 249 settings fields. Use `?fields=` to select specific ones.

```bash
# All fields
curl -H "api-key: YOUR_ADMIN_API_KEY" "http://localhost:8069/api/v2/search/res.config.settings/1"

# Specific fields
curl -H "api-key: YOUR_ADMIN_API_KEY" \
  "http://localhost:8069/api/v2/search/res.config.settings/1?fields=company_name,currency_id,sale_tax_id,purchase_tax_id,auth_signup_reset_password,show_effect"
```

**Admin Response (selected key fields shown):**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "display_name": "Settings",
      "company_id": [1, "My Company"],
      "is_root_company": true,
      "module_base_import": true,
      "module_google_calendar": false,
      "module_microsoft_calendar": false,
      "module_auth_oauth": false,
      "module_auth_ldap": false,
      "module_voip": false,
      "module_web_unsplash": true,
      "module_sms": false,
      "module_partner_autocomplete": false,
      "group_multi_currency": false,
      "show_effect": true,
      "company_count": 1,
      "active_user_count": 4,
      "language_count": 1,
      "company_name": "My Company",
      "company_country_code": "US",
      "restrict_template_rendering": true,
      "auth_signup_reset_password": true,
      "auth_signup_uninvited": "b2c",
      "auth_totp_enforce": false,
      "group_uom": false,
      "group_product_variant": false,
      "group_product_pricelist": false,
      "portal_allow_api_keys": false,
      "digest_emails": true,
      "has_chart_of_accounts": true,
      "chart_template": "generic_coa",
      "sale_tax_id": [1, "15%"],
      "purchase_tax_id": [2, "15%"],
      "account_price_include": "tax_excluded",
      "tax_calculation_rounding_method": "round_globally",
      "currency_id": [1, "USD"],
      "country_code": "US",
      "has_accounting_entries": true,
      "autopost_bills": true
    },
    "model": "res.config.settings",
    "id": 1,
    "total_fields_available": 249
  },
  "message": "Found record 1 in res.config.settings"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied for model 'res.config.settings'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 8. `GET /api/v2/search/ir.config_parameter` - System Parameters

```bash
curl -H "api-key: YOUR_ADMIN_API_KEY" \
  "http://localhost:8069/api/v2/search/ir.config_parameter?limit=10&fields=key,value"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "records": [
      {"id": 1, "key": "database.secret", "value": "005f3f24-a151-433a-a867-c80f266fe7a8"},
      {"id": 2, "key": "database.uuid", "value": "f5414fba-24e3-11f1-9a58-e24fd493b472"},
      {"id": 3, "key": "database.create_date", "value": "2026-03-21 05:08:11"},
      {"id": 4, "key": "web.base.url", "value": "http://localhost:8069"},
      {"id": 5, "key": "base.login_cooldown_after", "value": "10"},
      {"id": 6, "key": "base.login_cooldown_duration", "value": "60"},
      {"id": 7, "key": "base.template_portal_user_id", "value": "4"},
      {"id": 8, "key": "base.default_max_email_size", "value": "10"},
      {"id": 9, "key": "base_setup.show_effect", "value": "True"},
      {"id": 10, "key": "mail.activity.gc.delete_overdue_years", "value": "3"}
    ],
    "count": 10, "model": "ir.config_parameter",
    "fields": ["id", "key", "value"],
    "total_count": 24
  },
  "message": "Found 10 records in ir.config_parameter"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied for model 'ir.config_parameter'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 9. `GET /api/v2/search/res.company/{id}` - Company Settings

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/res.company/1?fields=name,currency_id,country_id,street,city,zip,phone,email,website,vat,company_registry"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "name": "My Company",
      "currency_id": [1, "USD"],
      "country_id": [233, "United States"],
      "street": "",
      "city": "",
      "zip": "",
      "phone": "+1-555-0100",
      "email": false,
      "website": false,
      "vat": false,
      "company_registry": false
    },
    "model": "res.company",
    "id": 1,
    "total_fields_available": 182
  },
  "message": "Found record 1 in res.company"
}
```

**Regular / Sales User Response** (read-only, same structure):

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "name": "My Company",
      "currency_id": [1, "USD"],
      "country_id": [233, "United States"]
    },
    "model": "res.company",
    "id": 1,
    "total_fields_available": 182
  },
  "message": "Found record 1 in res.company"
}
```

---

### 10. `GET /api/v2/fields/res.config.settings` - Settings Field Metadata

Returns metadata for all 249 settings fields.

```bash
curl -H "api-key: YOUR_ADMIN_API_KEY" "http://localhost:8069/api/v2/fields/res.config.settings"
```

**Admin Response (sample):**

```json
{
  "success": true,
  "data": {
    "model": "res.config.settings",
    "fields": [
      {
        "name": "auth_signup_reset_password",
        "description": "Enable password reset from Login page",
        "type": "boolean",
        "required": false,
        "readonly": false,
        "help": "",
        "relation": "",
        "store": true
      },
      {
        "name": "auth_totp_enforce",
        "description": "Enforce two-factor authentication",
        "type": "boolean",
        "required": false,
        "readonly": false,
        "help": "",
        "relation": "",
        "store": true
      },
      {
        "name": "company_id",
        "description": "Company",
        "type": "many2one",
        "required": true,
        "readonly": false,
        "help": "",
        "relation": "res.company",
        "store": false
      }
    ],
    "count": 249
  },
  "message": "Found 249 fields for model res.config.settings"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Error retrieving model fields",
    "code": "FIELDS_ERROR"
  }
}
```

---

### 11. `GET /api/v2/models?search=config` - Discover Settings Models

```bash
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/models?search=config"
```

**Admin Response** (3 models):

```json
{
  "success": true,
  "data": {
    "models": [
      {"model": "ir.actions.todo", "name": "Configuration Wizards", "info": "Configuration Wizards", "field_count": 10},
      {"model": "ir.config_parameter", "name": "System Parameter", "info": "Per-database storage of configuration key-value pairs.", "field_count": 8},
      {"model": "report.paperformat", "name": "Paper Format Config", "info": "...", "field_count": 24}
    ],
    "count": 3
  },
  "message": "Found 3 accessible models"
}
```

**Regular / Sales User Response** (1 model):

```json
{
  "success": true,
  "data": {
    "models": [
      {"model": "report.paperformat", "name": "Paper Format Config", "info": "...", "field_count": 24}
    ],
    "count": 1
  },
  "message": "Found 1 accessible models"
}
```

---

## Settings UPDATE Endpoints

### 1. `PUT /api/v2/update/res.config.settings/{id}` - Update System Settings

Admin only. Updates system-wide configuration.

```bash
curl -X PUT -H "api-key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/update/res.config.settings/1" \
  -d '{"show_effect": true}'
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "display_name": "Settings",
      "write_date": "2026-03-22 00:35:45.710210"
    },
    "updated_fields": ["show_effect"]
  },
  "message": "Record 1 updated in res.config.settings"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Write access denied for model 'res.config.settings'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 2. `PUT /api/v2/update/ir.config_parameter/{id}` - Update System Parameters

Admin only. Updates individual system key-value parameters.

```bash
curl -X PUT -H "api-key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/update/ir.config_parameter/9" \
  -d '{"value": "True"}'
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 9,
      "display_name": "base_setup.show_effect",
      "write_date": "2026-03-22 00:36:16.128644"
    },
    "updated_fields": ["value"]
  },
  "message": "Record 9 updated in ir.config_parameter"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Write access denied for model 'ir.config_parameter'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 3. `PUT /api/v2/update/res.company/{id}` - Update Company Settings

Admin only.

```bash
curl -X PUT -H "api-key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/update/res.company/1" \
  -d '{"phone": "+1-555-0100"}'
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "name": "My Company",
      "display_name": "My Company",
      "write_date": "2026-03-22 00:35:45.979719"
    },
    "updated_fields": ["phone"]
  },
  "message": "Record 1 updated in res.company"
}
```

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Write access denied for model 'res.company'",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 4. `PUT /api/v2/users/{id}` - Update User Profile

Admin can update any user. Regular users can only update their own profile with limited fields.

**Admin updating another user (including group assignment):**

```bash
curl -X PUT -H "api-key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/users/6" \
  -d '{"name": "Regular User", "group_names": ["Role / User"]}'
```

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6, "name": "Regular User", "login": "user@test.com",
      "email": "user@test.com", "phone": "+1-555-0101",
      "active": true, "lang": "en_US", "tz": false,
      "groups": [{"id": 1, "name": "Role / User"}]
    },
    "updated_fields": ["group_ids"]
  },
  "message": "User updated successfully"
}
```

**Regular User updating own profile:**

```bash
curl -X PUT -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/users/6" \
  -d '{"name": "Regular User", "phone": "+1-555-0101"}'
```

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 6, "name": "Regular User", "login": "user@test.com",
      "email": "user@test.com", "phone": "+1-555-0101",
      "active": true, "lang": "en_US", "tz": false
    },
    "updated_fields": ["name", "phone"]
  },
  "message": "User updated successfully"
}
```

**Regular User trying to update another user:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied: Can only update own profile or need admin rights",
    "code": "ACCESS_DENIED"
  }
}
```

**Regular User trying admin-only field (`login`):**

```json
{
  "success": false,
  "error": {
    "message": "Access denied: Field 'login' requires admin rights",
    "code": "ADMIN_FIELD_ACCESS_DENIED"
  }
}
```

**Regular User trying group assignment:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied: Field 'group_names' requires admin rights",
    "code": "ADMIN_FIELD_ACCESS_DENIED"
  }
}
```

---

### 5. `PUT /api/v2/users/{id}/password` - Change Password

Admin can change any user's password. Regular users can change their own (requires `old_password`).

**Admin changing another user's password:**

```bash
curl -X PUT -H "api-key: YOUR_ADMIN_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/users/6/password" \
  -d '{"new_password": "newpass123"}'
```

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

**Regular User changing own password:**

```bash
curl -X PUT -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/users/6/password" \
  -d '{"old_password": "current_password", "new_password": "new_password"}'
```

---

### 6. `POST /api/v2/users/{id}/reset-password` - Reset Password (Admin Only)

Generates a temporary password for the target user.

```bash
curl -X POST -H "api-key: YOUR_ADMIN_API_KEY" \
  "http://localhost:8069/api/v2/users/6/reset-password"
```

**Admin Response:**

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

**Regular / Sales User Response:**

```json
{
  "success": false,
  "error": {
    "message": "Access denied: Admin rights required",
    "code": "ACCESS_DENIED"
  }
}
```

---

### 7. `POST /api/v2/users/{id}/api-key` - Generate API Key

Admin can generate for any user. Regular users can generate their own.

```bash
curl -X POST -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/users/7/api-key"
```

**Success Response:**

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

## Quick Reference: Endpoint Access Matrix

| Endpoint | Method | Admin | Internal User | Sales User |
|----------|--------|-------|---------------|------------|
| `/api/v2/auth/me` | GET | Full | Full | Full |
| `/api/v2/user/info` | GET | Full | Full | Full |
| `/api/v2/groups` | GET | Full | Denied | Denied |
| `/api/v2/users` | GET | Full + groups | Basic | Basic |
| `/api/v2/users/{id}` | GET | Full | Own: extended, Other: basic | Own: extended, Other: basic |
| `/api/v2/users/{id}` | PUT | Any user, all fields | Own profile, limited fields | Own profile, limited fields |
| `/api/v2/users/{id}/password` | PUT | Any user | Own only (needs old_password) | Own only (needs old_password) |
| `/api/v2/users/{id}/reset-password` | POST | Any user | Denied | Denied |
| `/api/v2/users/{id}/api-key` | POST | Any user | Own only | Own only |
| `/api/v2/search/res.config.settings` | GET | Full | Denied | Denied |
| `/api/v2/search/res.config.settings/{id}` | GET | Full (249 fields) | Denied | Denied |
| `/api/v2/update/res.config.settings/{id}` | PUT | Full | Denied | Denied |
| `/api/v2/search/ir.config_parameter` | GET | Full | Denied | Denied |
| `/api/v2/update/ir.config_parameter/{id}` | PUT | Full | Denied | Denied |
| `/api/v2/search/res.company/{id}` | GET | Full | Read-only | Read-only |
| `/api/v2/update/res.company/{id}` | PUT | Full | Denied | Denied |
| `/api/v2/fields/res.config.settings` | GET | Full (249 fields) | Error | Error |
| `/api/v2/models?search=config` | GET | 3 models | 1 model | 1 model |
| `/api/v2/create/res.users` | POST | Full | Denied | Denied |
