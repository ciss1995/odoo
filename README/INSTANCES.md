### Per-company Instances: Provisioning, Access, and Operations

This guide explains how to serve one instance per company. It covers two deployment patterns, how companies access their instance, and how to stop/disable instances.

- Pattern A: Single Odoo server, multiple databases (one DB per company)
- Pattern B: Dedicated Odoo service per company (container/VM per tenant)

Both approaches are valid; choose based on isolation and operational needs.

## Pattern A — Single server, one database per company

### Overview
- One Odoo process serves multiple PostgreSQL databases.
- Each company has its own DB (e.g., `acme`, `beta`).
- Reverse proxy terminates TLS and routes all subdomains to the same Odoo backend.

### Steps (provision a new company)

1) DNS and TLS
- Create a subdomain like `acme.example.com` pointing to your reverse proxy.
- Provision an SSL certificate (e.g., via Let’s Encrypt).

2) Create the database
```bash
# create + initialize without demo data
python3 odoo-bin -c odoo.conf -d acme -i base,web --without-demo=all
```

3) Secure Odoo DB manager
- In `odoo.conf`: set `list_db = False` to hide the DB manager page.
- Set a strong `admin_password` (master password) if DB manager is enabled for maintenance.

4) Restrict served DBs with `dbfilter`
- In `odoo.conf`, enumerate allowed DBs:
```
dbfilter = ^(acme|beta|gamma)$
```
This prevents accidental exposure of other databases.

5) Give the company their access URL
- Share `https://acme.example.com/web?db=acme`
- First admin user is "Administrator" (UID 1). Change the password and create company users under Settings → Users.

6) Install required modules and configure
```bash
python3 odoo-bin -c odoo.conf -d acme -i sale,crm,account  # example
```
- Configure outgoing email (Settings → Technical → Outgoing Mail Servers) and incoming/aliases if used.

7) Branding and specifics (optional)
- If needed, install a theme or your `ui_overrides` addon in the `acme` DB.

### Optional: reverse proxy hint
- You can keep the clean URL `https://acme.example.com/web` by adding `?db=acme` automatically, e.g., Nginx rewrite for `/web` to `/web?db=acme` on that vhost.

### Stop/disable a company instance (Pattern A)
- Temporarily disable:
  - Remove the DB from `dbfilter` so it’s not served.
  - Optionally return a maintenance page from the proxy for `acme.example.com`.
- Decommission:
  - Backup DB + filestore, then drop the DB.

## Pattern B — Dedicated service per company (container/VM)

### Overview
- Each company runs its own Odoo service (separate process/container), pointing to its own DB.
- Stronger isolation; you can vary addons and Odoo version per tenant.

### Steps (Docker example)

1) DNS and TLS
- Create `acme.example.com` and provision SSL on the proxy (or inside the container if terminating TLS there).

2) PostgreSQL database
```bash
createdb -h <db_host> -p <db_port> -U <db_user> acme
```

3) Compose service (sample)
```yaml
# docker-compose.acme.yml
version: "3.8"
services:
  odoo-acme:
    image: odoo:16.0  # or your custom image
    environment:
      - HOST=<db_host>
      - PORT=<db_port>
      - USER=<db_user>
      - PASSWORD=<db_password>
      - PGDATABASE=acme
    volumes:
      - ./data/acme/filestore:/var/lib/odoo
      - ./addons:/mnt/extra-addons
    command: ["odoo", "-d", "acme", "--db-filter", "^acme$", "-i", "base,web", "--without-demo=all"]
    restart: unless-stopped
```

4) Initialize modules post-start
```bash
docker compose -f docker-compose.acme.yml exec odoo-acme odoo -d acme -u base
```

5) Reverse proxy
- Route `acme.example.com` to `odoo-acme` upstream.

### Stop/disable a company instance (Pattern B)
- Temporarily stop: `docker compose -f docker-compose.acme.yml stop`
- Disable at the proxy: serve a maintenance page instead of forwarding.
- Decommission: take backup, remove filestore directory, drop DB, remove the service.

## Access for companies (what to send clients)

- URL: `https://<company>.example.com/web` (Pattern B) or `https://<company>.example.com/web?db=<db_name>` (Pattern A)
- Admin login: the "Administrator" user created at DB initialization. Change its password on first login.
- User creation: Settings → Users & Companies → Users. Assign roles per your security guidelines.
- Support note: provide a link to your `AUTHENTICATION.md` for password resets and 2FA.

## Operational checklist

- Backups: set up nightly `pg_dump` per DB and filestore snapshots.
- Monitoring: health checks on Odoo HTTP endpoint, DB connectivity, disk space for filestores.
- Upgrades: plan per-company update windows (especially Pattern B).
- Email: configure outgoing SMTP and reply-to for each DB; align aliases per company domain if needed.
- Security: `proxy_mode = True`, secure cookies, `list_db = False`, strict `dbfilter`.

## Quick commands

- Create DB + base install (Pattern A or B):
```bash
python3 odoo-bin -c odoo.conf -d acme -i base,web --without-demo=all
```

- Install modules for a company:
```bash
python3 odoo-bin -c odoo.conf -d acme -i sale,crm
```

- Update a company’s modules:
```bash
python3 odoo-bin -c odoo.conf -d acme -u sale
```

- Hide DB manager and restrict served DBs (`odoo.conf`):
```
list_db = False
dbfilter = ^(acme|beta)$
```

## Choosing a pattern

- Prefer Pattern A if you want centralized operations and shared code, with light isolation.
- Prefer Pattern B for strong isolation, different module sets/versions, or per-tenant SLAs.

For background on databases and filestores, see `DATABASES.md`. 