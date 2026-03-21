"""
Initialize role-based users for odoo19_db.

Run with:
    python3 odoo-bin shell --addons-path=addons -d odoo19_db < scripts/init_users.py
"""
from datetime import datetime, timedelta

USERS = [
    {
        'name': 'Manager User',
        'login': 'manager@test.com',
        'email': 'manager@test.com',
        'password': 'manager123',
        'groups': ['base.group_user', 'base.group_user_admin'],
    },
    {
        'name': 'Regular User',
        'login': 'user@test.com',
        'email': 'user@test.com',
        'password': 'user123',
        'groups': ['base.group_user'],
    },
    {
        'name': 'Sales User',
        'login': 'sales@test.com',
        'email': 'sales@test.com',
        'password': 'sales123',
        'groups': ['base.group_user', 'sales_team.group_sale_salesman'],
    },
]


def create_users(env):
    Users = env['res.users']
    ApiKeys = env['res.users.apikeys']
    results = []

    for user_data in USERS:
        login = user_data['login']
        existing = Users.search([('login', '=', login)], limit=1)
        if existing:
            print(f"[SKIP] User '{login}' already exists (id={existing.id})")
            results.append({'login': login, 'id': existing.id, 'status': 'exists'})
            continue

        group_ids = []
        for xml_id in user_data['groups']:
            group = env.ref(xml_id, raise_if_not_found=False)
            if group:
                group_ids.append(group.id)

        user = Users.create({
            'name': user_data['name'],
            'login': login,
            'email': user_data['email'],
            'password': user_data['password'],
            'group_ids': [(6, 0, group_ids)],
        })

        # Generate an API key for each user
        api_key = env(user=user.id)['res.users.apikeys'].sudo()._generate(
            scope=None,
            name='Init script API key',
            expiration_date=None,
        )

        print(f"[OK]   Created '{login}' (id={user.id})")
        print(f"       API key: {api_key}")
        results.append({
            'login': login,
            'id': user.id,
            'api_key': api_key,
            'status': 'created',
        })

    # Also generate an API key for admin if none exists
    admin = Users.browse(2)  # admin user
    admin_keys = ApiKeys.sudo().search([('user_id', '=', admin.id)])
    if not admin_keys:
        admin_key = env(user=admin.id)['res.users.apikeys'].sudo()._generate(
            scope=None,
            name='Admin API key',
            expiration_date=None,
        )
        print(f"[OK]   Generated API key for admin (id={admin.id})")
        print(f"       API key: {admin_key}")
    else:
        print(f"[SKIP] Admin already has {len(admin_keys)} API key(s)")

    return results


results = create_users(env)
env.cr.commit()
print("\n--- User initialization complete ---")
for r in results:
    print(f"  {r['login']}: {r['status']} (id={r['id']})")
