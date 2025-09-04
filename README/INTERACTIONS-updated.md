### Interactions and Module Lifecycle

This guide explains how core parts of the system interact at runtime and how to add, update, or remove addons. **Includes specific guidance for API modules like base_api.**

## High-level runtime flow

1) Server startup
- Config is loaded (`odoo.conf` or CLI flags)
- Database connection is established (`odoo/sql_db.py`)
- The registry is built per database: manifests are parsed, dependencies resolved, models registered, views compiled
- **API routes are registered from controllers during startup**

2) HTTP request lifecycle
- Incoming request hits `odoo/http.py`
- **API requests to `/api/v2/*` are routed to base_api controllers**
- Session/auth are handled; a request-bound environment (`request.env`) is created
- **API authentication via API keys or session tokens**
- Routing dispatch calls a controller method (if matched) or the JSON-RPC endpoints
- A DB cursor/transaction wraps the request. On success: commit; on error: rollback

3) Client-server data access (RPC)
- The web client generally calls model methods through JSON-RPC (`/web/dataset/call_kw`)
- **External applications use RESTful API endpoints (`/api/v2/*`)**
- The server executes the ORM method (`odoo/models.py`) on `request.env["<model>"]`
- Responses are serialized to JSON and returned to the client

4) UI rendering and assets
- Views (XML `ir.ui.view`) are requested and rendered by the client using QWeb templates and assets
- Asset bundles (`web.assets_*`) include JS/TS, CSS/SCSS, and templates provided by addons

## How files interact

- `__manifest__.py`
  - Declares the module, dependencies (`depends`), data files (`data`), demo files (`demo`), and assets
  - Controls load order via dependencies and includes security, views, and data
  - **For API modules: often minimal dependencies (`base`, `web`) and no UI assets**

- Backend models: `models/*.py`
  - Define business objects (models), fields, computed methods, constraints, onchange, business logic
  - Exposed via RPC (e.g., `name_get`, `search_read`, custom methods with `@api.model`, `@api.depends`, etc.)
  - **API modules may include authentication models (API keys, sessions)**

- Controllers: `controllers/*.py`
  - HTTP routes via `@http.route` for JSON/HTTP endpoints
  - Use `request.env` to interact with models
  - **API controllers handle authentication, rate limiting, and data serialization**

- Views and actions: `views/*.xml`
  - Records for `ir.ui.view` (form/tree/kanban/search) and `ir.actions.*` (window actions, server actions)
  - Menus (`ir.ui.menu`) link to actions; actions reference models and views
  - **API modules typically have minimal or no UI views**

- Security: `security/ir.model.access.csv`, `security/*.xml`
  - Access control lists and record rules enforce permissions per model
  - **Critical for API modules to control data access**

- Data and demo: `data/*.xml`, `demo/*.xml`
  - Initial records (sequences, mail templates, server actions, settings) and sample/demo data
  - **API modules might include default API key setups or demo users**

- Assets and UI components: `static/src/(js|xml|scss|img)`
  - Frontend logic and presentation (QWeb templates, styles, images)
  - Included into bundles like `web.assets_backend`, `web.assets_frontend`, `web.assets_tests`
  - **API modules typically don't include frontend assets**

## Typical end-to-end flows

### Standard Odoo Flow
- Menu click → action → view → model
  - A menu (`ir.ui.menu`) triggers an action (`ir.actions.act_window`)
  - The action targets a model and a view type; the client loads the view definition
  - The UI requests records via RPC to the model methods

### API Flow (NEW)
- **External application → API endpoint → authentication → model access**
  - External app makes HTTP request to `/api/v2/partners`
  - base_api controller authenticates via API key or session token
  - Controller calls Odoo model methods (`env['res.partner'].search_read()`)
  - Data is serialized to JSON and returned

### Internal RPC Flow
- UI component → RPC → model method
  - JS component calls `/web/dataset/call_kw` with model/method
  - Server executes ORM code; result is serialized back to the UI

### Custom Controller Flow
- Controller route → custom JSON/HTTP API
  - `@http.route` matches the path
  - Python controller executes business logic and returns JSON/HTML

## Module (addon) lifecycle

### Install (`-i <module>` or via Apps UI)
- Manifests are read, dependencies installed first
- Models, views, data, security are loaded; computed fields and constraints initialized
- **API routes are registered and become available immediately**
- Demo data is loaded unless disabled

### Update (`-u <module>`)
- Re-reads manifests and data files and applies XML/CSV updates
- Upgrades models/views/assets; migration scripts (if any) can run in `pre_init_hook`/`post_init_hook`
- **API route changes take effect after restart or in development mode**

### Uninstall (Apps UI → Uninstall)
- Removes data defined in XML/CSV (via `ir.model.data` references) and model structures
- Runs `uninstall_hook` if defined in `__manifest__.py`
- **API routes are unregistered and no longer accessible**
- Blocks uninstall if other modules depend on it; uninstall dependents first

## Adding a new API-enabled addon

### 1) Create the module structure under `addons/<your_api_module>`
```
addons/
  your_api_module/
    __manifest__.py
    __init__.py
    models/
      __init__.py
      api_session.py      # API session management
      api_key.py          # API key model (if custom)
    controllers/
      __init__.py
      api_controller.py   # Main API endpoints
    security/
      ir.model.access.csv # API model access rights
      api_security.xml    # API access groups
    tests/
      test_api.py         # API endpoint tests
```

### 2) API Module `__manifest__.py` example
```python
{
    "name": "Your API Module",
    "version": "1.0",
    "depends": ["base", "web"],  # Minimal dependencies
    "data": [
        "security/api_security.xml",
        "security/ir.model.access.csv",
        "data/api_defaults.xml",
    ],
    "external_dependencies": {
        "python": ["requests"],  # If needed for external calls
    },
    "installable": True,
    "auto_install": False,
}
```

### 3) API Controller Structure
```python
# controllers/api_controller.py
from odoo import http
from odoo.http import request
import json

class YourApiController(http.Controller):
    
    def _authenticate(self):
        """API authentication logic"""
        api_key = request.httprequest.headers.get('api-key')
        # ... authentication logic
        
    @http.route('/api/v1/your-endpoint', type='http', auth='none', methods=['GET'], csrf=False)
    def your_api_endpoint(self, **kwargs):
        """Your API endpoint"""
        authenticated, auth_response = self._authenticate()
        if not authenticated:
            return auth_response
            
        # Your API logic here
        data = request.env['your.model'].search_read([])
        
        return request.make_response(
            json.dumps({'success': True, 'data': data}),
            headers=[('Content-Type', 'application/json')]
        )
```

### 4) Install and Test
- Place the module under a path included in `--addons-path`
- Install via Apps UI or CLI:

```bash
python3 odoo-bin -c odoo.conf -d <db> -i your_api_module
```

- Test your API endpoints:
```bash
curl "http://localhost:8069/api/v1/your-endpoint" -H "api-key: your_key"
```

### 5) Update during development
```bash
python3 odoo-bin -c odoo.conf -d <db> -u your_api_module --dev=reload
```

**API Development Tips:**
- Use `--dev=reload` for automatic code reloading
- API routes don't require asset recompilation
- Test with `curl` or Postman during development
- Use proper HTTP status codes (200, 401, 403, 404, 500)

## base_api Module Specific Lifecycle

### Installation
```bash
# Install base_api module
python3 odoo-bin --addons-path=addons -d your_db -i base_api

# Verify installation
curl "http://localhost:8069/api/v2/test"
```

### Available Endpoints After Installation
The base_api module provides these endpoints immediately:
- `GET /api/v2/test` - Basic connectivity test
- `GET /api/v2/auth/test` - Authentication test
- `POST /api/v2/auth/login` - User login
- `GET /api/v2/partners` - List partners/customers
- `GET /api/v2/products` - List products
- `GET /api/v2/search/{model}` - Search any model
- `POST /api/v2/create/{model}` - Create records

### Configuration After Installation
```python
# Generate API keys for users
user = env['res.users'].browse(user_id)
api_key = user.generate_api_key()

# Or via API endpoint
curl -X POST "http://localhost:8069/api/v2/users/2/api-key" \
     -H "session-token: YOUR_SESSION_TOKEN"
```

## Removing an addon

Preferred: use the Apps UI → search your module → Uninstall

**For API modules:**
- Ensure no external applications are actively using the API
- Backup any API usage logs or analytics
- Consider deprecation period with proper API versioning

Command-line approach (advanced):
- You can script uninstall through the server by calling the uninstall button on `ir.module.module` via RPC, but the UI is safer

Uninstall behavior and caveats:
- Dependencies: if other modules depend on yours, uninstall them first
- **API routes: all API endpoints will become unavailable immediately**
- Data cleanup: define an `uninstall_hook` in `__manifest__.py` for custom cleanup if needed
- Orphan records: records created dynamically (not via XML IDs) may persist; handle in `uninstall_hook` or via model `ondelete` policies

## API Development Best Practices

### Version Management
```python
# Use versioned API routes
@http.route('/api/v2/partners', ...)  # Current version
@http.route('/api/v1/partners', ...)  # Legacy support

# Deprecation headers
response.headers['API-Deprecation'] = 'true'
response.headers['API-Sunset'] = '2024-12-31'
```

### Error Handling
```python
def _error_response(self, message, status_code=400, error_code=None):
    """Standardized error responses"""
    return request.make_response(
        json.dumps({
            'success': False,
            'error': {
                'message': message,
                'code': error_code,
                'timestamp': datetime.now().isoformat()
            }
        }),
        status=status_code,
        headers=[('Content-Type', 'application/json')]
    )
```

### Authentication Patterns
```python
# Multiple authentication methods
def _authenticate(self):
    """Support multiple auth methods"""
    # Try API key first
    api_key = request.httprequest.headers.get('api-key')
    if api_key:
        return self._authenticate_api_key(api_key)
    
    # Try session token
    session_token = request.httprequest.headers.get('session-token')
    if session_token:
        return self._authenticate_session(session_token)
    
    return False, self._error_response("Authentication required", 401)
```

## Useful server flags

- `-i, --init <modules>`: install modules
- `-u, --update <modules>`: update modules
- `--addons-path=<paths>`: comma-separated addon search paths
- `--without-demo=all` or `--without-demo=<modules>`: skip demo records
- `--dev=reload,qweb,assets` (subset as needed): developer conveniences
- **`--dev=reload`**: Essential for API development - auto-reloads Python code
- `--log-level=debug_sql` (or `--log-level=debug_rpc`): verbose logs for debugging

## API Testing and Validation

### Automated Testing
```python
# tests/test_api.py
from odoo.tests import TransactionCase
import json

class TestYourAPI(TransactionCase):
    
    def test_api_authentication(self):
        """Test API key authentication"""
        # Create test API key
        api_key = self.env['res.users'].browse(1).generate_api_key()
        
        # Test endpoint
        response = self.env['ir.http']._handle_request(
            '/api/v2/test',
            headers={'api-key': api_key}
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.get_data())
        self.assertTrue(data['success'])
```

### Performance Testing
```bash
# Load testing with curl
for i in {1..100}; do
    curl -s "http://localhost:8069/api/v2/partners?limit=10" \
         -H "api-key: $API_KEY" &
done
wait
```

## Debugging pointers

- Use `--log-level=debug` (or `debug_sql`, `debug_rpc`) to trace operations
- **API debugging: Check request logs for authentication and routing issues**
- Inspect the registry via the Odoo shell: `python3 odoo-bin shell -c odoo.conf -d <db>`
- **Test API endpoints with curl/Postman before building applications**
- In tests, leverage `SavepointCase` and `TransactionCase` in `addons/<module>/tests/`

## API Module Dependencies

### Common Dependencies for API Modules
```python
# __manifest__.py
{
    "depends": [
        "base",           # Core Odoo models
        "web",            # Web framework
        "mail",           # If using messaging features
        "portal",         # If supporting portal users
    ],
    "external_dependencies": {
        "python": [
            "requests",   # For external API calls
            "jwt",        # For JWT token handling
            "cryptography", # For advanced encryption
        ],
    },
}
```

### Integration Patterns
```python
# Extending existing models for API access
class Partner(models.Model):
    _inherit = 'res.partner'
    
    api_access_level = fields.Selection([
        ('none', 'No Access'),
        ('read', 'Read Only'),
        ('write', 'Read/Write'),
    ], default='none')
    
    def api_search_read(self, domain=None, fields=None, limit=None):
        """API-specific search method with access control"""
        if self.env.user.partner_id.api_access_level == 'none':
            raise AccessError("API access denied")
        
        return self.search_read(domain, fields, limit=limit)
```

This enhanced guide now includes comprehensive coverage of API module development, lifecycle management, and integration patterns specific to the base_api module architecture.
