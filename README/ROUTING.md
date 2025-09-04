### Routing: Link Subdomains to Databases

This guide shows how to route `company.example.com` to the corresponding Odoo database (e.g., `company`) using your reverse proxy and Odoo configuration.

## Key ideas

- Odoo can serve multiple databases. You select which DB to use via the `db` query parameter or restrict/route using `dbfilter`.
- The most reliable approach is to have the reverse proxy append `?db=<name>` based on the subdomain and to restrict which DBs are served using `dbfilter`.
- Always run behind a reverse proxy with `proxy_mode = True` for correct headers/cookies.

## Odoo configuration (`odoo.conf`)

```ini
[options]
proxy_mode = True
list_db = False
# Serve only the intended databases (add all tenant DB names here)
dbfilter = ^(acme|beta|gamma)$
```

- `proxy_mode = True`: respect `X-Forwarded-*` headers from your proxy
- `list_db = False`: hide DB manager in production
- `dbfilter`: only serve listed DBs; blocks access to others even if reachable at PostgreSQL level

## Nginx example

Goal: map `acme.example.com` → DB `acme`, `beta.example.com` → DB `beta` by adding `?db=<name>` automatically.

At the `http` level, define an upstream and a host→db map:

```nginx
upstream odoo_backend {
    server 127.0.0.1:8069;
}

map $host $odoo_db {
    default "";
    acme.example.com acme;
    beta.example.com beta;
}
```

Server block for all tenants on this host:

```nginx
server {
    listen 80;
    server_name acme.example.com beta.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name acme.example.com beta.example.com;

    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    # Common proxy headers
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP $remote_addr;

    # 1) Ensure /web requests carry the db parameter
    location ^~ /web {
        if ($odoo_db != "") {
            if ($arg_db = "") {
                return 302 /web?db=$odoo_db;
            }
        }
        proxy_pass http://odoo_backend;
    }

    # 2) Optionally redirect bare root to the correct DB
    location = / {
        if ($odoo_db != "") {
            return 302 /web?db=$odoo_db;
        }
        proxy_pass http://odoo_backend;
    }

    # 3) Pass everything else through (assets, APIs, etc.)
    location / {
        proxy_pass http://odoo_backend;
    }
}
```

Notes:
- The small 302 ensures users land on the right DB without typing `?db=...`.
- Keep `dbfilter` in sync with tenants so only intended DBs are served.
- Add additional `server_name` entries and `map` entries as you onboard more companies.

## Caddy example

For a single tenant per vhost:

```caddy
acme.example.com {
    encode gzip

    @needsdb not query db
    redir @needsdb /web?db=acme 302

    reverse_proxy 127.0.0.1:8069 {
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
        header_up X-Real-IP {remote_host}
    }
}
```

For multiple tenants, create one site block per host and adjust the `redir` to the target DB name.

## Traefik (labels) sketch

Attach labels to a per-tenant router/service and add a middleware redirect:

```yaml
labels:
  - "traefik.http.routers.acme.rule=Host(`acme.example.com`)"
  - "traefik.http.routers.acme.entrypoints=websecure"
  - "traefik.http.routers.acme.tls=true"
  - "traefik.http.middlewares.acme-db.redirectregex.regex=^(https?://[^/]+)/(?:$|\?[^#]*)$"
  - "traefik.http.middlewares.acme-db.redirectregex.replacement=$1/web?db=acme"
  - "traefik.http.routers.acme.middlewares=acme-db"
  - "traefik.http.services.acme.loadbalancer.server.port=8069"
```

## Verifying the setup

- Visit `https://acme.example.com` → should redirect to `/web?db=acme`
- Log in and ensure the UI shows only `acme` data
- Visiting `https://beta.example.com` should land on `/web?db=beta`
- Attempting to access a non-listed DB should fail due to `dbfilter`

## Troubleshooting

- Stuck on login screen or wrong company data:
  - Check that the `db` parameter is present after redirection
  - Confirm `dbfilter` includes the target DB name and excludes others
  - Ensure `proxy_mode = True` (cookies and redirects rely on correct headers)

- Assets not loading:
  - Confirm generic `location /` proxies to Odoo without redirection to avoid affecting asset URLs

- Multiple proxies:
  - Ensure only the edge proxy performs the redirect; inner proxies should just pass through

## Alternative approach (dedicated instances)

Run one Odoo service per tenant with `--db-filter ^acme$` and a dedicated vhost pointing directly to that service. This avoids query-param redirection but requires more infrastructure. See `INSTANCES.md` Pattern B. 