### Databases: Storage, Creation, Multi-company, and Maintenance

This document explains where your data lives, how to create databases, whether to use one DB per company or multi-company in a single DB, how to apply per-company customizations, and how to disable/stop a DB.

## Where is data stored?

- PostgreSQL database
  - All business data (models, records, users) lives in PostgreSQL.
  - Each Odoo database is a separate PostgreSQL database identified by its name (e.g., `mycompany_prod`).
  - Connection settings come from `odoo.conf` (`db_host`, `db_port`, `db_user`, `db_password`).

- Filestore (binary attachments on disk)
  - Large binaries (attachments, images, reports) are stored on the filesystem, not in PostgreSQL.
  - Path: `<data_dir>/filestore/<db_name>/...`
  - `data_dir` is set in `odoo.conf`. If not set, Odoo uses a platform-specific user data directory. In many dev setups it’s configured to a local path (commonly `odoo/filestore/`).

- Logs and other artifacts
  - Logs go to stdout or to the file set by `logfile` in `odoo.conf`.

## Creating a new database

You can create a database via the web DB manager or via CLI/SQL.

### Web DB Manager (recommended)

1) Ensure DB manager is accessible (`list_db = True` in `odoo.conf`).
2) Open `/web/database/manager` (or `/web` → Manage Databases when logged out).
3) Click “Create database” and provide:
   - Database name, email/login for Administrator, password, language, demo data option.
4) On success, the new DB is initialized (installs `base` and core modules) and ready to use.

### CLI (auto-create on first run)

If your PostgreSQL user has `CREATEDB` rights:
```bash
python3 odoo-bin -c odoo.conf -d mycompany_prod -i base --without-demo=all
```
- `-d` selects the DB name; if it does not exist and the DB user can create DBs, Odoo will create it.
- Add modules during init with `-i base,web,<other_modules>`.

### Manual (SQL) + init

Create the DB in PostgreSQL, then let Odoo initialize it:
```bash
createdb -h <db_host> -p <db_port> -U <db_user> mycompany_prod
python3 odoo-bin -c odoo.conf -d mycompany_prod -i base --without-demo=all
```

## One DB per company vs single DB with multi-company

- Single DB (multi-company) when:
  - Companies share users or workflows
  - You want consolidated reporting across companies
  - You don’t need different module sets per company

- One DB per company when:
  - Strict data isolation is required (legal/compliance)
  - Each company needs different module sets or heavy customizations
  - Separate upgrade/maintenance windows per company

Important constraint: module installation is DB-wide. In a single DB, you cannot “install a module for one company only”. Use groups/ACLs to show/hide features, but code and schema are shared.

## Per-company customization

In a single DB with multiple companies:
- Company fields and record rules
  - Many core models have `company_id`. Record rules restrict visibility by company.
  - Define record rules to enforce company boundaries where needed.

- Company-dependent fields
  - Use `company_dependent=True` on fields to store different values per company (via `ir.property`).
  - Typical for prices, accounts, journals, and configuration parameters.

- Company-specific settings and defaults
  - Use `ir.property` for per-company defaults and settings.
  - Use `res.config.settings` to expose company-scoped configuration UIs.

- UI differences by company
  - Gate menus/views with groups bound to company-specific groups.
  - The active company in the user session drives which data appears due to record rules.

In a one-DB-per-company setup:
- Install different modules per DB as needed.
- Keep separate configurations, users, and filestores per DB.

## Routing multiple databases (subdomains and dbfilter)

Use `dbfilter` in `odoo.conf` to control which databases the server serves and route requests by hostname:

- Only serve specific DBs:
```
dbfilter = ^(mycompany_prod|sandbox)$
```

- Map subdomain to DB name (common pattern):
```
# If the DB name equals the subdomain (without dots)
# %h is the full host (e.g., acme.example.com)
# %d is the database; you can use a regex to extract the subdomain
# Common example using the host:
dbfilter = ^%h$
# Or stricter (extract subdomain and match DB names you expect)
```

Test your `dbfilter` and ensure only intended DBs are visible/served.

## Backups and restores

- Web backup (if enabled): `/web/database/backup` via DB manager (requires master password)
- PostgreSQL recommended:
```bash
# Backup
pg_dump -h <db_host> -p <db_port> -U <db_user> -Fc -f mycompany_prod.dump mycompany_prod
# Restore
pg_restore -h <db_host> -p <db_port> -U <db_user> -C -d postgres mycompany_prod.dump
```
- Remember to also snapshot/copy the filestore directory: `<data_dir>/filestore/<db_name>/`

## Disabling or “stopping” a database

Options depending on your goal:

- Temporarily stop serving a DB from this Odoo instance
  - Adjust `dbfilter` so the DB no longer matches. The server will not list or accept requests for it.
  - Example: remove it from the allowed list or tighten the regex.

- Put behind maintenance
  - Use a reverse proxy to return a maintenance page for that host/subdomain.
  - Alternatively, set `dbfilter` to exclude it and host a static page elsewhere.

- Lock access at PostgreSQL level (strong block)
  - Revoke connect for the Odoo DB user or change its password (affects all DBs using that user):
    ```sql
    REVOKE CONNECT ON DATABASE mycompany_prod FROM PUBLIC;
    -- Or restrict the specific role used by Odoo
    ```
  - Safer: change Odoo’s `dbfilter` rather than database privileges unless you are decommissioning.

- Archive or remove
  - Backup DB + filestore, then drop via DB manager (`/web/database/manager`) or SQL:
    ```sql
    DROP DATABASE mycompany_prod;
    ```

## Creating companies inside a single DB

- Add companies via Settings → Companies. Users can be assigned allowed companies.
- Multi-company behavior:
  - User context contains `allowed_company_ids` and current company
  - Record rules and `company_id` fields handle separation
  - Use company-dependent fields and `ir.property` for per-company configuration

## Useful configuration keys (odoo.conf)

- `db_host`, `db_port`, `db_user`, `db_password`: PostgreSQL connection
- `dbfilter`: which DBs to serve, optionally routing by host
- `list_db`: show database manager (True/False)
- `data_dir`: base directory for filestore (attachments)

## Quick recipes

- Create a new DB without demo data:
```bash
python3 odoo-bin -c odoo.conf -d acme_prod -i base,web --without-demo=all
```

- Serve only two DBs:
```
dbfilter = ^(acme_prod|beta)$
```

- Single DB, per-company settings:
  - Add `company_dependent=True` fields; expose via `res.config.settings`.
  - Use record rules and groups for visibility and UI differences.

- One DB per company:
  - Create separate DBs; install different modules per DB as needed.
  - Route with `dbfilter` + subdomains; manage backups per DB and filestore. 