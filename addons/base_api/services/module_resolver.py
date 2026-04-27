# -*- coding: utf-8 -*-
"""Resolve Odoo model names to module keys.

Uses the MODULE_ACCESS_MAP from simple_api.py to build a reverse lookup.
Example: 'crm.lead' → 'crm', 'sale.order' ��� 'sales', 'hr.employee' → 'hr'

For models not in the map (e.g., 'res.users', 'res.company', 'ir.model'),
returns None — these are "system models" that are always accessible regardless of plan.
"""

# Primary mappings — derived from SimpleApiController.MODULE_ACCESS_MAP
_PRIMARY_MODEL_MAPPINGS = {
    'crm.lead': 'crm',
    'sale.order': 'sales',
    'hr.employee': 'hr',
    'account.move': 'accounting',
    'stock.picking': 'inventory',
    'purchase.order': 'purchase',
    'res.partner': 'contacts',
    'product.template': 'products',
    'project.project': 'project',
    'calendar.event': 'calendar',
    'debt.record': 'debt',
}

# Secondary mappings — related models that belong to the same module
_EXTRA_MODEL_MAPPINGS = {
    'sale.order.line': 'sales',
    'purchase.order.line': 'purchase',
    'account.move.line': 'accounting',
    'hr.department': 'hr',
    'hr.job': 'hr',
    'hr.contract': 'hr',
    'hr.resume.line': 'hr',
    'product.product': 'products',
    'stock.quant': 'inventory',
    'stock.move': 'inventory',
    'stock.warehouse': 'inventory',
    'stock.location': 'inventory',
    'project.task': 'project',
    'crm.stage': 'crm',
    'debt.payment': 'debt',
}

# Combined lookup
MODEL_TO_MODULE = {**_PRIMARY_MODEL_MAPPINGS, **_EXTRA_MODEL_MAPPINGS}


def resolve_module_key(model_name):
    """Return the module key for an Odoo model name, or None if it's a system model.

    Args:
        model_name: Odoo model technical name (e.g. 'crm.lead', 'sale.order')

    Returns:
        Module key string (e.g. 'crm', 'sales') or None for system/utility models.
    """
    return MODEL_TO_MODULE.get(model_name, None)
