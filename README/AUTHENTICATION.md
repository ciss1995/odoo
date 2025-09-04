### Authentication and Roles

This document explains how login works in this project (Odoo), how sessions are managed, and how roles/permissions are enforced.

## Web login flow (backend UI)

1) Navigate to `/web/login`
- If multi-database is enabled, the page may include a database selector
- The page includes a CSRF token to protect the form submission

2) Submit credentials
- POST to `/web/login` with `login` (email/username), `password`, optional `db`
- Password is verified against the stored hash (Passlib-based hash)

3) Session creation
- On success, a server-side session is created and linked to the user (`uid`)
- A session cookie (e.g., `session_id`) is set in the browser
- The session stores user context (language, tz, company, allowed companies)

4) Redirect to the web client
- The client loads and begins making RPC calls using the session cookie
- Non-logged-in users remain as the `public` user and have very limited access

5) Logout
- Logout invalidates the server session and clears the cookie

Notes:
- The HTTP stack and routing are handled in `odoo/http.py` and the web module controllers
- CSRF protections apply to form posts; JSON-RPC uses session cookie and specific protections

## Sessions and CSRF

- Sessions: server-managed, cookie contains only a session key; user id and context live server-side
- Lifetime: configurable via server configuration (session timeout, remember options)
- CSRF: login form and other state-changing forms contain a CSRF token; rejected if missing/invalid

## JSON-RPC calls (client ↔ server)

- The web client calls JSON-RPC endpoints (e.g., `/web/dataset/call_kw`) with the session cookie
- The server uses the session to execute model methods with the correct `uid` and context
- On access errors or missing permissions, the server returns appropriate errors

## API keys (optional)

- Personal API keys can be used in place of passwords for RPC integrations
- Keys are generated per user (under user preferences) and stored hashed
- Use them with XML-RPC/JSON-RPC by providing the API key as the password for programmatic access

## Password storage and reset

- Passwords are stored as secure hashes (no plaintext)
- Reset flows can be enabled via the `auth_signup` addon (email-based reset/signup)
- Admins can set passwords directly from the Users form

## Multi-company context

- Users can belong to multiple companies; allowed companies live in the session context (`allowed_company_ids`)
- The active company affects record rules and defaults
- Users can switch company from the UI; RPC calls inherit the current company context

## Roles and permissions

Odoo enforces permissions in three layers, evaluated together:

1) Groups (roles)
- Users are assigned to groups, which represent roles
- Menus, actions, buttons, and views can be limited to specific groups via `groups` attributes in XML

2) Access Control Lists (ACLs)
- Defined in `ir.model.access.csv` per module to grant model-level permissions (read, write, create, delete) for groups
- If a group lacks permission, the operation is blocked regardless of record rules

3) Record Rules
- Domain filters attached to models that limit which records a user can see/edit
- Evaluated in addition to ACLs; can be company-aware or ownership-based

### Default roles (key groups)

- Public (anonymous): not logged in; mapped to a special shared user; can only access routes explicitly allowed to public
- Portal (`base.group_portal`): external users (customers/partners) with login; can access portal pages and their own documents
- Internal User (`base.group_user`): employees with backend access; permissions controlled by module-specific groups
- Settings / Administrator (`base.group_system` and related): manage general settings, users, and configurations
- Superuser (UID 1): bypasses access rights and record rules; use carefully, typically for maintenance

Modules often define additional groups for fine-grained roles (e.g., Sales User, Sales Manager). Assign users to these for specific app permissions.

### Superuser and administrators

- Superuser (UID 1)
  - Created automatically when a database is initialized (installing `base`).
  - Record: `env.ref('base.user_admin')` (named "Administrator").
  - Bypasses all ACLs and record rules; use only for maintenance.
  - Password:
    - Set at database creation; can be changed later via UI (Settings → Users → Administrator) or shell:
      ```python
      # python3 odoo-bin shell -c odoo.conf -d <db>
      env.ref('base.user_admin').write({'password': 'NewStrongPassword!'})
      ```
  - Identification: UID equals 1. In shell, `env.uid` shows current UID.

- Administrators (day-to-day admin)
  - Create a normal user and add the `Settings` role (`base.group_system`).
  - UI: Settings → Users & Companies → Users → Access Rights tab → Administration = Settings.
  - Promote via shell:
    ```python
    user = env['res.users'].search([('login', '=', 'you@example.com')], limit=1)
    user.write({'groups_id': [(4, env.ref('base.group_system').id)]})
    ```

Best practice: operate daily as an admin with `base.group_system`; reserve UID 1 for emergencies.

### How menus and views enforce roles

- In `views/*.xml`, add `groups="module_name.group_role"` on:
  - `menuitem` to hide/show menus
  - `record id="..." model="ir.ui.view"` to restrict view usage
  - `record id="..." model="ir.actions.act_window"` to restrict actions

### How models enforce roles

- `ir.model.access.csv`: grants model CRUD by group
- Record rules: add domain-based constraints per group or globally

## Two-factor authentication (optional)

- If enabled (e.g., via `auth_totp` or enterprise security modules), users may be prompted for a second factor after password
- Enrollment and recovery codes are managed per user

## Demo and hardcoded users

- Hardcoded credentials: none are shipped for production. Passwords are set at DB creation or during user creation.
- Special built-in users:
  - Administrator (UID 1): `env.ref('base.user_admin')`
  - Public User (anonymous): `env.ref('base.public_user')`
- Demo users:
  - Only created if you load demo data (e.g., during install with demo enabled or via specific app demo files).
  - Credentials for demo users are defined in demo XML of modules or set on first run in demo builds; never rely on them in production.
  - To avoid demo users, install with `--without-demo=all` or exclude demo at module level.
- Portal users are created when you invite contacts to portal; they are not hardcoded and must set their own passwords.

## Common admin tasks

- Add a new role:
  - Create a new group (`res.groups`) in your module data
  - Assign menus, actions, and views to the group via `groups` attributes
  - Add ACLs in `ir.model.access.csv` for relevant models
  - Optionally add record rules for domain restrictions

- Restrict a menu or button to managers:
  - Add the manager group to the `groups` attribute on the `menuitem` or view/button definition

- Impersonate for debugging:
  - Use the Odoo shell or log in as a test user with the desired group set
  - Avoid running daily work as superuser; it bypasses security

## Troubleshooting permissions

- AccessError on a model: check ACLs first, then record rules, then group membership
- Menu not visible: verify `groups` on the menu and all parent menus
- Record missing in list: likely record rule filtering; check active company and rule domains

## Configuration knobs (examples)

- Reverse proxy: set `proxy_mode = True` in `odoo.conf` if behind a proxy (ensures secure cookies, correct IP detection)
- Session cookie security: configure secure cookies and lifetime in server configuration
- Password policies and signup: configure via modules like `auth_signup` and security settings 