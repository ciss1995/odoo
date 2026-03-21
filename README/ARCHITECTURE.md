### Architecture and Project Layout

This project is a standard Odoo codebase. It contains:
- The core Odoo framework (Python, ORM, HTTP server)
- Official Odoo addons (business apps)
- The web client (UI) and module-specific assets

Use this guide to quickly locate code and understand where to add backend vs UI changes.

## Top-level directories

- `odoo/`: Core Python framework (ORM, HTTP, RPC, server tools) and core addons under `odoo/addons/`
- `addons/`: Main Odoo apps (Accounting, CRM, Sales, Website, Web client, …)
- `odoo-bin`: Entry point script to run the server
- `requirements.txt`: Python dependencies
- `setup/`, `debian/`: Packaging and deployment files
- `doc/`: Documentation assets
- `.tx/`: Translations configuration (Transifex)
- `.github/`: GitHub templates and CI workflows

## Backend (Python)

- Core framework lives in `odoo/`:
  - `odoo/models.py`: ORM engine (business objects, CRUD, onchanges, computed fields)
  - `odoo/fields.py`: Field types and descriptors
  - `odoo/api.py`: API decorators and environment handling
  - `odoo/http.py`: HTTP layer (routing, controllers, JSON-RPC, sessions)
  - `odoo/sql_db.py`: Database connections and cursors
  - `odoo/tools/`: Utilities (dates, misc helpers, config handling)
  - `odoo/conf/`: Example configuration templates
  - `odoo/addons/base`: Base module (users, access rights, core data models)

- Addon backend code:
  - Each addon under `addons/<module>` or `odoo/addons/<module>` may include `models/` (Python models), `controllers/` (HTTP endpoints), `data/` (XML/CSV initial data), `security/` (access rules), and `views/` (UI view XML).
  - Addons are declared with `__manifest__.py` (name, depends, data, assets).

- HTTP endpoints (controllers):
  - Add in `<module>/controllers/*.py` using the `odoo.http` APIs.
  - Typical use is to expose JSON routes consumed by the web client.

## UI / Frontend

Odoo’s web client and UI assets live in addons. There is no separate Node service; assets are bundled and served by the Python server.

- Global web client: `addons/web/`
  - Contains generic client framework pieces, QWeb templates, assets, and tooling used across all apps.

- Module-specific UI:
  - `addons/<module>/static/src/` for JS/TS, SCSS, XML templates (QWeb)
    - `static/src/js` or `static/src/core` etc.: client logic and components
    - `static/src/xml`: QWeb templates
    - `static/src/scss`: styles
  - `addons/<module>/views/*.xml` for view definitions (form/tree/kanban/search) and asset bundles (e.g., `assets.xml`).
  - Register frontend assets in the module’s `__manifest__.py` or via `views/assets.xml`.

- Common UI patterns:
  - Components and templates are loaded via Odoo’s assets system and QWeb.
  - The UI consumes backend models and controllers via RPC/HTTP provided by the server.

## Typical addon structure

```
addons/
  <module_name>/
    __manifest__.py
    __init__.py
    models/
      *.py
    controllers/
      *.py
    views/
      *.xml  (views, menus, actions, assets)
    data/
      *.xml, *.csv (initial and reference data)
    security/
      ir.model.access.csv, security.xml
    static/
      src/js|xml|scss|img|...
    report/ (optional)
    wizards/ (optional)
    tests/ (optional)
```

## Running and developing

- Install deps:
  - `python3 -m venv .venv && source .venv/bin/activate`
  - `pip install -r requirements.txt`

- Create a config file (copy and edit `debian/odoo.conf` as a starting point) or pass flags directly.

- Run server (example):
  - `python3 odoo-bin -c odoo.conf --addons-path=addons,odoo/addons -d <db_name>`
  - First-time setup: add `-i base,web` to install core modules.
  - Update a module after code changes: `-u <module_name>`

- Where to put code:
  - Backend models/business logic: `addons/<module>/models/*.py`
  - HTTP endpoints: `addons/<module>/controllers/*.py`
  - Security rules: `addons/<module>/security/ir.model.access.csv`
  - Data/init records: `addons/<module>/data/*.xml`
  - Views and actions: `addons/<module>/views/*.xml`
  - UI components/assets: `addons/<module>/static/src/(js|xml|scss)/`

- Tests:
  - Python tests in `addons/<module>/tests/`.
  - Run with the Odoo test runner: `python3 odoo-bin -d <db> -i <module> --test-enable`.

## Finding things quickly

- Backend core: `odoo/` (look up ORM, HTTP, fields, tools)
- Core addons: `odoo/addons/` (e.g., `base`)
- Business apps and the web client: `addons/` (e.g., `addons/web`, `addons/sale`, `addons/crm`)
- UI assets and components: `addons/<module>/static/src/`
- View declarations and menus: `addons/<module>/views/*.xml`
- Access control: `addons/<module>/security/`
- Packaging and deployment: `setup/`, `debian/`
- Translations: `addons/<module>/i18n/` and `.tx/`

## Key entry points (for reference)

- Server entry:

```startLine:endLine:odoo-bin
#!/usr/bin/env python3

# set server timezone in UTC before time module imported
__import__('os').environ['TZ'] = 'UTC'
import odoo

if __name__ == "__main__":
    odoo.cli.main()
```

- HTTP layer and routing: see `odoo/http.py`
- ORM: see `odoo/models.py` and `odoo/fields.py`

If you’re unsure where a feature lives, start by finding its addon in `addons/`, check its `__manifest__.py` for declared data/assets, and then open its `models/`, `controllers/`, `views/`, and `static/` folders. 