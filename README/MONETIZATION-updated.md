### Monetization and Access Control for Paid Modules

This guide describes how to offer paid modules/features and revoke access when a user or company stops paying, without deleting data. **This includes controlling access to the API endpoints provided by the base_api module.**

## Strategy overview

- Keep modules installed; control access via entitlements
- Map entitlements to security groups and record rules
- **Control API access via entitlements and API key permissions**
- Sync entitlements from your billing provider (webhooks + cron)
- Provide clear UX (banners, disabled menus) and a grace period

## Entitlement model

Create a simple entitlement model (in your own addon, e.g., `addons/billing_entitlement`) to represent who owns which features:

- `license.license` or `subscription.subscription`:
  - `company_id` (or `partner_id` if per-customer)
  - `module_key` (e.g., `crm_pro`, `advanced_reports`, `api_access`)
  - `state` (`active`, `grace`, `expired`, `canceled`)
  - `valid_until` (date)
  - `users_domain` or user list (optional: restrict to specific users)
  - **`api_access_level`** (`none`, `read_only`, `full_access`)

Map each `module_key` to one or more security groups (see below).

## Enforcement mechanisms

Prefer access control over code branching. Combine these layers:

### 1) Security groups (primary)
- Create a group per paid module: `group_paid_crm_pro`, `group_paid_advanced_reports`, `group_paid_api_access`
- Grant menus (`ir.ui.menu`), actions (`ir.actions.*`), and views access via `groups` attribute
- In `ir.model.access.csv`, grant read/write/create/unlink only to these groups as needed
- Entitlement on → add users to the group; entitlement off → remove users from the group

### 2) API Access Control (NEW)
**For controlling access to base_api endpoints:**

```python
# In your billing_entitlement addon
class ApiAccessController(http.Controller):
    
    def _check_api_entitlement(self, user, endpoint_type='read'):
        """Check if user has API access entitlement"""
        entitlement = self.env['license.license'].search([
            ('company_id', '=', user.company_id.id),
            ('module_key', '=', 'api_access'),
            ('state', 'in', ['active', 'grace'])
        ], limit=1)
        
        if not entitlement:
            return False
            
        if endpoint_type == 'write' and entitlement.api_access_level == 'read_only':
            return False
            
        return True
    
    @http.route('/api/v2/protected/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
    def protected_api_access(self, model, **kwargs):
        """Example of protected API endpoint"""
        # First do standard API authentication
        authenticated, auth_response = self._authenticate()
        if not authenticated:
            return auth_response
            
        # Then check entitlement
        if not self._check_api_entitlement(request.env.user, 'read'):
            return self._error_response("API access not included in your plan", 402, "PAYMENT_REQUIRED")
            
        # Proceed with normal API logic
        return self._handle_api_request(model, **kwargs)
```

### 3) API Key Entitlement Control
**Extend the base_api authentication to check entitlements:**

```python
# Override in your billing addon
def _authenticate_with_entitlement(self):
    """Enhanced authentication that checks entitlements"""
    api_key = request.httprequest.headers.get('api-key')
    if not api_key:
        return False, self._error_response("Missing API key", 401, "MISSING_API_KEY")
    
    # Standard API key validation
    try:
        api_key_record = request.env['res.users.apikeys'].sudo().search([
            ('key', '=', api_key)
        ], limit=1)
        
        if not api_key_record or not api_key_record.user_id.active:
            return False, self._error_response("Invalid API key", 403, "INVALID_API_KEY")
            
        user = api_key_record.user_id
        
        # NEW: Check API entitlement
        entitlement = request.env['license.license'].search([
            ('company_id', '=', user.company_id.id),
            ('module_key', '=', 'api_access'),
            ('state', 'in', ['active', 'grace'])
        ], limit=1)
        
        if not entitlement:
            return False, self._error_response("API access expired", 402, "PAYMENT_REQUIRED")
            
        request.env = request.env(user=user.id)
        return True, user
        
    except Exception as e:
        return False, self._error_response(f"Authentication error: {str(e)}", 500, "AUTH_ERROR")
```

### 4) Record rules (optional)
- Further restrict domain-level access if needed (e.g., per company or plan limits)

### 5) Controller guards (for custom HTTP APIs)
- In `controllers/*.py`, check entitlement before serving endpoints; raise `AccessError` or return 402

### 6) Feature flags (optional)
- Use `ir.config_parameter` booleans or a service to quickly toggle features (use sparingly; groups/ACLs should suffice)

## Wiring menus and views to groups

- Add `groups="module_name.group_paid_crm_pro"` on menus, views, and actions in `views/*.xml`
- Users without the group will not see the menus or open the views

Example snippet:
```xml
<menuitem id="menu_crm_pro" name="CRM Pro"
          parent="crm.menu_crm_root"
          action="action_crm_pro"
          groups="module_name.group_paid_crm_pro"/>

<!-- API Access Group -->
<record id="group_paid_api_access" model="res.groups">
    <field name="name">Paid: API Access</field>
    <field name="category_id" ref="base.module_category_hidden"/>
</record>
```

## API-Specific Monetization Strategies

### 1) Tiered API Access
```python
# Different API access levels
API_TIERS = {
    'basic': {
        'requests_per_hour': 100,
        'endpoints': ['partners', 'products'],
        'write_access': False
    },
    'professional': {
        'requests_per_hour': 1000,
        'endpoints': ['partners', 'products', 'search', 'users'],
        'write_access': True
    },
    'enterprise': {
        'requests_per_hour': 10000,
        'endpoints': '*',  # All endpoints
        'write_access': True
    }
}
```

### 2) Rate Limiting by Plan
```python
def _check_rate_limit(self, user):
    """Check if user has exceeded their plan's rate limit"""
    entitlement = self._get_user_entitlement(user)
    tier = entitlement.api_tier or 'basic'
    
    # Check requests in last hour
    request_count = self._get_recent_request_count(user, hours=1)
    limit = API_TIERS[tier]['requests_per_hour']
    
    if request_count >= limit:
        return False, self._error_response(
            f"Rate limit exceeded. Upgrade to increase limits.", 
            429, "RATE_LIMIT_EXCEEDED"
        )
    
    return True, None
```

### 3) Endpoint Access Control
```python
def _check_endpoint_access(self, user, endpoint):
    """Check if user's plan includes access to this endpoint"""
    entitlement = self._get_user_entitlement(user)
    tier = entitlement.api_tier or 'basic'
    
    allowed_endpoints = API_TIERS[tier]['endpoints']
    
    if allowed_endpoints != '*' and endpoint not in allowed_endpoints:
        return False, self._error_response(
            f"Endpoint '{endpoint}' not included in your plan", 
            402, "PAYMENT_REQUIRED"
        )
    
    return True, None
```

## Sync with billing providers

- Webhooks: Implement a controller endpoint (e.g., `/billing/webhook`) that receives events from Stripe/Paddle/etc.
  - Verify signatures
  - Upsert entitlements and compute the desired group memberships
  - **Update API access levels and rate limits**
- Scheduled job (cron): Periodically validate entitlements (e.g., daily) to catch missed webhooks and handle grace expirations

Flow:
1) Event received (payment succeeded/failed, subscription canceled/renewed)
2) Update `license.license` state, `valid_until`, and `api_access_level`
3) Reconcile user memberships in groups for each affected company/user
4) **Update or revoke API keys if needed**

## Revocation and grace period

- On non-payment or cancellation:
  - Move entitlement to `grace` until `valid_until` + N days (configurable)
  - **Downgrade API access to read-only during grace period**
  - After grace, remove users from the paid groups to revoke access
  - **Disable API keys or return 402 Payment Required for API calls**
  - Optionally, switch the module to a "read-only" group to allow export/view without edits

- Do not uninstall the module; keep data intact. Consider a banner informing users why access is limited.

## UX considerations

- Show a dismissible banner in the backend when entitlement is in `grace` or `expired`
  - Inject via a small JS asset registered in `web.assets_backend`
  - Server-side: expose entitlement status via an endpoint or `ir.config_parameter`
- Replace key buttons with an upsell dialog if group missing (guard server-side too)
- **For API users: return helpful 402 Payment Required responses with upgrade URLs**

## Implementation steps (summary)

1) Create an addon `billing_entitlement`:
- Models: `license.license` with fields described above
- Data: define groups `group_paid_<key>` including `group_paid_api_access`
- Server logic: helpers to grant/revoke groups from users in a company
- Controllers: `/billing/webhook` for provider events
- **Controllers: Enhanced API authentication with entitlement checks**
- Cron: nightly job to refresh entitlements and enforce groups

2) Update paid modules to require groups:
- Add `groups` on menus, actions, views
- Adjust `ir.model.access.csv` to limit model access to the paid groups
- Add record rules if needed
- **Wrap base_api endpoints with entitlement checks**

3) Connect billing provider:
- Configure product → `module_key` mapping (including API access tiers)
- Implement signature verification and idempotency in webhook controller

4) Testing:
- Unit/integration tests for:
  - Granting/removing groups when entitlement toggles
  - Menus/actions visibility per group
  - Model access denied when group removed
  - **API endpoints returning 402 when access expired**
  - **Rate limiting enforcement**
  - Webhook event processing and cron reconciliation

## API Billing Integration Examples

### Webhook Handler for API Access
```python
@http.route('/billing/webhook/api-access', type='http', auth='none', methods=['POST'], csrf=False)
def handle_api_access_webhook(self, **kwargs):
    """Handle billing webhooks for API access changes"""
    try:
        # Verify webhook signature
        payload = request.httprequest.get_data()
        signature = request.httprequest.headers.get('Stripe-Signature')
        
        if not self._verify_webhook_signature(payload, signature):
            return self._error_response("Invalid signature", 401)
        
        event = json.loads(payload)
        
        if event['type'] == 'invoice.payment_succeeded':
            # Grant API access
            customer_id = event['data']['object']['customer']
            subscription = event['data']['object']['subscription']
            
            # Find the company/user
            company = self._find_company_by_customer_id(customer_id)
            
            # Update entitlement
            entitlement = self.env['license.license'].search([
                ('company_id', '=', company.id),
                ('module_key', '=', 'api_access')
            ], limit=1)
            
            if entitlement:
                entitlement.write({
                    'state': 'active',
                    'valid_until': self._calculate_expiry_date(subscription),
                    'api_access_level': self._determine_api_tier(subscription)
                })
            
            # Grant API access group to company users
            self._grant_api_access(company)
            
        elif event['type'] == 'invoice.payment_failed':
            # Move to grace period
            self._handle_payment_failure(event)
            
        return self._json_response({'status': 'processed'})
        
    except Exception as e:
        _logger.error(f"Webhook processing error: {e}")
        return self._error_response("Processing error", 500)
```

## Operational checklist

- Backups: ensure customer data is retained after access revocation
- Admin override: superuser or a special admin group to bypass restrictions for support
- Auditing: log entitlement changes and group membership changes
- **API monitoring: track API usage per customer for billing and support**
- Idempotency: webhook handlers must be safe to re-run
- **Rate limiting: implement proper rate limiting based on customer tiers**

## Example admin commands (shell)

Grant API access for a company's users:
```python
# python3 odoo-bin shell -c odoo.conf -d <db>
users = env['res.users'].search([('company_id', '=', company_id)])
api_group = env.ref('billing_entitlement.group_paid_api_access')
for u in users:
    api_group.users = [(4, u.id)]  # add

# Also update entitlement
entitlement = env['license.license'].search([
    ('company_id', '=', company_id),
    ('module_key', '=', 'api_access')
], limit=1)
entitlement.write({
    'state': 'active',
    'api_access_level': 'full_access'
})
```

Revoke API access:
```python
for u in users:
    api_group.users = [(3, u.id)]  # remove

# Update entitlement
entitlement.write({
    'state': 'expired',
    'api_access_level': 'none'
})
```

Check API usage:
```python
# Example: Check recent API requests for a company
env.cr.execute("""
    SELECT COUNT(*) as request_count 
    FROM api_request_log 
    WHERE company_id = %s 
    AND created_at > NOW() - INTERVAL '1 hour'
""", (company_id,))
```

## Legal and licensing note

Ensure your module licensing complies with Odoo and third-party licenses. Restricting access via groups and entitlements is a common approach; avoid removing user-owned data upon revocation. **For API access, clearly communicate usage limits and pricing in your terms of service.** Consult legal counsel for commercial licensing terms as needed.
