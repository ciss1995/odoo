"""
Seed demo data across all major modules.
Idempotent: skips records that already exist (matched by name/reference).

Run inside Docker:
    docker compose exec odoo python3 /opt/odoo/odoo-bin shell \
        --addons-path=/opt/odoo/addons -d odoo19_db --no-http \
        < /opt/odoo/scripts/seed_demo_data.py

Or locally:
    python3 odoo-bin shell --addons-path=addons -d odoo19_db --no-http \
        < scripts/seed_demo_data.py
"""
from datetime import date, timedelta

Partner = env['res.partner'].sudo()
Product = env['product.template'].sudo()
SaleOrder = env['sale.order'].sudo()
SaleOrderLine = env['sale.order.line'].sudo()
CrmLead = env['crm.lead'].sudo()
Employee = env['hr.employee'].sudo()
Department = env['hr.department'].sudo()
Users = env['res.users'].sudo()

has_purchase = 'purchase.order' in env
has_stock = 'stock.picking' in env
has_account = 'account.move' in env

if has_purchase:
    PurchaseOrder = env['purchase.order'].sudo()
    PurchaseOrderLine = env['purchase.order.line'].sudo()

stats = {'created': 0, 'skipped': 0}


def _find_or_create(model, domain, vals, label=None):
    existing = model.search(domain, limit=1)
    if existing:
        stats['skipped'] += 1
        return existing
    rec = model.create(vals)
    stats['created'] += 1
    name = label or vals.get('name', f'id={rec.id}')
    print(f"  [NEW] {model._name}: {name}")
    return rec


# ═══════════════════════════════════════════
# 1. CONTACTS / PARTNERS
# ═══════════════════════════════════════════
print("\n━━━ Seeding Partners ━━━")

companies = [
    {'name': 'Acme Corporation', 'is_company': True, 'email': 'info@acme.com',
     'phone': '+1-555-0100', 'city': 'San Francisco', 'country_id': env.ref('base.us').id,
     'street': '123 Market St', 'zip': '94105', 'customer_rank': 1},
    {'name': 'Globex Industries', 'is_company': True, 'email': 'contact@globex.com',
     'phone': '+1-555-0200', 'city': 'New York', 'country_id': env.ref('base.us').id,
     'street': '456 Broadway', 'zip': '10012', 'customer_rank': 1},
    {'name': 'Umbrella Corp', 'is_company': True, 'email': 'sales@umbrella.io',
     'phone': '+44-20-7946-0958', 'city': 'London', 'country_id': env.ref('base.uk').id,
     'street': '10 Downing St', 'zip': 'SW1A 2AA', 'customer_rank': 1},
    {'name': 'Stark Enterprises', 'is_company': True, 'email': 'info@stark.com',
     'phone': '+1-555-0300', 'city': 'Los Angeles', 'country_id': env.ref('base.us').id,
     'street': '789 Sunset Blvd', 'zip': '90028', 'customer_rank': 1},
    {'name': 'Wayne Industries', 'is_company': True, 'email': 'hello@wayne.com',
     'phone': '+1-555-0400', 'city': 'Chicago', 'country_id': env.ref('base.us').id,
     'street': '321 Michigan Ave', 'zip': '60601', 'customer_rank': 1},
    {'name': 'TechSupply Ltd', 'is_company': True, 'email': 'orders@techsupply.com',
     'phone': '+33-1-42-68-5300', 'city': 'Paris', 'country_id': env.ref('base.fr').id,
     'street': '55 Rue de Rivoli', 'zip': '75001', 'supplier_rank': 1},
    {'name': 'Global Parts Inc', 'is_company': True, 'email': 'parts@globalparts.com',
     'phone': '+49-30-1234-5678', 'city': 'Berlin', 'country_id': env.ref('base.de').id,
     'street': '12 Unter den Linden', 'zip': '10117', 'supplier_rank': 1},
]

company_records = {}
for c in companies:
    rec = _find_or_create(Partner, [('name', '=', c['name'])], c)
    company_records[c['name']] = rec

contacts = [
    {'name': 'Alice Johnson', 'email': 'alice@acme.com', 'phone': '+1-555-0101',
     'function': 'CEO', 'parent_id': company_records['Acme Corporation'].id, 'customer_rank': 1},
    {'name': 'Bob Williams', 'email': 'bob@acme.com', 'phone': '+1-555-0102',
     'function': 'CTO', 'parent_id': company_records['Acme Corporation'].id, 'customer_rank': 1},
    {'name': 'Carol Martinez', 'email': 'carol@globex.com', 'phone': '+1-555-0201',
     'function': 'VP Sales', 'parent_id': company_records['Globex Industries'].id, 'customer_rank': 1},
    {'name': 'David Chen', 'email': 'david@umbrella.io', 'phone': '+44-20-7946-0959',
     'function': 'Procurement Director', 'parent_id': company_records['Umbrella Corp'].id, 'customer_rank': 1},
    {'name': 'Eva Rossi', 'email': 'eva@stark.com', 'phone': '+1-555-0301',
     'function': 'Operations Manager', 'parent_id': company_records['Stark Enterprises'].id, 'customer_rank': 1},
    {'name': 'Frank Dupont', 'email': 'frank@techsupply.com', 'phone': '+33-1-42-68-5301',
     'function': 'Account Manager', 'parent_id': company_records['TechSupply Ltd'].id, 'supplier_rank': 1},
    {'name': 'Grace Kim', 'email': 'grace@wayne.com', 'phone': '+1-555-0401',
     'function': 'Project Lead', 'parent_id': company_records['Wayne Industries'].id, 'customer_rank': 1},
    {'name': 'Henry Müller', 'email': 'henry@globalparts.com', 'phone': '+49-30-1234-5679',
     'function': 'Sales Director', 'parent_id': company_records['Global Parts Inc'].id, 'supplier_rank': 1},
]

for c in contacts:
    _find_or_create(Partner, [('name', '=', c['name']), ('parent_id', '=', c['parent_id'])], c)


# ═══════════════════════════════════════════
# 2. PRODUCTS
# ═══════════════════════════════════════════
print("\n━━━ Seeding Products ━━━")

uom_unit = env.ref('uom.product_uom_unit')
uom_hour = env.ref('uom.product_uom_hour', raise_if_not_found=False) or uom_unit

products_data = [
    {'name': 'Laptop Pro 15"', 'list_price': 1499.00, 'default_code': 'LAPTOP-PRO-15',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id,
     'description_sale': 'High-performance 15-inch laptop with 32GB RAM'},
    {'name': 'Wireless Mouse', 'list_price': 29.99, 'default_code': 'MOUSE-WL-01',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'USB-C Hub 7-in-1', 'list_price': 59.99, 'default_code': 'HUB-USBC-7',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'Monitor 27" 4K', 'list_price': 449.00, 'default_code': 'MON-27-4K',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'Mechanical Keyboard', 'list_price': 89.99, 'default_code': 'KB-MECH-01',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'Standing Desk', 'list_price': 699.00, 'default_code': 'DESK-STAND-01',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'Webcam HD 1080p', 'list_price': 79.99, 'default_code': 'WEBCAM-HD',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'Noise-Cancelling Headset', 'list_price': 199.99, 'default_code': 'HEADSET-NC',
     'type': 'consu', 'sale_ok': True, 'purchase_ok': True, 'uom_id': uom_unit.id},
    {'name': 'IT Consulting (hourly)', 'list_price': 150.00, 'default_code': 'SVC-CONSULT',
     'type': 'service', 'sale_ok': True, 'purchase_ok': False, 'uom_id': uom_hour.id},
    {'name': 'Annual Support Plan', 'list_price': 2999.00, 'default_code': 'SVC-SUPPORT',
     'type': 'service', 'sale_ok': True, 'purchase_ok': False, 'uom_id': uom_unit.id},
]

product_records = {}
for p in products_data:
    rec = _find_or_create(Product, [('default_code', '=', p['default_code'])], p)
    product_records[p['default_code']] = rec


# ═══════════════════════════════════════════
# 3. SALE ORDERS
# ═══════════════════════════════════════════
print("\n━━━ Seeding Sale Orders ━━━")

sales_user = Users.search([('login', '=', 'sales@test.com')], limit=1)
sales_mgr = Users.search([('login', '=', 'sales_manager@test.com')], limit=1)

def get_product_variant(template):
    return env['product.product'].sudo().search([('product_tmpl_id', '=', template.id)], limit=1)

so_data = [
    {'partner': 'Acme Corporation', 'user': sales_user, 'ref': 'DEMO-SO-001',
     'lines': [('LAPTOP-PRO-15', 5), ('MOUSE-WL-01', 10), ('HUB-USBC-7', 5)]},
    {'partner': 'Globex Industries', 'user': sales_mgr or sales_user, 'ref': 'DEMO-SO-002',
     'lines': [('MON-27-4K', 20), ('KB-MECH-01', 20), ('DESK-STAND-01', 10)]},
    {'partner': 'Stark Enterprises', 'user': sales_user, 'ref': 'DEMO-SO-003',
     'lines': [('HEADSET-NC', 50), ('WEBCAM-HD', 50)]},
    {'partner': 'Wayne Industries', 'user': sales_mgr or sales_user, 'ref': 'DEMO-SO-004',
     'lines': [('SVC-CONSULT', 40), ('SVC-SUPPORT', 2)]},
    {'partner': 'Umbrella Corp', 'user': sales_user, 'ref': 'DEMO-SO-005',
     'lines': [('LAPTOP-PRO-15', 10), ('MON-27-4K', 10), ('HEADSET-NC', 10)]},
]

for so in so_data:
    existing = SaleOrder.search([('client_order_ref', '=', so['ref'])], limit=1)
    if existing:
        stats['skipped'] += 1
        continue
    partner = company_records.get(so['partner'])
    if not partner:
        continue
    order_vals = {
        'partner_id': partner.id,
        'user_id': so['user'].id if so['user'] else 2,
        'client_order_ref': so['ref'],
        'date_order': date.today() - timedelta(days=len(so_data)),
    }
    order = SaleOrder.create(order_vals)
    for code, qty in so['lines']:
        tmpl = product_records.get(code)
        if not tmpl:
            continue
        variant = get_product_variant(tmpl)
        if not variant:
            continue
        SaleOrderLine.create({
            'order_id': order.id,
            'product_id': variant.id,
            'product_uom_qty': qty,
            'price_unit': tmpl.list_price,
        })
    stats['created'] += 1
    print(f"  [NEW] sale.order: {order.name} ({so['ref']}) for {so['partner']}")


# ═══════════════════════════════════════════
# 4. CRM LEADS / OPPORTUNITIES
# ═══════════════════════════════════════════
print("\n━━━ Seeding CRM Leads ━━━")

crm_user = sales_user
crm_stages = env['crm.stage'].sudo().search([], order='sequence')
stage_ids = {s.name: s.id for s in crm_stages}

leads_data = [
    {'name': 'Enterprise License Deal', 'partner': 'Acme Corporation',
     'expected_revenue': 75000, 'probability': 60, 'type': 'opportunity',
     'priority': '2', 'stage': 'Qualified'},
    {'name': 'Office Equipment Order', 'partner': 'Globex Industries',
     'expected_revenue': 25000, 'probability': 80, 'type': 'opportunity',
     'priority': '1', 'stage': 'Proposition'},
    {'name': 'Annual Support Renewal', 'partner': 'Umbrella Corp',
     'expected_revenue': 12000, 'probability': 90, 'type': 'opportunity',
     'priority': '3', 'stage': 'Proposition'},
    {'name': 'Consulting Engagement', 'partner': 'Wayne Industries',
     'expected_revenue': 45000, 'probability': 40, 'type': 'opportunity',
     'priority': '1', 'stage': 'Qualified'},
    {'name': 'Hardware Refresh Project', 'partner': 'Stark Enterprises',
     'expected_revenue': 120000, 'probability': 30, 'type': 'opportunity',
     'priority': '2', 'stage': 'New'},
    {'name': 'Website Inquiry - Small Biz', 'expected_revenue': 5000,
     'probability': 10, 'type': 'lead', 'priority': '0',
     'contact_name': 'John Smith', 'email_from': 'john.smith@example.com'},
    {'name': 'Trade Show Lead - Berlin', 'expected_revenue': 15000,
     'probability': 20, 'type': 'lead', 'priority': '1',
     'contact_name': 'Maria Garcia', 'email_from': 'maria@garcia.de'},
    {'name': 'Referral from Acme', 'partner': 'Wayne Industries',
     'expected_revenue': 30000, 'probability': 50, 'type': 'opportunity',
     'priority': '2', 'stage': 'Qualified'},
]

for lead in leads_data:
    existing = CrmLead.search([('name', '=', lead['name'])], limit=1)
    if existing:
        stats['skipped'] += 1
        continue
    vals = {
        'name': lead['name'],
        'expected_revenue': lead.get('expected_revenue', 0),
        'probability': lead.get('probability', 10),
        'type': lead.get('type', 'lead'),
        'priority': lead.get('priority', '0'),
        'user_id': crm_user.id if crm_user else 2,
    }
    partner_name = lead.get('partner')
    if partner_name and partner_name in company_records:
        vals['partner_id'] = company_records[partner_name].id
    if lead.get('contact_name'):
        vals['contact_name'] = lead['contact_name']
    if lead.get('email_from'):
        vals['email_from'] = lead['email_from']
    stage_name = lead.get('stage')
    if stage_name and stage_name in stage_ids:
        vals['stage_id'] = stage_ids[stage_name]
    rec = CrmLead.create(vals)
    stats['created'] += 1
    print(f"  [NEW] crm.lead: {rec.name} ({lead['type']})")


# ═══════════════════════════════════════════
# 5. HR DEPARTMENTS & EMPLOYEES
# ═══════════════════════════════════════════
print("\n━━━ Seeding HR ━━━")

departments_data = [
    'Engineering', 'Sales', 'Human Resources', 'Finance', 'Operations', 'Marketing',
]
dept_records = {}
for d in departments_data:
    rec = _find_or_create(Department, [('name', '=', d)], {'name': d})
    dept_records[d] = rec

employees_data = [
    {'name': 'Alice Johnson', 'job_title': 'Software Engineer', 'department': 'Engineering',
     'work_email': 'alice.j@company.com', 'work_phone': '+1-555-1001'},
    {'name': 'Bob Williams', 'job_title': 'Senior Developer', 'department': 'Engineering',
     'work_email': 'bob.w@company.com', 'work_phone': '+1-555-1002'},
    {'name': 'Carol Martinez', 'job_title': 'Sales Representative', 'department': 'Sales',
     'work_email': 'carol.m@company.com', 'work_phone': '+1-555-1003'},
    {'name': 'David Chen', 'job_title': 'HR Specialist', 'department': 'Human Resources',
     'work_email': 'david.c@company.com', 'work_phone': '+1-555-1004'},
    {'name': 'Eva Rossi', 'job_title': 'Financial Analyst', 'department': 'Finance',
     'work_email': 'eva.r@company.com', 'work_phone': '+1-555-1005'},
    {'name': 'Frank Dupont', 'job_title': 'Operations Coordinator', 'department': 'Operations',
     'work_email': 'frank.d@company.com', 'work_phone': '+1-555-1006'},
    {'name': 'Grace Kim', 'job_title': 'Marketing Manager', 'department': 'Marketing',
     'work_email': 'grace.k@company.com', 'work_phone': '+1-555-1007'},
    {'name': 'Henry Müller', 'job_title': 'DevOps Engineer', 'department': 'Engineering',
     'work_email': 'henry.m@company.com', 'work_phone': '+1-555-1008'},
    {'name': 'Isla Tanaka', 'job_title': 'UX Designer', 'department': 'Engineering',
     'work_email': 'isla.t@company.com', 'work_phone': '+1-555-1009'},
    {'name': 'James Brown', 'job_title': 'Account Executive', 'department': 'Sales',
     'work_email': 'james.b@company.com', 'work_phone': '+1-555-1010'},
]

for emp in employees_data:
    dept = dept_records.get(emp['department'])
    vals = {
        'name': emp['name'],
        'job_title': emp['job_title'],
        'department_id': dept.id if dept else False,
        'work_email': emp['work_email'],
        'work_phone': emp['work_phone'],
    }
    _find_or_create(Employee, [('work_email', '=', emp['work_email'])], vals)


# ═══════════════════════════════════════════
# 6. PURCHASE ORDERS (if module installed)
# ═══════════════════════════════════════════
if has_purchase:
    print("\n━━━ Seeding Purchase Orders ━━━")

    purchase_user = Users.search([('login', '=', 'purchase@test.com')], limit=1)

    po_data = [
        {'supplier': 'TechSupply Ltd', 'ref': 'DEMO-PO-001',
         'lines': [('LAPTOP-PRO-15', 20, 1200.00), ('MON-27-4K', 15, 350.00)]},
        {'supplier': 'Global Parts Inc', 'ref': 'DEMO-PO-002',
         'lines': [('MOUSE-WL-01', 100, 18.00), ('KB-MECH-01', 50, 55.00), ('HUB-USBC-7', 30, 35.00)]},
        {'supplier': 'TechSupply Ltd', 'ref': 'DEMO-PO-003',
         'lines': [('WEBCAM-HD', 40, 50.00), ('HEADSET-NC', 30, 120.00)]},
    ]

    for po in po_data:
        existing = PurchaseOrder.search([('partner_ref', '=', po['ref'])], limit=1)
        if existing:
            stats['skipped'] += 1
            continue
        supplier = company_records.get(po['supplier'])
        if not supplier:
            continue
        order = PurchaseOrder.create({
            'partner_id': supplier.id,
            'partner_ref': po['ref'],
            'date_order': date.today() - timedelta(days=3),
        })
        for code, qty, price in po['lines']:
            tmpl = product_records.get(code)
            if not tmpl:
                continue
            variant = get_product_variant(tmpl)
            if not variant:
                continue
            PurchaseOrderLine.create({
                'order_id': order.id,
                'product_id': variant.id,
                'product_qty': qty,
                'price_unit': price,
            })
        stats['created'] += 1
        print(f"  [NEW] purchase.order: {order.name} ({po['ref']}) from {po['supplier']}")


# ═══════════════════════════════════════════
# 7. PROJECTS & TASKS (if module installed)
# ═══════════════════════════════════════════
has_project = 'project.project' in env
if has_project:
    print("\n━━━ Seeding Projects & Tasks ━━━")

    Project = env['project.project'].sudo()
    Task = env['project.task'].sudo()

    proj_mgr = Users.search([('login', '=', 'project_mgr@test.com')], limit=1) or Users.browse(2)
    proj_user = Users.search([('login', '=', 'project@test.com')], limit=1) or Users.browse(2)

    projects_data = [
        {
            'name': 'Website Redesign',
            'partner': 'Acme Corporation',
            'user': proj_mgr,
            'description': '<p>Complete redesign of the corporate website with modern UI/UX.</p>',
            'tasks': [
                {'name': 'Wireframe mockups', 'priority': '1',
                 'description': '<p>Create wireframes for all main pages</p>'},
                {'name': 'Frontend development', 'priority': '0',
                 'description': '<p>Implement HTML/CSS/JS based on approved designs</p>'},
                {'name': 'Backend API integration', 'priority': '1',
                 'description': '<p>Connect frontend to backend services</p>'},
                {'name': 'QA & Browser testing', 'priority': '0',
                 'description': '<p>Cross-browser and responsive testing</p>'},
                {'name': 'Launch & DNS cutover', 'priority': '2',
                 'description': '<p>Deploy to production and update DNS records</p>'},
            ],
        },
        {
            'name': 'ERP Implementation',
            'partner': 'Globex Industries',
            'user': proj_mgr,
            'description': '<p>Full ERP rollout including Sales, Inventory, and Accounting modules.</p>',
            'tasks': [
                {'name': 'Requirements gathering', 'priority': '1',
                 'description': '<p>Document all business requirements with stakeholders</p>'},
                {'name': 'Data migration plan', 'priority': '1',
                 'description': '<p>Plan migration from legacy system</p>'},
                {'name': 'Module configuration', 'priority': '0',
                 'description': '<p>Configure Sales, Inventory, Accounting modules</p>'},
                {'name': 'User training sessions', 'priority': '0',
                 'description': '<p>Train department leads on new system</p>'},
                {'name': 'Go-live support', 'priority': '2',
                 'description': '<p>Provide on-site support during first week</p>'},
            ],
        },
        {
            'name': 'Mobile App Development',
            'partner': 'Wayne Industries',
            'user': proj_user,
            'description': '<p>Develop iOS and Android companion apps for field workers.</p>',
            'tasks': [
                {'name': 'UI/UX design for mobile', 'priority': '1',
                 'description': '<p>Design mobile-specific flows and screens</p>'},
                {'name': 'iOS development', 'priority': '0',
                 'description': '<p>Swift-based iOS app implementation</p>'},
                {'name': 'Android development', 'priority': '0',
                 'description': '<p>Kotlin-based Android app implementation</p>'},
                {'name': 'Push notification service', 'priority': '1',
                 'description': '<p>Implement push notification backend</p>'},
                {'name': 'App store submission', 'priority': '0',
                 'description': '<p>Prepare assets and submit to Apple/Google stores</p>'},
            ],
        },
        {
            'name': 'IT Infrastructure Upgrade',
            'partner': 'Stark Enterprises',
            'user': proj_mgr,
            'description': '<p>Upgrade server infrastructure, networking, and security systems.</p>',
            'tasks': [
                {'name': 'Server audit & capacity planning', 'priority': '1',
                 'description': '<p>Audit current servers and plan capacity needs</p>'},
                {'name': 'Network redesign', 'priority': '1',
                 'description': '<p>Redesign network topology for redundancy</p>'},
                {'name': 'Firewall & security hardening', 'priority': '2',
                 'description': '<p>Implement new firewall rules and security policies</p>'},
                {'name': 'Cloud migration (Phase 1)', 'priority': '0',
                 'description': '<p>Migrate non-critical workloads to cloud</p>'},
                {'name': 'Disaster recovery testing', 'priority': '1',
                 'description': '<p>Test backup and recovery procedures</p>'},
            ],
        },
        {
            'name': 'Data Analytics Platform',
            'partner': 'Umbrella Corp',
            'user': proj_user,
            'description': '<p>Build a real-time analytics dashboard for business intelligence.</p>',
            'tasks': [
                {'name': 'Data warehouse design', 'priority': '1',
                 'description': '<p>Design star schema for analytics warehouse</p>'},
                {'name': 'ETL pipeline development', 'priority': '0',
                 'description': '<p>Build extract-transform-load pipelines</p>'},
                {'name': 'Dashboard UI development', 'priority': '1',
                 'description': '<p>Create interactive dashboard with charts</p>'},
                {'name': 'Report generation engine', 'priority': '0',
                 'description': '<p>Automated PDF/Excel report generation</p>'},
            ],
        },
    ]

    for proj_data in projects_data:
        existing = Project.search([('name', '=', proj_data['name'])], limit=1)
        if existing:
            stats['skipped'] += 1
            project = existing
        else:
            partner = company_records.get(proj_data['partner'])
            vals = {
                'name': proj_data['name'],
                'user_id': proj_data['user'].id,
                'description': proj_data.get('description', ''),
            }
            if partner:
                vals['partner_id'] = partner.id
            project = Project.create(vals)
            stats['created'] += 1
            print(f"  [NEW] project.project: {project.name}")

        for task_data in proj_data.get('tasks', []):
            existing_task = Task.search([
                ('name', '=', task_data['name']),
                ('project_id', '=', project.id),
            ], limit=1)
            if existing_task:
                stats['skipped'] += 1
                continue
            task_vals = {
                'name': task_data['name'],
                'project_id': project.id,
                'user_ids': [(6, 0, [proj_data['user'].id])],
                'priority': task_data.get('priority', '0'),
                'description': task_data.get('description', ''),
                'date_deadline': date.today() + timedelta(days=30),
            }
            Task.create(task_vals)
            stats['created'] += 1
            print(f"  [NEW] project.task: {task_data['name']} → {project.name}")


# ═══════════════════════════════════════════
# 8. CUSTOMER INVOICES (account.move)
# ═══════════════════════════════════════════
if has_account:
    print("\n━━━ Seeding Customer Invoices ━━━")

    AccountMove = env['account.move'].sudo()
    AccountMoveLine = env['account.move.line'].sudo()

    sale_journal = env['account.journal'].sudo().search([('type', '=', 'sale')], limit=1)

    invoices_data = [
        {'partner': 'Acme Corporation', 'ref': 'DEMO-INV-001', 'move_type': 'out_invoice',
         'lines': [
             {'name': 'Laptop Pro 15" x5', 'quantity': 5, 'price_unit': 1499.00},
             {'name': 'Wireless Mouse x10', 'quantity': 10, 'price_unit': 29.99},
         ]},
        {'partner': 'Globex Industries', 'ref': 'DEMO-INV-002', 'move_type': 'out_invoice',
         'lines': [
             {'name': 'Monitor 27" 4K x20', 'quantity': 20, 'price_unit': 449.00},
             {'name': 'Standing Desk x10', 'quantity': 10, 'price_unit': 699.00},
         ]},
        {'partner': 'Stark Enterprises', 'ref': 'DEMO-INV-003', 'move_type': 'out_invoice',
         'lines': [
             {'name': 'Noise-Cancelling Headset x50', 'quantity': 50, 'price_unit': 199.99},
             {'name': 'Webcam HD 1080p x50', 'quantity': 50, 'price_unit': 79.99},
         ]},
        {'partner': 'Wayne Industries', 'ref': 'DEMO-INV-004', 'move_type': 'out_invoice',
         'lines': [
             {'name': 'IT Consulting (40h)', 'quantity': 40, 'price_unit': 150.00},
             {'name': 'Annual Support Plan x2', 'quantity': 2, 'price_unit': 2999.00},
         ]},
        {'partner': 'Umbrella Corp', 'ref': 'DEMO-INV-005', 'move_type': 'out_invoice',
         'lines': [
             {'name': 'Laptop Pro 15" x10', 'quantity': 10, 'price_unit': 1499.00},
             {'name': 'Mechanical Keyboard x10', 'quantity': 10, 'price_unit': 89.99},
         ]},
        {'partner': 'TechSupply Ltd', 'ref': 'DEMO-BILL-001', 'move_type': 'in_invoice',
         'lines': [
             {'name': 'Laptop Pro wholesale x20', 'quantity': 20, 'price_unit': 1200.00},
             {'name': 'Monitor wholesale x15', 'quantity': 15, 'price_unit': 350.00},
         ]},
        {'partner': 'Global Parts Inc', 'ref': 'DEMO-BILL-002', 'move_type': 'in_invoice',
         'lines': [
             {'name': 'Mouse wholesale x100', 'quantity': 100, 'price_unit': 18.00},
             {'name': 'Keyboard wholesale x50', 'quantity': 50, 'price_unit': 55.00},
         ]},
    ]

    for inv in invoices_data:
        existing = AccountMove.search([('ref', '=', inv['ref'])], limit=1)
        if existing:
            stats['skipped'] += 1
            continue
        partner = company_records.get(inv['partner'])
        if not partner:
            continue

        invoice_lines = []
        for line in inv['lines']:
            invoice_lines.append((0, 0, {
                'name': line['name'],
                'quantity': line['quantity'],
                'price_unit': line['price_unit'],
            }))

        move_vals = {
            'move_type': inv['move_type'],
            'partner_id': partner.id,
            'ref': inv['ref'],
            'invoice_date': date.today() - timedelta(days=7),
            'invoice_line_ids': invoice_lines,
        }
        if sale_journal and inv['move_type'].startswith('out'):
            move_vals['journal_id'] = sale_journal.id

        try:
            move = AccountMove.create(move_vals)
            stats['created'] += 1
            label = 'Invoice' if inv['move_type'] == 'out_invoice' else 'Bill'
            print(f"  [NEW] account.move: {move.name} ({label}, {inv['ref']}) for {inv['partner']}")
        except Exception as e:
            print(f"  [ERR] account.move {inv['ref']}: {e}")


# ═══════════════════════════════════════════
# 9. CRM LEAD ACTIVITIES
# ═══════════════════════════════════════════
print("\n━━━ Seeding CRM Lead Activities ━━━")

Activity = env['mail.activity'].sudo()
crm_model = env['ir.model'].sudo().search([('model', '=', 'crm.lead')], limit=1)
all_leads = CrmLead.search([('type', '=', 'opportunity')], limit=10)

activity_templates = [
    {'type_id': 2, 'summary': 'Follow-up call with decision maker',
     'note': '<p>Discuss pricing and timeline with the client contact.</p>', 'days': 3},
    {'type_id': 1, 'summary': 'Send updated proposal via email',
     'note': '<p>Email the revised proposal with new pricing tiers.</p>', 'days': 5},
    {'type_id': 3, 'summary': 'Schedule product demo meeting',
     'note': '<p>Set up a live demo of the platform for the team.</p>', 'days': 7},
    {'type_id': 4, 'summary': 'Prepare contract for review',
     'note': '<p>Draft the contract and send to legal for approval.</p>', 'days': 10},
    {'type_id': 2, 'summary': 'Check in on procurement status',
     'note': '<p>Call to check budget approval and procurement timeline.</p>', 'days': 14},
]

if crm_model:
    activity_idx = 0
    for lead in all_leads:
        tmpl = activity_templates[activity_idx % len(activity_templates)]
        existing_act = Activity.search([
            ('res_model_id', '=', crm_model.id),
            ('res_id', '=', lead.id),
            ('summary', '=', tmpl['summary']),
        ], limit=1)
        if existing_act:
            stats['skipped'] += 1
            activity_idx += 1
            continue
        try:
            Activity.create({
                'res_model_id': crm_model.id,
                'res_id': lead.id,
                'activity_type_id': tmpl['type_id'],
                'summary': tmpl['summary'],
                'note': tmpl['note'],
                'date_deadline': date.today() + timedelta(days=tmpl['days']),
                'user_id': lead.user_id.id or 2,
            })
            stats['created'] += 1
            print(f"  [NEW] mail.activity: '{tmpl['summary']}' → {lead.name}")
        except Exception as e:
            print(f"  [ERR] activity for {lead.name}: {e}")
        activity_idx += 1

        if activity_idx < len(activity_templates) * 2:
            tmpl2 = activity_templates[(activity_idx + 2) % len(activity_templates)]
            existing_act2 = Activity.search([
                ('res_model_id', '=', crm_model.id),
                ('res_id', '=', lead.id),
                ('summary', '=', tmpl2['summary']),
            ], limit=1)
            if not existing_act2:
                try:
                    Activity.create({
                        'res_model_id': crm_model.id,
                        'res_id': lead.id,
                        'activity_type_id': tmpl2['type_id'],
                        'summary': tmpl2['summary'],
                        'note': tmpl2['note'],
                        'date_deadline': date.today() + timedelta(days=tmpl2['days'] + 7),
                        'user_id': lead.user_id.id or 2,
                    })
                    stats['created'] += 1
                    print(f"  [NEW] mail.activity: '{tmpl2['summary']}' → {lead.name}")
                except Exception as e:
                    print(f"  [ERR] activity for {lead.name}: {e}")
else:
    print("  [SKIP] crm.lead model not found in ir.model")


env.cr.commit()
print(f"\n═══ Demo data seeding complete ═══")
print(f"  Created: {stats['created']}")
print(f"  Skipped (already exist): {stats['skipped']}")
