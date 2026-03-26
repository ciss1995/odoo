# 🚀 Odoo Base API v2 - Production Ready

## Quick Start

**ONLY API v2 exists** - API v1 was removed and replaced with this working solution.

### ⚠️ Important: Module Installation

**The `base_api` module must be installed in each database you want to use:**

```bash
# Install base_api module in your database
python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME -i base_api

# Examples:
python3 odoo-bin --addons-path=addons -d mydb -i base_api
python3 odoo-bin --addons-path=addons -d odoo_o -i base_api
python3 odoo-bin --addons-path=addons -d production -i base_api
```

**Why this is needed:** Each Odoo database is independent. Installing modules in one database doesn't affect others.

### Test API (No authentication required)
```bash
curl "http://localhost:8069/api/v2/test"
```

### Test with API Key
```bash
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/auth/test"
```

**Note**: You need to generate an API key first. See [COMPLETE_API_GUIDE.md](COMPLETE_API_GUIDE.md) for details.

## 📋 Working Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v2/test` | No | Basic test |
| GET | `/api/v2/auth/test` | Yes | Auth test |
| GET | `/api/v2/user/info` | Yes | User info |
| GET | `/api/v2/partners` | Yes | List customers |
| GET | `/api/v2/products` | Yes | List products |
| GET | `/api/v2/search/{model}` | Yes | Search any model |
| GET | `/api/v2/fields/{model}` | Yes | Get model fields |
| POST | `/api/v2/create/{model}` | Yes | Create records |
| POST | `/api/v2/auth/login` | No | Login with username/password |
| POST | `/api/v2/auth/refresh` | Session | Refresh session token |
| GET | `/api/v2/auth/me` | Session/API | Get current user info |
| POST | `/api/v2/auth/logout` | Session | Logout user |
| GET | `/api/v2/groups` | Yes | List user groups (admin) |
| GET | `/api/v2/users` | Yes | List users |
| GET | `/api/v2/users/{id}` | Yes | Get user details |
| PUT | `/api/v2/users/{id}` | Yes | Update user |
| PUT | `/api/v2/users/{id}/password` | Yes | Change password |
| POST | `/api/v2/users/{id}/reset-password` | Admin | Reset password |
| POST | `/api/v2/users/{id}/api-key` | Yes | Generate API key |
| PUT | `/api/v2/update/{model}/{id}` | Yes | Update records |
| DELETE | `/api/v2/delete/{model}/{id}` | Yes | Delete records |
| GET | `/api/v2/models` | Yes | List accessible models |
| GET | `/api/v2/modules/access` | Yes | Check module access |
| POST | `/api/v2/inventory/adjust` | Yes | Adjust inventory quantities |
| GET | `/api/v2/analytics/dashboard/summary` | Session | Cross-module KPI dashboard |
| GET | `/api/v2/analytics/crm/overview` | Session | CRM analytics |
| GET | `/api/v2/analytics/sales/overview` | Session | Sales analytics |
| GET | `/api/v2/analytics/invoicing/overview` | Session | Invoicing analytics |
| GET | `/api/v2/analytics/inventory/overview` | Session | Inventory analytics |
| GET | `/api/v2/analytics/purchases/overview` | Session | Purchase analytics |
| GET | `/api/v2/analytics/hr/overview` | Session | HR analytics |
| GET | `/api/v2/analytics/projects/overview` | Session | Project analytics |

## 🔑 Authentication Options

### Option 1: Session-Based Authentication (Recommended)
```bash
# 1. Login to get session token
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'

# 2. Use session token for API calls
curl "http://localhost:8069/api/v2/auth/me" \
     -H "session-token: YOUR_SESSION_TOKEN"

# 3. Refresh session token when needed (extends expiration)
curl -X POST "http://localhost:8069/api/v2/auth/refresh" \
     -H "session-token: YOUR_SESSION_TOKEN"

# 4. Logout when done
curl -X POST "http://localhost:8069/api/v2/auth/logout" \
     -H "session-token: YOUR_SESSION_TOKEN"
```

### Option 2: API Key Authentication
**You need to generate an API key first** - see [COMPLETE_API_GUIDE.md](COMPLETE_API_GUIDE.md) for multiple generation methods.

## 📚 Complete Documentation

**👉 See [COMPLETE_API_GUIDE.md](COMPLETE_API_GUIDE.md) for:**
- ✅ **Complete API reference** - All endpoints with examples
- ✅ **User management** - Create users, manage API keys
- ✅ **Authentication methods** - Multiple ways to get API keys
- ✅ **Business examples** - Customer, product, sales, HR, CRM
- ✅ **Programming examples** - Python, JavaScript, PHP clients
- ✅ **Production deployment** - Security, nginx, performance

**👉 See [MODEL_DISCOVERY_GUIDE.md](MODEL_DISCOVERY_GUIDE.md) for:**
- ✅ **How to find model names** - Like `res.users`, `crm.lead`, `hr.employee`
- ✅ **All available models** - CRM, HR, Sales, Accounting, Products
- ✅ **Working examples** - Test commands for each model type
- ✅ **Model naming patterns** - Understanding Odoo conventions

**👉 See [AUTHENTICATION_TROUBLESHOOTING.md](AUTHENTICATION_TROUBLESHOOTING.md) for:**
- ✅ **Common authentication issues** - "relation 'api_session' does not exist"
- ✅ **Step-by-step solutions** - Database setup, module installation
- ✅ **Quick diagnostic commands** - Test your setup quickly
- ✅ **Best practices** - Development workflow and error handling

**👉 See [ROLE_BASED_ACCESS_CONTROL_TEST.md](ROLE_BASED_ACCESS_CONTROL_TEST.md) for:**
- ✅ **Access control testing** - Comprehensive user permission tests
- ✅ **Role-based security** - Sales, HR, Accounting user restrictions
- ✅ **Security model analysis** - How Odoo's security works with the API
- ✅ **Test commands** - Verify your security implementation

## 🧪 Test API

```bash
# Health check (no auth needed)
curl "http://localhost:8069/api/v2/test"

# Test with API key
curl -H "api-key: YOUR_API_KEY" "http://localhost:8069/api/v2/auth/test"

# Test with session token
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'
```

## ✅ Status

- ✅ **Production ready** - Used in live systems
- ✅ **All endpoints working** - Fully tested and debugged
- ✅ **Dual authentication** - Session-based & API key support
- ✅ **Authentication fixed** - Proper Odoo 19 compatibility
- ✅ **Complete CRUD** - Create, read, search, update, delete
- ✅ **Any Odoo model** - Generic access to all models
- ✅ **User management** - Create/manage users and sessions
- ✅ **Analytics & Dashboards** - Cross-module KPI dashboards (session auth)
- ✅ **Inventory adjustments** - Stock quant adjustments via API
- ✅ **Comprehensive docs** - Including troubleshooting guide
- ✅ **Role-based access control** - Proper user permissions enforced

### Recent Updates (March 2026)
- ✅ **Odoo 19 upgrade** - Full compatibility with Odoo 19
- ✅ **Analytics endpoints** - Dashboard summary, CRM, Sales, Invoicing, Inventory, Purchases, HR, Projects
- ✅ **Inventory adjustment** - `POST /api/v2/inventory/adjust` for stock quant management
- ✅ **Module access check** - `GET /api/v2/modules/access` for frontend navigation
- ✅ **Session-based auth** - More secure than API keys for web apps
- ✅ **Complete user management** - Full user lifecycle API
- ✅ **Enhanced security** - Role-based access control enforced