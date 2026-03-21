# -*- coding: utf-8 -*-
{
    'name': 'Base REST API',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',
    'category': 'API',
    'summary': 'Headless REST API module for Odoo',
    'description': """
        This module provides a clean, secure, and maintainable API façade
        that does not modify any of Odoo's core modules. It includes:
        - API key authentication
        - Base API controller with request validation
        - Secure API key generation
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['base', 'web', 'sale', 'hr', 'crm'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_users_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
