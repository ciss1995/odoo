# Odoo 19 Upgrade: Advantages and AI Integration Strategy

## What Changed in Odoo 19

### Restructured ORM (`odoo.orm`)

Odoo 19 moves the core ORM implementation into `odoo/orm/`, splitting it into focused modules:

- `odoo.orm.models` -- base model classes
- `odoo.orm.fields` -- all field types
- `odoo.orm.decorators` -- `@api.model`, `@api.depends`, `@onchange`
- `odoo.orm.environments` -- `Transaction` and `Environment`
- `odoo.orm.domains` -- first-class `Domain` objects
- `odoo.orm.commands` -- `Command` helper for relational writes

The legacy import paths (`from odoo import api, fields, models`) remain valid and
forward to `odoo.orm`, so existing modules continue to work without import changes.

### New External API (`/json/2/`)

The `addons/rpc` module introduces a REST-style endpoint:

```
POST /json/2/<model>/<method>
```

This replaces the older `/jsonrpc` and `/xmlrpc` endpoints, which are **deprecated in
Odoo 19** and scheduled for removal in Odoo 22. The new endpoint accepts standard JSON
bodies and returns JSON responses directly, removing the JSON-RPC envelope overhead.

### Privilege-Based Group Model

`res.groups` no longer has a `category_id` field. Instead, groups link to
`res.groups.privilege` via `privilege_id`, which in turn links to
`ir.module.category` via `category_id`. This three-level hierarchy
(category -> privilege -> group) provides finer-grained access control.

The field `res.users.groups_id` has been renamed to `group_ids`.

### Declarative Constraints

SQL constraints use class-level declarations instead of the old list format:

```python
# Odoo 18
_sql_constraints = [('name_unique', 'UNIQUE(name)', 'Name must be unique')]

# Odoo 19
_name_unique = models.Constraint('UNIQUE(name)', 'Name must be unique')
```

`UniqueIndex` and `Index` are also available for non-constraint indexes.

### Route Type Change

`@http.route(type='json')` is deprecated. Use `type='jsonrpc'` instead. The old
value still works but emits a deprecation warning.

### Deprecated Record Shortcuts

Direct access to `record._cr`, `record._uid`, and `record._context` is deprecated.
Use `record.env.cr`, `record.env.uid`, and `record.env.context`.

### Hashed API Keys

API keys stored in `res_users_apikeys` are now hashed with passlib. An `index` column
(first N characters of the key) enables fast lookups without exposing the full key.
Raw SQL inserts into this table will fail; always use
`env['res.users.apikeys']._generate(scope, name, expiration_date)`.

### Python and PostgreSQL Requirements

- **Python**: 3.10 -- 3.13
- **PostgreSQL**: 13+

---

## How base_api Leverages Odoo 19

### Updated Authentication

The module uses Odoo 19's hashed API key system (`_check_credentials`) for
authentication. This means API keys are never stored in plain text and verification
uses constant-time hash comparison.

### Session Authentication

Login uses `request.session.authenticate(env, credential)` with the Odoo 19
signature, passing a structured credential dictionary instead of positional
arguments.

### Proper ORM Usage

API key generation uses the ORM's `_generate` method instead of raw SQL. This
ensures keys are properly hashed, indexed, and respect expiration constraints.

### Privilege-Aware Group Listing

The `/api/v2/groups` endpoint now navigates the privilege -> category hierarchy,
providing accurate group categorization.

---

## AI Module Integration Strategy

The `base_api` module's existing endpoints provide the foundation for an AI agent
that has scoped visibility into a user's database. Below is the architecture for an
`ai_assistant` module that builds on top of `base_api`.

### Architecture Overview

```
AI Client (LLM Agent)
        |
        v
  base_api auth layer (API key / session)
        |
        v
  ai_assistant controller (/api/v2/ai/*)
        |
        v
  Odoo ORM (with user's access rights)
```

The AI agent authenticates with the same API key or session token as a regular user.
Every query it makes is filtered through Odoo's access control system (`ir.model.access`
and `ir.rule`), so the AI can only see what the user is allowed to see.

### Proposed Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v2/ai/schema` | GET | Return all models and fields the user can access |
| `/api/v2/ai/query` | POST | Accept a natural language query, translate to domain search, return results |
| `/api/v2/ai/summarize/<model>` | GET | Return aggregated stats for a model (counts, sums, recent activity) |
| `/api/v2/ai/execute` | POST | Run a validated action (create record, update field) with confirmation |

### Schema Discovery

The AI agent uses the existing `base_api` endpoints to understand the database:

1. **`GET /api/v2/fields/<model>`** -- enumerate all fields on a model with types,
   relations, and help text.
2. **`GET /api/v2/search/<model>?fields=...&limit=...`** -- read data from any model
   the user has access to.

This gives the AI full visibility into the user-scoped database without any
special privileges.

### Leveraging `/json/2/` for Direct Model Access

Odoo 19's new `/json/2/<model>/<method>` endpoint lets the AI call any public
model method directly with a JSON payload. Combined with proper API key scoping,
this provides a powerful and secure interface:

```json
POST /json/2/res.partner/search_read
{
    "args": [[["customer_rank", ">", 0]]],
    "kwargs": {"fields": ["name", "email", "phone"], "limit": 50}
}
```

### Security Model

- **User-scoped**: The AI operates under the authenticated user's identity. It
  cannot see records outside the user's access rules.
- **Read-by-default**: The AI agent should be configured with a read-only API key
  scope by default. Write operations require explicit user confirmation.
- **Audit trail**: All AI-initiated writes go through the ORM, so they appear in
  the standard `create_uid`/`write_uid` audit fields.
- **Rate limiting**: The nginx configuration already rate-limits `/api/v2/` to
  10 requests/second. AI agents should respect this or use a dedicated rate tier.

### Sample ai_assistant Module Structure

```
addons/ai_assistant/
    __init__.py
    __manifest__.py
    controllers/
        __init__.py
        ai_api.py           # /api/v2/ai/* endpoints
    models/
        __init__.py
        ai_query_log.py     # audit log of AI queries
    security/
        ir.model.access.csv
```

The `__manifest__.py` would declare `depends: ['base_api']` to inherit
authentication and the existing API infrastructure.

### Implementation Considerations

1. **Model allowlisting**: Not all models should be queryable by an AI agent.
   Maintain an `ir.config_parameter` that lists allowed models, defaulting to
   common business models (partners, products, sales orders, invoices).

2. **Field filtering**: Exclude sensitive fields (passwords, API keys, tokens)
   from AI schema responses. Use a field-level allowlist or blocklist.

3. **Query translation**: The AI endpoint translates natural language to Odoo
   domains. This can use an external LLM API or a local model. The domain is
   validated against the model's field definitions before execution.

4. **Caching**: Schema information changes infrequently. Cache the output of
   `GET /api/v2/ai/schema` per-user and invalidate on module install/update.

5. **Token budget**: When returning data to the AI, limit response sizes to stay
   within LLM context windows. Use pagination and field selection to control
   payload size.
