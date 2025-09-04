# 🔧 Authentication Troubleshooting Guide

## Common Authentication Issues & Solutions

### Issue 1: "relation 'api_session' does not exist"

**Error Message:**
```
ERROR mydb odoo.addons.base_api.controllers.simple_api: Authentication error for user admin: relation "api_session" does not exist
```

**Cause:** The `base_api` module is not installed in the database you're trying to use.

**Solution:**
```bash
# Install the base_api module in your specific database
python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME -i base_api

# Examples:
python3 odoo-bin --addons-path=addons -d mydb -i base_api
python3 odoo-bin --addons-path=addons -d odoo_o -i base_api
python3 odoo-bin --addons-path=addons -d production -i base_api
```

**Why this happens:** Each Odoo database is completely independent. Installing modules in one database doesn't affect others.

### Issue 2: "404 Not Found" for API endpoints

**Error Message:**
```html
<!doctype html>
<html lang=en>
<title>404 Not Found</title>
<h1>Not Found</h1>
```

**Cause:** The `base_api` module routes are not registered.

**Solution:**
1. **Check if base_api module is installed:**
   ```bash
   # Test basic endpoint
   curl "http://localhost:8069/api/v2/test"
   ```

2. **If 404, install the module:**
   ```bash
   python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME -i base_api
   ```

### Issue 3: "Authentication failed" with correct credentials

**Error Message:**
```json
{
  "success": false,
  "error": {
    "message": "Authentication failed",
    "code": "AUTH_FAILED"
  }
}
```

**Causes & Solutions:**

#### A) Wrong credentials
```bash
# Check available users first
curl "http://localhost:8069/api/v2/users" \
     -H "session-token: VALID_SESSION_TOKEN"

# Try with correct username (usually 'admin' not 'administrator')
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'
```

#### B) Database not configured
```bash
# Check Odoo logs for database errors
# Restart Odoo with proper database name:
python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME
```

#### C) User account inactive
```bash
# Login as admin and check user status via Odoo web interface
# Or reactivate via API if you have access
```

### Issue 4: "User not found" for specific user ID

**Error Message:**
```json
{
  "success": false,
  "error": {
    "message": "User not found",
    "code": "USER_NOT_FOUND"
  }
}
```

**Solution:**
```bash
# 1. First, list all users to see available IDs
curl "http://localhost:8069/api/v2/users" \
     -H "session-token: YOUR_SESSION_TOKEN"

# 2. Use a valid user ID from the response
# Example: if users are [2, 6, 7], don't try to access user 9
curl "http://localhost:8069/api/v2/users/2" \
     -H "session-token: YOUR_SESSION_TOKEN"
```

### Issue 5: Session token expired or invalid

**Error Message:**
```json
{
  "success": false,
  "error": {
    "message": "Invalid or expired session",
    "code": "INVALID_SESSION"
  }
}
```

**Solution:**
```bash
# 1. Login again to get fresh session token
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'

# 2. Use the new session token
curl "http://localhost:8069/api/v2/auth/me" \
     -H "session-token: NEW_SESSION_TOKEN_FROM_LOGIN"
```

### Issue 6: API key authentication not working

**Error Message:**
```json
{
  "success": false,
  "error": {
    "message": "Invalid API key",
    "code": "INVALID_API_KEY"
  }
}
```

**Solutions:**

#### A) Generate new API key
```bash
# Method 1: Use existing working API key
curl "http://localhost:8069/api/v2/users/2/api-key" \
     -X POST \
     -H "api-key: YOUR_API_KEY"

# Method 2: Login with session first
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'

curl "http://localhost:8069/api/v2/users/2/api-key" \
     -X POST \
     -H "session-token: YOUR_SESSION_TOKEN"
```

#### B) Use working API key
```bash
# Test with known working API key
curl "http://localhost:8069/api/v2/auth/test" \
     -H "api-key: YOUR_API_KEY"
```

## Quick Diagnostic Commands

### 1. Test API availability
```bash
curl "http://localhost:8069/api/v2/test"
# Expected: {"success": true, "data": {"message": "API v2 is working!", "version": "2.0"}}
```

### 2. Test authentication methods
```bash
# Session-based
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'

# API key-based  
curl "http://localhost:8069/api/v2/auth/test" \
     -H "api-key: YOUR_API_KEY"
```

### 3. Check Odoo server status
```bash
# Check if server is running
ps aux | grep odoo-bin | grep -v grep

# Check port
netstat -an | grep LISTEN | grep 8069

# Check logs
tail -f odoo.log  # or wherever your logs are
```

### 4. Verify database setup
```bash
# Start server with specific database
python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME --log-level=info

# Install base_api if needed
python3 odoo-bin --addons-path=addons -d YOUR_DATABASE_NAME -i base_api
```

## Best Practices

### 1. Database Management
- **Always install `base_api` in each database you use**
- **Use consistent database names** (avoid special characters)
- **Keep track of which databases have which modules installed**

### 2. Authentication Strategy
- **Use session-based auth for web applications**
- **Use API keys for server-to-server integrations**
- **Implement proper token refresh logic**
- **Always logout when done with sessions**

### 3. Error Handling
- **Check HTTP status codes first** (200, 401, 403, 404, 500)
- **Parse JSON error responses** for specific error codes
- **Implement retry logic** with exponential backoff
- **Log authentication attempts** for debugging

### 4. Development Workflow
```bash
# 1. Start with clean database
python3 odoo-bin --addons-path=addons -d testdb -i base_api

# 2. Test basic endpoint
curl "http://localhost:8069/api/v2/test"

# 3. Test authentication
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "admin"}'

# 4. Use session token for API calls
curl "http://localhost:8069/api/v2/auth/me" \
     -H "session-token: YOUR_SESSION_TOKEN"
```

## Contact & Support

If you're still experiencing issues after following this guide:

1. **Check the logs** for specific error messages
2. **Verify module installation** in the correct database
3. **Test with working examples** from this guide
4. **Use session-based authentication** as it's more reliable than API keys

Remember: **Each database is independent** - this is the most common source of configuration issues!
