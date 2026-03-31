# -*- coding: utf-8 -*-
{
    'name': 'Debt Management',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    'category': 'Accounting',
    'summary': 'Customer debt tracking with interest, payments, and notifications',
    'description': """
        Track customer debts with:
        - Debt records linked to sales orders or standalone
        - Configurable interest rules (daily/weekly/monthly/yearly, simple/compound)
        - Payment tracking with automatic balance updates
        - Per-customer debt limits that block new orders when exceeded
        - Automated overdue detection and reminder notifications
        - REST API endpoints under /api/v2/debts/*
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['base_api', 'mail', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/cron_data.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
