"""
Populate odoo19_db with realistic mock data across all installed modules.

Run with:
    python3 odoo-bin shell --addons-path=addons -d odoo19_db < scripts/populate_mock_data.py
"""
import random
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def rand_phone():
    return f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"

def rand_date(start_days_ago=365, end_days_ago=0):
    delta = random.randint(end_days_ago, start_days_ago)
    return (datetime.now() - timedelta(days=delta)).strftime('%Y-%m-%d')

# ---------------------------------------------------------------------------
# 1. Product Categories
# ---------------------------------------------------------------------------
print("\n=== Creating Product Categories ===")
ProductCategory = env['product.category']
categories = {}
for name in ['Electronics', 'Furniture', 'Office Supplies', 'Software', 'Services']:
    cat = ProductCategory.search([('name', '=', name)], limit=1)
    if not cat:
        cat = ProductCategory.create({'name': name})
        print(f"  [OK] Category: {name}")
    else:
        print(f"  [SKIP] Category: {name}")
    categories[name] = cat

# ---------------------------------------------------------------------------
# 2. Products
# ---------------------------------------------------------------------------
print("\n=== Creating Products ===")
ProductTemplate = env['product.template']
PRODUCTS = [
    {'name': 'Laptop Pro 15"', 'list_price': 1299.99, 'default_code': 'ELEC-LP15', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Wireless Mouse', 'list_price': 29.99, 'default_code': 'ELEC-WM01', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'USB-C Hub', 'list_price': 49.99, 'default_code': 'ELEC-HUB1', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
    {'name': '27" Monitor', 'list_price': 399.99, 'default_code': 'ELEC-MON27', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Mechanical Keyboard', 'list_price': 89.99, 'default_code': 'ELEC-KB01', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Standing Desk', 'list_price': 599.99, 'default_code': 'FURN-SD01', 'categ_id': categories['Furniture'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Ergonomic Chair', 'list_price': 449.99, 'default_code': 'FURN-EC01', 'categ_id': categories['Furniture'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Bookshelf', 'list_price': 189.99, 'default_code': 'FURN-BS01', 'categ_id': categories['Furniture'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Printer Paper (A4 500 sheets)', 'list_price': 12.99, 'default_code': 'OFFC-PP01', 'categ_id': categories['Office Supplies'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Whiteboard Markers (12-pack)', 'list_price': 8.49, 'default_code': 'OFFC-WM12', 'categ_id': categories['Office Supplies'].id, 'type': 'consu', 'sale_ok': True},
    {'name': 'Odoo Enterprise License', 'list_price': 2400.00, 'default_code': 'SOFT-OE01', 'categ_id': categories['Software'].id, 'type': 'service', 'sale_ok': True},
    {'name': 'Cloud Hosting (Annual)', 'list_price': 1200.00, 'default_code': 'SOFT-CH01', 'categ_id': categories['Software'].id, 'type': 'service', 'sale_ok': True},
    {'name': 'IT Consulting (per hour)', 'list_price': 150.00, 'default_code': 'SERV-ITC1', 'categ_id': categories['Services'].id, 'type': 'service', 'sale_ok': True},
    {'name': 'Training Session (per day)', 'list_price': 800.00, 'default_code': 'SERV-TS01', 'categ_id': categories['Services'].id, 'type': 'service', 'sale_ok': True},
    {'name': 'Webcam HD', 'list_price': 69.99, 'default_code': 'ELEC-WC01', 'categ_id': categories['Electronics'].id, 'type': 'consu', 'sale_ok': True},
]
product_records = []
for p in PRODUCTS:
    p_name = p['name']
    p_price = p['list_price']
    existing = ProductTemplate.search([('default_code', '=', p['default_code'])], limit=1)
    if existing:
        product_records.append(existing)
        print(f"  [SKIP] {p_name}")
    else:
        rec = ProductTemplate.create(dict(p))
        product_records.append(rec)
        print(f"  [OK] {p_name} (${p_price})")

# ---------------------------------------------------------------------------
# 3. Partners (Companies & Contacts)
# ---------------------------------------------------------------------------
print("\n=== Creating Partners ===")
Partner = env['res.partner']
COMPANIES = [
    {'name': 'Acme Corp', 'email': 'info@acme.com', 'phone': rand_phone(), 'city': 'New York', 'is_company': True, 'customer_rank': 1},
    {'name': 'TechStart Inc', 'email': 'hello@techstart.io', 'phone': rand_phone(), 'city': 'San Francisco', 'is_company': True, 'customer_rank': 1},
    {'name': 'Global Logistics', 'email': 'contact@globallog.com', 'phone': rand_phone(), 'city': 'Chicago', 'is_company': True, 'customer_rank': 1},
    {'name': 'Creative Design Studio', 'email': 'studio@creative.co', 'phone': rand_phone(), 'city': 'Los Angeles', 'is_company': True, 'customer_rank': 1},
    {'name': 'DataDriven Analytics', 'email': 'data@dda.com', 'phone': rand_phone(), 'city': 'Boston', 'is_company': True, 'customer_rank': 1},
    {'name': 'GreenEnergy Solutions', 'email': 'info@greenenergy.net', 'phone': rand_phone(), 'city': 'Austin', 'is_company': True, 'customer_rank': 1, 'supplier_rank': 1},
    {'name': 'MegaSupply Co', 'email': 'orders@megasupply.com', 'phone': rand_phone(), 'city': 'Dallas', 'is_company': True, 'supplier_rank': 1},
    {'name': 'CloudFirst SaaS', 'email': 'support@cloudfirst.io', 'phone': rand_phone(), 'city': 'Seattle', 'is_company': True, 'customer_rank': 1},
]
company_records = []
for c in COMPANIES:
    c_name = c['name']
    existing = Partner.search([('name', '=', c_name), ('is_company', '=', True)], limit=1)
    if existing:
        company_records.append(existing)
        print(f"  [SKIP] Company: {c_name}")
    else:
        rec = Partner.create(dict(c))
        company_records.append(rec)
        print(f"  [OK] Company: {c_name}")

CONTACTS = [
    {'name': 'Alice Johnson', 'email': 'alice@acme.com', 'phone': rand_phone(), 'function': 'CEO'},
    {'name': 'Bob Smith', 'email': 'bob@techstart.io', 'phone': rand_phone(), 'function': 'CTO'},
    {'name': 'Carol Williams', 'email': 'carol@globallog.com', 'phone': rand_phone(), 'function': 'Procurement Manager'},
    {'name': 'David Lee', 'email': 'david@creative.co', 'phone': rand_phone(), 'function': 'Art Director'},
    {'name': 'Eva Martinez', 'email': 'eva@dda.com', 'phone': rand_phone(), 'function': 'Data Scientist'},
    {'name': 'Frank Brown', 'email': 'frank@greenenergy.net', 'phone': rand_phone(), 'function': 'VP Sales'},
    {'name': 'Grace Kim', 'email': 'grace@megasupply.com', 'phone': rand_phone(), 'function': 'Supply Chain Lead'},
    {'name': 'Henry Chen', 'email': 'henry@cloudfirst.io', 'phone': rand_phone(), 'function': 'DevOps Engineer'},
    {'name': 'Irene Davis', 'email': 'irene@acme.com', 'phone': rand_phone(), 'function': 'HR Manager'},
    {'name': 'Jack Wilson', 'email': 'jack@techstart.io', 'phone': rand_phone(), 'function': 'Sales Rep'},
    {'name': 'Karen Moore', 'email': 'karen@standalone.com', 'phone': rand_phone(), 'function': 'Freelancer', 'customer_rank': 1},
    {'name': 'Leo Garcia', 'email': 'leo@standalone.com', 'phone': rand_phone(), 'function': 'Consultant', 'customer_rank': 1},
]
for i, ct in enumerate(CONTACTS):
    ct_name = ct['name']
    existing = Partner.search([('email', '=', ct['email'])], limit=1)
    if existing:
        print(f"  [SKIP] Contact: {ct_name}")
    else:
        vals = dict(ct)
        if i < len(company_records):
            vals['parent_id'] = company_records[i % len(company_records)].id
        Partner.create(vals)
        print(f"  [OK] Contact: {ct_name}")

# ---------------------------------------------------------------------------
# 4. HR Departments & Employees
# ---------------------------------------------------------------------------
print("\n=== Creating HR Data ===")
Department = env['hr.department']
Employee = env['hr.employee']

DEPARTMENTS = ['Engineering', 'Sales', 'Human Resources', 'Marketing', 'Finance']
dept_records = {}
for dept_name in DEPARTMENTS:
    existing = Department.search([('name', '=', dept_name)], limit=1)
    if not existing:
        existing = Department.create({'name': dept_name})
        print(f"  [OK] Department: {dept_name}")
    else:
        print(f"  [SKIP] Department: {dept_name}")
    dept_records[dept_name] = existing

EMPLOYEES = [
    {'name': 'Emma Thompson', 'job_title': 'Senior Developer', 'department_id': 'Engineering', 'work_email': 'emma@company.com', 'work_phone': rand_phone()},
    {'name': 'Liam Parker', 'job_title': 'Account Executive', 'department_id': 'Sales', 'work_email': 'liam@company.com', 'work_phone': rand_phone()},
    {'name': 'Sophia Reed', 'job_title': 'HR Specialist', 'department_id': 'Human Resources', 'work_email': 'sophia@company.com', 'work_phone': rand_phone()},
    {'name': 'Noah Clark', 'job_title': 'Marketing Lead', 'department_id': 'Marketing', 'work_email': 'noah@company.com', 'work_phone': rand_phone()},
    {'name': 'Olivia Hayes', 'job_title': 'Financial Analyst', 'department_id': 'Finance', 'work_email': 'olivia@company.com', 'work_phone': rand_phone()},
    {'name': 'James Rivera', 'job_title': 'DevOps Engineer', 'department_id': 'Engineering', 'work_email': 'james@company.com', 'work_phone': rand_phone()},
    {'name': 'Mia Scott', 'job_title': 'Sales Manager', 'department_id': 'Sales', 'work_email': 'mia@company.com', 'work_phone': rand_phone()},
    {'name': 'Benjamin Ross', 'job_title': 'QA Engineer', 'department_id': 'Engineering', 'work_email': 'benjamin@company.com', 'work_phone': rand_phone()},
]
for emp in EMPLOYEES:
    emp_name = emp['name']
    existing = Employee.search([('work_email', '=', emp['work_email'])], limit=1)
    if existing:
        print(f"  [SKIP] Employee: {emp_name}")
    else:
        vals = dict(emp)
        vals['department_id'] = dept_records[vals['department_id']].id
        Employee.create(vals)
        print(f"  [OK] Employee: {emp_name}")

# ---------------------------------------------------------------------------
# 5. CRM Leads / Opportunities
# ---------------------------------------------------------------------------
print("\n=== Creating CRM Leads ===")
Lead = env['crm.lead']
LEADS = [
    {'name': 'Enterprise ERP Implementation', 'partner_id': company_records[0].id, 'expected_revenue': 50000, 'type': 'opportunity', 'priority': '2'},
    {'name': 'Cloud Migration Project', 'partner_id': company_records[1].id, 'expected_revenue': 35000, 'type': 'opportunity', 'priority': '3'},
    {'name': 'Office Equipment Bulk Order', 'partner_id': company_records[2].id, 'expected_revenue': 12000, 'type': 'opportunity', 'priority': '1'},
    {'name': 'Website Redesign', 'partner_id': company_records[3].id, 'expected_revenue': 8000, 'type': 'lead', 'priority': '1'},
    {'name': 'Data Analytics Platform', 'partner_id': company_records[4].id, 'expected_revenue': 75000, 'type': 'opportunity', 'priority': '3'},
    {'name': 'Solar Panel Installation', 'partner_id': company_records[5].id, 'expected_revenue': 25000, 'type': 'lead', 'priority': '2'},
    {'name': 'Supply Chain Optimization', 'partner_id': company_records[6].id, 'expected_revenue': 40000, 'type': 'opportunity', 'priority': '2'},
    {'name': 'SaaS Annual Renewal', 'partner_id': company_records[7].id, 'expected_revenue': 15000, 'type': 'opportunity', 'priority': '1'},
    {'name': 'Training Program for Staff', 'expected_revenue': 5000, 'type': 'lead', 'priority': '0', 'contact_name': 'Unknown Prospect', 'email_from': 'prospect@unknown.com'},
    {'name': 'Managed IT Services Inquiry', 'expected_revenue': 20000, 'type': 'lead', 'priority': '1', 'contact_name': 'Pat Doe', 'email_from': 'pat@smallbiz.com'},
]
for lead_data in LEADS:
    lead_name = lead_data['name']
    existing = Lead.search([('name', '=', lead_name)], limit=1)
    if existing:
        print(f"  [SKIP] Lead: {lead_name}")
    else:
        Lead.create(dict(lead_data))
        print(f"  [OK] Lead: {lead_name}")

# ---------------------------------------------------------------------------
# 6. Sale Orders
# ---------------------------------------------------------------------------
print("\n=== Creating Sale Orders ===")
SaleOrder = env['sale.order']
SaleOrderLine = env['sale.order.line']
products_with_variant = []
for pt in product_records:
    pp = env['product.product'].search([('product_tmpl_id', '=', pt.id)], limit=1)
    if pp:
        products_with_variant.append(pp)

if products_with_variant:
    for i, company in enumerate(company_records[:5]):
        existing = SaleOrder.search([('partner_id', '=', company.id)], limit=1)
        if existing:
            print(f"  [SKIP] SO for {company.name}")
            continue
        so = SaleOrder.create({'partner_id': company.id})
        num_lines = random.randint(1, 4)
        chosen = random.sample(products_with_variant, min(num_lines, len(products_with_variant)))
        for prod in chosen:
            SaleOrderLine.create({
                'order_id': so.id,
                'product_id': prod.id,
                'product_uom_qty': random.randint(1, 10),
            })
        print(f"  [OK] Sale Order {so.name} for {company.name} ({len(chosen)} lines)")

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------
env.cr.commit()
print("\n=== Mock data population complete ===")
