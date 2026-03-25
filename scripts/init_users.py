"""
Initialize role-based users for odoo19_db.
Idempotent: creates missing users, updates groups for existing ones,
and ensures each user has a valid API key.

Run inside Docker:
    docker compose exec odoo python3 /opt/odoo/odoo-bin shell \
        --addons-path=/opt/odoo/addons -d odoo19_db --no-http \
        < /opt/odoo/scripts/init_users.py

Or locally:
    python3 odoo-bin shell --addons-path=addons -d odoo19_db --no-http \
        < scripts/init_users.py
"""

USERS = [
    # ── Admin / Management ──
    {
        'name': 'Manager User',
        'login': 'manager@test.com',
        'email': 'manager@test.com',
        'password': 'manager123',
        'groups': [
            'base.group_user',
            'base.group_erp_manager',
        ],
    },
    {
        'name': 'Regular User',
        'login': 'user@test.com',
        'email': 'user@test.com',
        'password': 'user123',
        'groups': [
            'base.group_user',
        ],
    },

    # ── Sales ──
    {
        'name': 'Sales User',
        'login': 'sales@test.com',
        'email': 'sales@test.com',
        'password': 'sales123',
        'groups': [
            'base.group_user',
            'sales_team.group_sale_salesman',
        ],
    },
    {
        'name': 'Sales Manager',
        'login': 'sales_manager@test.com',
        'email': 'sales_manager@test.com',
        'password': 'salesmgr123',
        'groups': [
            'base.group_user',
            'sales_team.group_sale_manager',
            'sales_team.group_sale_salesman_all_leads',
            'sales_team.group_sale_salesman',
        ],
    },

    # ── CRM ──
    {
        'name': 'CRM User',
        'login': 'crm@test.com',
        'email': 'crm@test.com',
        'password': 'crm12345',
        'groups': [
            'base.group_user',
            'sales_team.group_sale_salesman',
        ],
    },

    # ── Accounting ──
    {
        'name': 'Accounting User',
        'login': 'accounting@test.com',
        'email': 'accounting@test.com',
        'password': 'account123',
        'groups': [
            'base.group_user',
            'account.group_account_invoice',
        ],
    },
    {
        'name': 'Accounting Manager',
        'login': 'accounting_mgr@test.com',
        'email': 'accounting_mgr@test.com',
        'password': 'accountmgr123',
        'groups': [
            'base.group_user',
            'account.group_account_manager',
            'account.group_account_invoice',
        ],
    },

    # ── HR ──
    {
        'name': 'HR User',
        'login': 'hr@test.com',
        'email': 'hr@test.com',
        'password': 'hr123456',
        'groups': [
            'base.group_user',
            'hr.group_hr_user',
        ],
    },
    {
        'name': 'HR Manager',
        'login': 'hr_manager@test.com',
        'email': 'hr_manager@test.com',
        'password': 'hrmgr12345',
        'groups': [
            'base.group_user',
            'hr.group_hr_manager',
            'hr.group_hr_user',
        ],
    },

    # ── Inventory ──
    {
        'name': 'Inventory User',
        'login': 'inventory@test.com',
        'email': 'inventory@test.com',
        'password': 'inventory123',
        'groups': [
            'base.group_user',
            'stock.group_stock_user',
        ],
    },
    {
        'name': 'Inventory Manager',
        'login': 'inventory_mgr@test.com',
        'email': 'inventory_mgr@test.com',
        'password': 'invmgr12345',
        'groups': [
            'base.group_user',
            'stock.group_stock_manager',
            'stock.group_stock_user',
        ],
    },

    # ── Project ──
    {
        'name': 'Project User',
        'login': 'project@test.com',
        'email': 'project@test.com',
        'password': 'project123',
        'groups': [
            'base.group_user',
            'project.group_project_user',
        ],
    },
    {
        'name': 'Project Manager',
        'login': 'project_mgr@test.com',
        'email': 'project_mgr@test.com',
        'password': 'projectmgr123',
        'groups': [
            'base.group_user',
            'project.group_project_manager',
            'project.group_project_user',
        ],
    },

    # ── Purchase ──
    {
        'name': 'Purchase User',
        'login': 'purchase@test.com',
        'email': 'purchase@test.com',
        'password': 'purchase123',
        'groups': [
            'base.group_user',
            'purchase.group_purchase_user',
        ],
    },
    {
        'name': 'Purchase Manager',
        'login': 'purchase_mgr@test.com',
        'email': 'purchase_mgr@test.com',
        'password': 'purchasemgr123',
        'groups': [
            'base.group_user',
            'purchase.group_purchase_manager',
            'purchase.group_purchase_user',
        ],
    },
]


def sync_users(env):
    Users = env['res.users'].sudo()
    ApiKeys = env['res.users.apikeys']
    results = []

    for user_data in USERS:
        login = user_data['login']

        group_ids = []
        missing_groups = []
        for xml_id in user_data['groups']:
            group = env.ref(xml_id, raise_if_not_found=False)
            if group:
                group_ids.append(group.id)
            else:
                missing_groups.append(xml_id)
        if missing_groups:
            print(f"[WARN] Groups not found for '{login}': {missing_groups}")

        existing = Users.search([('login', '=', login)], limit=1)
        if existing:
            existing.write({
                'name': user_data['name'],
                'email': user_data['email'],
                'group_ids': [(6, 0, group_ids)],
            })
            existing.write({'password': user_data['password']})

            user_keys = ApiKeys.sudo().search([('user_id', '=', existing.id)])
            if not user_keys:
                api_key = env(user=existing.id)['res.users.apikeys'].sudo()._generate(
                    scope=None,
                    name=f'Auto-generated key ({login})',
                    expiration_date=None,
                )
                print(f"[UPD]  Updated '{login}' (id={existing.id}) + new API key: {api_key}")
                results.append({'login': login, 'id': existing.id, 'api_key': api_key, 'status': 'updated+key'})
            else:
                print(f"[UPD]  Updated '{login}' (id={existing.id}), API key already exists")
                results.append({'login': login, 'id': existing.id, 'status': 'updated'})
            continue

        user = Users.create({
            'name': user_data['name'],
            'login': login,
            'email': user_data['email'],
            'password': user_data['password'],
            'group_ids': [(6, 0, group_ids)],
        })

        api_key = env(user=user.id)['res.users.apikeys'].sudo()._generate(
            scope=None,
            name=f'Auto-generated key ({login})',
            expiration_date=None,
        )
        print(f"[NEW]  Created '{login}' (id={user.id}), API key: {api_key}")
        results.append({'login': login, 'id': user.id, 'api_key': api_key, 'status': 'created'})

    # Ensure admin has an API key
    admin = Users.browse(2)
    admin_keys = ApiKeys.sudo().search([('user_id', '=', admin.id)])
    if not admin_keys:
        admin_key = env(user=admin.id)['res.users.apikeys'].sudo()._generate(
            scope=None,
            name='Admin API key',
            expiration_date=None,
        )
        print(f"[OK]   Generated admin API key: {admin_key}")
    else:
        print(f"[SKIP] Admin already has {len(admin_keys)} API key(s)")

    return results


results = sync_users(env)
env.cr.commit()
print("\n═══ User sync complete ═══")
for r in results:
    key_info = f", api_key={r['api_key']}" if 'api_key' in r else ''
    print(f"  {r['login']}: {r['status']} (id={r['id']}{key_info})")
