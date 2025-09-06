# -*- coding: utf-8 -*-

import json
import logging
import secrets
import string
from datetime import datetime, timedelta
from odoo import http, SUPERUSER_ID
from odoo.http import request
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class SimpleApiController(http.Controller):
    """Simple working API controller without decorators."""

    def _json_response(self, data=None, success=True, message=None, status_code=200):
        """Create a standardized JSON response."""
        response_data = {
            'success': success,
            'data': data,
            'message': message
        }
        
        response = request.make_response(
            json.dumps(response_data, default=str),
            headers=[('Content-Type', 'application/json')]
        )
        response.status_code = status_code
        return response

    def _error_response(self, message, status_code=400, error_code=None):
        """Create a standardized error response."""
        error_data = {
            'success': False,
            'error': {
                'message': message,
                'code': error_code
            }
        }
        
        response = request.make_response(
            json.dumps(error_data, default=str),
            headers=[('Content-Type', 'application/json')]
        )
        response.status_code = status_code
        return response

    def _authenticate(self):
        """Simple authentication check using API keys."""
        api_key = request.httprequest.headers.get('api-key')
        if not api_key:
            return False, self._error_response("Missing API key", 401, "MISSING_API_KEY")
        
        try:
            # Look for API key in res_users_apikeys table
            # The model might be named differently in Odoo
            try:
                # Try the standard Odoo model name
                api_key_record = request.env['res.users.apikeys'].sudo().search([
                    ('key', '=', api_key)
                ], limit=1)
            except:
                # Fallback - search directly in the database table
                request.env.cr.execute("""
                    SELECT user_id FROM res_users_apikeys 
                    WHERE key = %s AND (expiration_date IS NULL OR expiration_date > NOW())
                    LIMIT 1
                """, (api_key,))
                result = request.env.cr.fetchone()
                if result:
                    user = request.env['res.users'].sudo().browse(result[0])
                    if user and user.active:
                        request.env = request.env(user=user.id)
                        return True, user
                return False, self._error_response("Invalid API key", 403, "INVALID_API_KEY")
            
            if not api_key_record:
                return False, self._error_response("Invalid API key", 403, "INVALID_API_KEY")
            
            # Check if API key is expired
            if api_key_record.expiration_date and api_key_record.expiration_date < datetime.now():
                return False, self._error_response("API key expired", 403, "EXPIRED_API_KEY")
            
            user = api_key_record.user_id
            if not user or not user.active:
                return False, self._error_response("User account inactive", 403, "INACTIVE_USER")
            
            # Set user context (Odoo 18 compatible)
            request.env = request.env(user=user.id)
            return True, user
            
        except Exception as e:
            _logger.error("Authentication error: %s", str(e))
            return False, self._error_response("Authentication error", 500, "AUTH_ERROR")

    def _authenticate_session(self):
        """Authenticate user with session token."""
        session_token = request.httprequest.headers.get('session-token')
        if not session_token:
            return False, self._error_response("Session token required", 401, "MISSING_SESSION_TOKEN")
        
        try:
            # Look for active session
            session = request.env['api.session'].sudo().search([
                ('token', '=', session_token),
                ('active', '=', True),
                ('expires_at', '>', datetime.now())
            ], limit=1)
            
            if not session:
                return False, self._error_response("Invalid or expired session", 401, "INVALID_SESSION")
            
            # Update last activity
            session.sudo().write({'last_activity': datetime.now()})
            
            # Set user in environment for proper access control
            request.env = request.env(user=session.user_id.id)
            return True, session.user_id
        except Exception as e:
            _logger.error("Session authentication error: %s", str(e))
            return False, self._error_response("Session authentication failed", 500, "SESSION_AUTH_ERROR")

    def _check_model_access(self, model_name, operation='read'):
        """Check if current user has access to the model and operation."""
        try:
            model = request.env[model_name]
            if operation == 'read':
                model.check_access_rights('read')
            elif operation == 'create':
                model.check_access_rights('create')
            elif operation == 'write':
                model.check_access_rights('write')
            elif operation == 'unlink':
                model.check_access_rights('unlink')
            return True
        except AccessError:
            return False
        except Exception:
            return False

    # ===== WORKING ENDPOINTS =====

    @http.route('/api/v2/test', type='http', auth='none', methods=['GET'], csrf=False)
    def test_basic(self):
        """Basic API test (no authentication required)."""
        return self._json_response(
            data={'message': 'API v2 is working!', 'version': '2.0'},
            message="Basic test successful"
        )

    @http.route('/api/v2/auth/test', type='http', auth='none', methods=['GET'], csrf=False)
    def test_auth(self):
        """Test authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result
        
        user = result
        return self._json_response(
            data={
                'user_id': user.id,
                'user_name': user.name,
                'user_login': user.login,
                'authenticated': True
            },
            message="Authentication test successful"
        )

    @http.route('/api/v2/user/info', type='http', auth='none', methods=['GET'], csrf=False)
    def user_info(self):
        """Get authenticated user information."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result
        
        user = result
        return self._json_response(
            data={
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'login': user.login,
                    'email': user.email,
                    'active': user.active,
                    'company_id': [user.company_id.id, user.company_id.name] if user.company_id else False,
                },
                'api_version': '2.0',
                'database': request.env.cr.dbname
            },
            message="User information retrieved successfully"
        )

    @http.route('/api/v2/partners', type='http', auth='none', methods=['GET'], csrf=False)
    def list_partners(self):
        """List partners with authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result

        try:
            Partner = request.env['res.partner']
            
            # Get parameters from URL
            limit = int(request.httprequest.args.get('limit', 10))
            offset = int(request.httprequest.args.get('offset', 0))
            customers_only = request.httprequest.args.get('customers_only', 'true').lower() == 'true'
            
            # Build domain
            domain = [('active', '=', True)]
            if customers_only:
                domain.append(('customer_rank', '>', 0))
            
            # Search partners
            partners = Partner.search(domain, limit=limit, offset=offset, order='name')
            
            # Prepare data
            partners_data = []
            for partner in partners:
                partners_data.append({
                    'id': partner.id,
                    'name': partner.name,
                    'email': partner.email,
                    'phone': partner.phone,
                    'is_company': partner.is_company,
                    'customer_rank': partner.customer_rank,
                    'city': partner.city,
                    'country': partner.country_id.name if partner.country_id else False,
                })
            
            return self._json_response(
                data={
                    'partners': partners_data,
                    'count': len(partners_data),
                    'total_count': Partner.search_count(domain)
                },
                message="Partners retrieved successfully"
            )
            
        except Exception as e:
            _logger.error("Error listing partners: %s", str(e))
            return self._error_response("Error retrieving partners", 500, "PARTNERS_ERROR")

    @http.route('/api/v2/products', type='http', auth='none', methods=['GET'], csrf=False)
    def list_products(self):
        """List products with authentication."""
        is_valid, result = self._authenticate()
        if not is_valid:
            return result

        try:
            Product = request.env['product.template']
            
            # Get parameters
            limit = int(request.httprequest.args.get('limit', 10))
            offset = int(request.httprequest.args.get('offset', 0))
            sale_ok = request.httprequest.args.get('sale_ok', 'true').lower() == 'true'
            
            # Build domain
            domain = [('active', '=', True)]
            if sale_ok:
                domain.append(('sale_ok', '=', True))
            
            # Search products
            products = Product.search(domain, limit=limit, offset=offset, order='name')
            
            # Prepare data
            products_data = []
            for product in products:
                products_data.append({
                    'id': product.id,
                    'name': product.name,
                    'default_code': product.default_code,
                    'list_price': product.list_price,
                    'sale_ok': product.sale_ok,
                    'category': product.categ_id.name if product.categ_id else False,
                })
            
            return self._json_response(
                data={
                    'products': products_data,
                    'count': len(products_data),
                    'total_count': Product.search_count(domain)
                },
                message="Products retrieved successfully"
            )
            
        except Exception as e:
            _logger.error("Error listing products: %s", str(e))
            return self._error_response("Error retrieving products", 500, "PRODUCTS_ERROR")

    @http.route('/api/v2/search/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
    def search_model(self, model):
        """Search any model with authentication and field specification."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        try:
            # Validate model
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")
            
            # Check user access to model
            if not self._check_model_access(model, 'read'):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
            
            model_obj = request.env[model]
            
            
            # Get parameters
            limit = int(request.httprequest.args.get('limit', 10))
            offset = int(request.httprequest.args.get('offset', 0))
            fields_param = request.httprequest.args.get('fields', '')
            
            # Handle field specification
            if fields_param:
                requested_fields = [f.strip() for f in fields_param.split(',')]
                # Add 'id' if not present (always needed)
                if 'id' not in requested_fields:
                    requested_fields.insert(0, 'id')
                # Validate fields exist in model
                available_fields = [f for f in requested_fields if f in model_obj._fields]
                if not available_fields:
                    return self._error_response("No valid fields specified", 400, "INVALID_FIELDS")
            else:
                # Default basic fields
                basic_fields = ['id', 'name', 'display_name']
                available_fields = [f for f in basic_fields if f in model_obj._fields]
            
            # Basic domain
            domain = []
            
            # Handle additional filtering parameters from URL first
            has_custom_filters = False
            for param_key, param_value in request.httprequest.args.items():
                # Skip special parameters
                if param_key in ['limit', 'offset', 'fields']:
                    continue
                
                # Add domain filter for other parameters
                if param_key in model_obj._fields:
                    domain.append((param_key, '=', param_value))
                    has_custom_filters = True
            
            # Only add active filter if no custom filters and active field exists
            # This prevents filtering out inactive records when specifically searching
            if not has_custom_filters and 'active' in model_obj._fields:
                domain.append(('active', '=', True))
            
            # Search records
            records = model_obj.search(domain, limit=limit, offset=offset, order='id')
            
            # Read specified fields
            records_data = records.read(available_fields)
            
            return self._json_response(
                data={
                    'records': records_data,
                    'count': len(records_data),
                    'model': model,
                    'fields': available_fields,
                    'total_count': model_obj.search_count(domain)
                },
                message=f"Found {len(records_data)} records in {model}"
            )
            
        except Exception as e:
            _logger.error("Error searching %s: %s", model, str(e))
            return self._error_response("Error searching records", 500, "SEARCH_ERROR")

    @http.route('/api/v2/fields/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_model_fields(self, model):
        """Get all fields for a specific model."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        try:
            # Validate model exists
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")
            
            # Get model fields from ir.model.fields
            fields_obj = request.env['ir.model.fields']
            model_fields = fields_obj.search([('model', '=', model)], order='name')
            
            # Prepare field data
            fields_data = []
            for field in model_fields:
                fields_data.append({
                    'name': field.name,
                    'description': field.field_description,
                    'type': field.ttype,
                    'required': field.required,
                    'readonly': field.readonly,
                    'help': field.help or '',
                    'relation': field.relation or '',
                    'store': field.store
                })
            
            return self._json_response(
                data={
                    'model': model,
                    'fields': fields_data,
                    'count': len(fields_data)
                },
                message=f"Found {len(fields_data)} fields for model {model}"
            )
            
        except Exception as e:
            _logger.error("Error getting fields for %s: %s", model, str(e))
            return self._error_response("Error retrieving model fields", 500, "FIELDS_ERROR")

    @http.route('/api/v2/auth/login', type='http', auth='none', methods=['POST'], csrf=False)
    def user_login(self):
        """Authenticate user with username/password and create session."""
        try:
            # Parse JSON data from HTTP request
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")
                
            username = data.get('username')
            password = data.get('password')
            
            if not username or not password:
                return self._error_response("Username and password required", 400, "MISSING_CREDENTIALS")
            
            # Authenticate user using session.authenticate
            try:
                # The database name should be properly set from the Odoo startup
                db_name = request.env.cr.dbname
                
                # Validate database name
                if not db_name:
                    return self._error_response("Database not configured", 500, "DB_NOT_CONFIGURED")
                
                # Prepare credential dictionary as expected by Odoo's authentication
                credential = {
                    'login': username,
                    'password': password, 
                    'type': 'password'
                }
                
                # Use Odoo's native session authentication with proper credential format
                auth_info = request.session.authenticate(db_name, credential)
                
                # Check if authentication was successful
                if not auth_info or not auth_info.get('uid'):
                    return self._error_response("Invalid credentials", 401, "INVALID_CREDENTIALS")
                
                uid = auth_info['uid']
                
                # Get the user record (need to use a fresh environment after authentication)
                # Create a new environment with the authenticated user
                from odoo import api
                with request.env.registry.cursor() as new_cr:
                    new_env = api.Environment(new_cr, uid, {})
                    user = new_env['res.users'].browse(uid)
                    if not user.exists() or not user.active:
                        return self._error_response("User account inactive", 403, "INACTIVE_USER")
                
                    
                    # Generate session token
                    session_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
                    expires_at = datetime.now() + timedelta(hours=24)  # 24 hour sessions
                    
                    # Create session record using the authenticated environment
                    session = new_env['api.session'].sudo().create({
                        'user_id': user.id,
                        'token': session_token,
                        'expires_at': expires_at,
                        'created_at': datetime.now(),
                        'last_activity': datetime.now(),
                        'active': True
                    })
                    
                    return self._json_response(
                        data={
                            'session_token': session_token,
                            'expires_at': expires_at.isoformat(),
                            'user': {
                                'id': user.id,
                                'name': user.name,
                                'login': user.login,
                                'email': user.email,
                                'groups': [group.name for group in user.groups_id]
                            }
                        },
                        message="Login successful"
                    )
                    
            except Exception as e:
                _logger.error("Authentication error for user %s: %s", username, str(e))
                return self._error_response("Authentication failed", 401, "AUTH_FAILED")
            
        except Exception as e:
            _logger.error("Login error: %s", str(e))
            return self._error_response("Login failed", 500, "LOGIN_ERROR")

    @http.route('/api/v2/auth/refresh', type='http', auth='none', methods=['POST'], csrf=False)
    def refresh_session(self):
        """Refresh session token to extend expiration."""
        session_token = request.httprequest.headers.get('session-token')
        if not session_token:
            return self._error_response("Session token required", 401, "MISSING_SESSION_TOKEN")
        
        try:
            # Look for session (even if expired within grace period)
            from datetime import datetime, timedelta
            grace_period = datetime.now() - timedelta(hours=1)  # Allow refresh within 1 hour of expiry
            
            session = request.env['api.session'].sudo().search([
                ('token', '=', session_token),
                ('active', '=', True),
                ('expires_at', '>', grace_period)  # Still within grace period
            ], limit=1)
            
            if not session:
                return self._error_response("Session not found or expired beyond refresh period", 401, "SESSION_NOT_REFRESHABLE")
            
            # Generate new session token
            new_session_token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
            new_expires_at = datetime.now() + timedelta(hours=24)  # 24 hour sessions
            
            # Update session with new token and expiration
            session.sudo().write({
                'token': new_session_token,
                'expires_at': new_expires_at,
                'last_activity': datetime.now()
            })
            
            # Get user info
            user = session.user_id
            
            return self._json_response(
                data={
                    'session_token': new_session_token,
                    'expires_at': new_expires_at.isoformat(),
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email
                    }
                },
                message="Session refreshed successfully"
            )
            
        except Exception as e:
            _logger.error("Session refresh error: %s", str(e))
            return self._error_response("Session refresh failed", 500, "REFRESH_ERROR")

    @http.route('/api/v2/auth/logout', type='http', auth='none', methods=['POST'], csrf=False)
    def user_logout(self):
        """Logout user and invalidate session."""
        is_valid, result = self._authenticate_session()
        if not is_valid:
            return result
        
        try:
            session_token = request.httprequest.headers.get('session-token')
            session = request.env['api.session'].sudo().search([('token', '=', session_token)], limit=1)
            if session:
                session.sudo().write({'active': False})
            
            return self._json_response(message="Logout successful")
            
        except Exception as e:
            _logger.error("Logout error: %s", str(e))
            return self._error_response("Logout failed", 500, "LOGOUT_ERROR")

    @http.route('/api/v2/auth/me', type='http', auth='none', methods=['GET'], csrf=False)
    def current_user(self):
        """Get current authenticated user info."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user
        
        try:
            return self._json_response(
                data={
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'login': user.login,
                        'email': user.email,
                        'active': user.active,
                        'company_id': [user.company_id.id, user.company_id.name] if user.company_id else False,
                        'groups': [{'id': g.id, 'name': g.name} for g in user.groups_id],
                        'permissions': {
                            'is_admin': user.has_group('base.group_system'),
                            'is_user': user.has_group('base.group_user'),
                            'can_manage_users': user.has_group('base.group_user_admin')
                        }
                    }
                },
                message="User information retrieved"
            )
        except Exception as e:
            _logger.error("Error getting user info: %s", str(e))
            return self._error_response("Error retrieving user info", 500, "USER_INFO_ERROR")

    @http.route('/api/v2/groups', type='http', auth='none', methods=['GET'], csrf=False)
    def get_available_groups(self):
        """Get all available user groups for assignment."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        try:
            # Check if user can manage users
            if not user.has_group('base.group_user_admin') and not user.has_group('base.group_system'):
                return self._error_response("Access denied: User management required", 403, "ACCESS_DENIED")

            # Get all groups excluding system and technical ones
            groups = request.env['res.groups'].sudo().search([
                ('share', '=', False),  # Exclude share groups
                ('category_id.xml_id', '!=', 'base.module_category_hidden'),  # Exclude hidden groups
            ], order='category_id desc, name')

            groups_by_category = {}
            for group in groups:
                category_name = group.category_id.name if group.category_id else 'Other'
                if category_name not in groups_by_category:
                    groups_by_category[category_name] = []
                
                groups_by_category[category_name].append({
                    'id': group.id,
                    'name': group.name,
                    'full_name': group.full_name,
                    'xml_id': group.get_external_id().get(group.id, ''),
                    'comment': group.comment or '',
                    'users_count': len(group.users)
                })

            return self._json_response(
                data={
                    'groups_by_category': groups_by_category,
                    'total_groups': len(groups)
                },
                message="Available groups retrieved"
            )

        except Exception as e:
            _logger.error("Error getting groups: %s", str(e))
            return self._error_response("Error retrieving groups", 500, "GROUPS_ERROR")

    @http.route('/api/v2/create/<string:model>', type='http', auth='none', methods=['POST'], csrf=False)
    def create_record(self, model):
        """Create a record with authentication."""
        # Try session authentication first, then API key
        is_valid, user = self._authenticate_session()
        if not is_valid:
            is_valid, user = self._authenticate()
            if not is_valid:
                return user

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            # Validate model
            if model not in request.env:
                return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")
            
            # Check user access to model
            if not self._check_model_access(model, 'create'):
                return self._error_response(f"Access denied for model '{model}'", 403, "ACCESS_DENIED")
            
            model_obj = request.env[model]
            
            # Special handling for user creation with groups
            if model == 'res.users':
                return self._create_user_with_groups(data)
            
            # Create record
            new_record = model_obj.create(data)
            
            return self._json_response(
                data={
                    'id': new_record.id,
                    'record': new_record.read()[0]
                },
                message=f"Record created in {model}",
                status_code=201
            )
            
        except Exception as e:
            _logger.error("Error creating %s: %s", model, str(e))
            return self._error_response("Error creating record", 500, "CREATE_ERROR")

    def _create_user_with_groups(self, data):
        """Special method to create users with proper group handling."""
        try:
            # Extract groups from data
            group_names = data.pop('group_names', [])
            group_ids = data.pop('group_ids', [])
            auto_generate_credentials = data.pop('auto_generate_credentials', True)
            
            # Generate temporary password if not provided
            if 'password' not in data and auto_generate_credentials:
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
                data['password'] = temp_password
            else:
                temp_password = data.get('password', None)
            
            # Create user first
            user = request.env['res.users'].create(data)
            
            # Handle group assignments
            if group_names:
                # Convert group names to IDs
                groups = request.env['res.groups'].sudo().search([('name', 'in', group_names)])
                if groups:
                    user.groups_id = [(6, 0, groups.ids)]
            
            elif group_ids:
                # Use provided group IDs directly  
                user.groups_id = [(6, 0, group_ids)]
            
            else:
                # Default to basic user group
                default_group = request.env.ref('base.group_user', raise_if_not_found=False)
                if default_group:
                    user.groups_id = [(6, 0, [default_group.id])]
            
            # Generate API key if requested
            api_key = None
            if auto_generate_credentials:
                try:
                    # Generate API key for the user
                    api_key = ''.join(secrets.choice(string.ascii_letters + string.digits + '-_') for _ in range(48))
                    
                    # Create API key record in database
                    request.env.cr.execute("""
                        INSERT INTO res_users_apikeys (name, user_id, key, create_date)
                        VALUES (%s, %s, %s, NOW())
                    """, ('Auto-generated API Key', user.id, api_key))
                    request.env.cr.commit()
                    
                except Exception as e:
                    _logger.warning("Could not generate API key for user %s: %s", user.login, str(e))
                    # If API key generation fails, we'll still return the user without it
                    api_key = None
            
            # Prepare response data
            response_data = {
                'id': user.id,
                'name': user.name,
                'login': user.login,
                'email': user.email,
                'groups': [{'id': g.id, 'name': g.name} for g in user.groups_id],
                'active': user.active,
                'create_date': user.create_date.isoformat() if user.create_date else None
            }
            
            # Add credentials if auto-generated
            if auto_generate_credentials:
                credentials = {}
                if temp_password:
                    credentials['temporary_password'] = temp_password
                if api_key:
                    credentials['api_key'] = api_key
                
                if credentials:
                    response_data['credentials'] = credentials
                    response_data['credentials']['note'] = "Store these credentials securely - they won't be shown again"
            
            return self._json_response(
                data=response_data,
                message="User created successfully with credentials" if auto_generate_credentials else "User created successfully",
                status_code=201
            )
            
        except Exception as e:
            _logger.error("Error creating user: %s", str(e))
            return self._error_response("Error creating user", 500, "USER_CREATE_ERROR")

    @http.route('/api/v2/users/<int:user_id>/password', type='http', auth='none', methods=['PUT'], csrf=False)
    def change_user_password(self, user_id):
        """Change user password (admin or own password)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            new_password = data.get('new_password')
            old_password = data.get('old_password')  # Required for own password change
            
            if not new_password:
                return self._error_response("new_password is required", 400, "MISSING_PASSWORD")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Check permissions
            is_own_password = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            
            if not is_own_password and not is_admin:
                return self._error_response("Access denied: Can only change own password or need admin rights", 403, "ACCESS_DENIED")

            # For own password change, verify old password
            if is_own_password and not is_admin:
                if not old_password:
                    return self._error_response("old_password is required when changing own password", 400, "MISSING_OLD_PASSWORD")
                
                # Verify old password
                try:
                    request.session.authenticate(request.session.db, current_user.login, old_password)
                except Exception:
                    return self._error_response("Invalid old password", 401, "INVALID_OLD_PASSWORD")

            # Change password
            target_user.sudo().password = new_password
            
            return self._json_response(
                data={
                    'user_id': user_id,
                    'message': 'Password changed successfully'
                },
                message="Password updated successfully"
            )

        except Exception as e:
            _logger.error("Error changing password for user %s: %s", user_id, str(e))
            return self._error_response("Error changing password", 500, "PASSWORD_CHANGE_ERROR")

    @http.route('/api/v2/users/<int:user_id>', type='http', auth='none', methods=['PUT'], csrf=False)
    def update_user(self, user_id):
        """Update user information."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Parse JSON data
            content_type = request.httprequest.headers.get('Content-Type', '')
            if 'application/json' not in content_type:
                return self._error_response("Content-Type must be application/json", 400, "INVALID_CONTENT_TYPE")
            
            try:
                data = request.httprequest.get_json(force=True)
                if not data:
                    return self._error_response("No data provided", 400, "NO_DATA")
            except Exception:
                return self._error_response("Invalid JSON", 400, "INVALID_JSON")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Check permissions
            is_own_profile = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            
            if not is_own_profile and not is_admin:
                return self._error_response("Access denied: Can only update own profile or need admin rights", 403, "ACCESS_DENIED")

            # Fields that users can update about themselves
            user_editable_fields = ['name', 'email', 'phone', 'mobile', 'signature', 'lang', 'tz']
            
            # Fields that only admins can update
            admin_only_fields = ['login', 'active', 'groups_id', 'company_id', 'company_ids']

            # Filter data based on permissions
            update_data = {}
            
            for field, value in data.items():
                if field == 'password':
                    continue  # Use password change endpoint instead
                elif field in user_editable_fields:
                    update_data[field] = value
                elif field in admin_only_fields:
                    if is_admin:
                        update_data[field] = value
                    else:
                        return self._error_response(f"Access denied: Field '{field}' requires admin rights", 403, "ADMIN_FIELD_ACCESS_DENIED")
                elif field in ['group_names', 'group_ids']:
                    # Handle group updates for admins
                    if is_admin:
                        if field == 'group_names':
                            groups = request.env['res.groups'].sudo().search([('name', 'in', value)])
                            if groups:
                                update_data['groups_id'] = [(6, 0, groups.ids)]
                        elif field == 'group_ids':
                            update_data['groups_id'] = [(6, 0, value)]
                    else:
                        return self._error_response(f"Access denied: Field '{field}' requires admin rights", 403, "ADMIN_FIELD_ACCESS_DENIED")

            if not update_data:
                return self._error_response("No valid fields to update", 400, "NO_VALID_FIELDS")

            # Update user
            target_user.sudo().write(update_data)
            
            # Get updated user data
            updated_user = target_user.read(['id', 'name', 'login', 'email', 'phone', 'mobile', 'active', 'lang', 'tz'])[0]
            if is_admin:
                updated_user['groups'] = [{'id': g.id, 'name': g.name} for g in target_user.groups_id]

            return self._json_response(
                data={
                    'user': updated_user,
                    'updated_fields': list(update_data.keys())
                },
                message="User updated successfully"
            )

        except Exception as e:
            _logger.error("Error updating user %s: %s", user_id, str(e))
            return self._error_response("Error updating user", 500, "USER_UPDATE_ERROR")

    @http.route('/api/v2/users/<int:user_id>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_user(self, user_id):
        """Get user information."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Check permissions
            is_own_profile = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            can_view_users = current_user.has_group('base.group_user')
            
            if not is_own_profile and not is_admin and not can_view_users:
                return self._error_response("Access denied", 403, "ACCESS_DENIED")

            # Basic fields everyone can see
            user_data = {
                'id': target_user.id,
                'name': target_user.name,
                'email': target_user.email,
                'active': target_user.active,
                'create_date': target_user.create_date.isoformat() if target_user.create_date else None,
            }

            # Additional fields for own profile or admins
            if is_own_profile or is_admin:
                user_data.update({
                    'login': target_user.login,
                    'phone': target_user.phone,
                    'mobile': target_user.mobile,
                    'lang': target_user.lang,
                    'tz': target_user.tz,
                    'signature': target_user.signature,
                    'company_id': [target_user.company_id.id, target_user.company_id.name] if target_user.company_id else None,
                })

            # Admin-only fields
            if is_admin:
                user_data.update({
                    'groups': [{'id': g.id, 'name': g.name, 'full_name': g.full_name} for g in target_user.groups_id],
                    'company_ids': [{'id': c.id, 'name': c.name} for c in target_user.company_ids],
                    'login_date': target_user.login_date.isoformat() if target_user.login_date else None,
                })

            return self._json_response(
                data={'user': user_data},
                message="User information retrieved"
            )

        except Exception as e:
            _logger.error("Error getting user %s: %s", user_id, str(e))
            return self._error_response("Error retrieving user", 500, "USER_GET_ERROR")

    @http.route('/api/v2/users/<int:user_id>/reset-password', type='http', auth='none', methods=['POST'], csrf=False)
    def reset_user_password(self, user_id):
        """Reset user password (admin only) - generates a temporary password."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Check admin permissions
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            if not is_admin:
                return self._error_response("Access denied: Admin rights required", 403, "ACCESS_DENIED")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Generate temporary password
            temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
            
            # Reset password
            target_user.sudo().password = temp_password
            
            return self._json_response(
                data={
                    'user_id': user_id,
                    'temporary_password': temp_password,
                    'message': 'Password has been reset. User should change it on first login.'
                },
                message="Password reset successfully"
            )

        except Exception as e:
            _logger.error("Error resetting password for user %s: %s", user_id, str(e))
            return self._error_response("Error resetting password", 500, "PASSWORD_RESET_ERROR")

    @http.route('/api/v2/users', type='http', auth='none', methods=['GET'], csrf=False)
    def list_users(self):
        """List all users (with pagination)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Check permissions
            can_view_users = current_user.has_group('base.group_user')
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            
            if not can_view_users:
                return self._error_response("Access denied", 403, "ACCESS_DENIED")

            # Get parameters
            limit = int(request.httprequest.args.get('limit', 10))
            offset = int(request.httprequest.args.get('offset', 0))
            search = request.httprequest.args.get('search', '')
            active_only = request.httprequest.args.get('active_only', 'true').lower() == 'true'

            # Build domain
            domain = []
            if active_only:
                domain.append(('active', '=', True))
            if search:
                domain.extend(['|', '|', 
                    ('name', 'ilike', search),
                    ('login', 'ilike', search),
                    ('email', 'ilike', search)
                ])

            # Get users
            users = request.env['res.users'].sudo().search(domain, limit=limit, offset=offset, order='name')
            total_count = request.env['res.users'].sudo().search_count(domain)

            # Prepare user data
            users_data = []
            for user in users:
                user_data = {
                    'id': user.id,
                    'name': user.name,
                    'login': user.login,
                    'email': user.email,
                    'active': user.active,
                    'create_date': user.create_date.isoformat() if user.create_date else None,
                }
                
                # Add more fields for admins
                if is_admin:
                    user_data.update({
                        'groups': [g.name for g in user.groups_id],
                        'company_id': user.company_id.name if user.company_id else None,
                        'login_date': user.login_date.isoformat() if user.login_date else None,
                    })
                
                users_data.append(user_data)

            return self._json_response(
                data={
                    'users': users_data,
                    'count': len(users_data),
                    'total_count': total_count,
                    'limit': limit,
                    'offset': offset
                },
                message=f"Found {len(users_data)} users"
            )

        except Exception as e:
            _logger.error("Error listing users: %s", str(e))
            return self._error_response("Error retrieving users", 500, "USERS_LIST_ERROR")

    @http.route('/api/v2/users/<int:user_id>/api-key', type='http', auth='none', methods=['POST'], csrf=False)
    def generate_user_api_key(self, user_id):
        """Generate API key for a user (admin only or own API key)."""
        # Try session authentication first, then API key
        is_valid, current_user = self._authenticate_session()
        if not is_valid:
            is_valid, current_user = self._authenticate()
            if not is_valid:
                return current_user

        try:
            # Check permissions
            is_own_user = current_user.id == user_id
            is_admin = current_user.has_group('base.group_system') or current_user.has_group('base.group_user_admin')
            
            if not is_own_user and not is_admin:
                return self._error_response("Access denied: Can only generate own API key or need admin rights", 403, "ACCESS_DENIED")

            # Get target user
            target_user = request.env['res.users'].sudo().browse(user_id)
            if not target_user.exists():
                return self._error_response("User not found", 404, "USER_NOT_FOUND")

            # Generate API key
            try:
                # Generate a new API key
                api_key = ''.join(secrets.choice(string.ascii_letters + string.digits + '-_') for _ in range(48))
                
                # Create API key record in database
                request.env.cr.execute("""
                    INSERT INTO res_users_apikeys (name, user_id, key, create_date)
                    VALUES (%s, %s, %s, NOW())
                """, ('Generated API Key', user_id, api_key))
                request.env.cr.commit()
                
                return self._json_response(
                    data={
                        'user_id': user_id,
                        'user_name': target_user.name,
                        'api_key': api_key,
                        'note': 'Store this API key securely - it will not be shown again'
                    },
                    message="API key generated successfully"
                )
                
            except Exception as e:
                _logger.error("Error generating API key for user %s: %s", user_id, str(e))
                return self._error_response("Could not generate API key", 500, "API_KEY_GENERATION_ERROR")

        except Exception as e:
            _logger.error("Error in API key generation for user %s: %s", user_id, str(e))
            return self._error_response("Error generating API key", 500, "API_KEY_ERROR")
