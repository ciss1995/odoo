# -*- coding: utf-8 -*-
"""Unit tests for the API error-leakage classifier.

Locks in the contract of ``SimpleApiController._safe_exc_message``:
Odoo's user-facing exception types pass their message through; anything
else gets a generic fallback so SQL fragments, internal IDs, and stack
traces never leak to API callers.
"""

from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.base_api.controllers.simple_api import SimpleApiController


@tagged('post_install', '-at_install')
class TestSafeExcMessage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.controller = SimpleApiController()

    def test_user_error_message_passes_through(self):
        msg = "Vous ne pouvez pas supprimer cette commande validée"
        self.assertEqual(
            self.controller._safe_exc_message(UserError(msg)),
            msg,
        )

    def test_validation_error_message_passes_through(self):
        msg = "Le code TVA doit être unique"
        self.assertEqual(
            self.controller._safe_exc_message(ValidationError(msg)),
            msg,
        )

    def test_missing_error_message_passes_through(self):
        msg = "Record does not exist or has been deleted"
        self.assertEqual(
            self.controller._safe_exc_message(MissingError(msg)),
            msg,
        )

    def test_access_error_message_passes_through(self):
        msg = "You are not allowed to access 'crm.lead'"
        self.assertEqual(
            self.controller._safe_exc_message(AccessError(msg)),
            msg,
        )

    def test_generic_exception_is_suppressed(self):
        """KeyError, AttributeError, etc. must not leak — they can carry
        internal field names, missing dict keys, or object reprs that
        help an attacker map the backend."""
        result = self.controller._safe_exc_message(
            KeyError('secret_internal_field')
        )
        self.assertEqual(result, "Internal error")
        self.assertNotIn('secret_internal_field', result)

    def test_attribute_error_is_suppressed(self):
        result = self.controller._safe_exc_message(
            AttributeError("'NoneType' object has no attribute '_origin_internal'")
        )
        self.assertEqual(result, "Internal error")
        self.assertNotIn('_origin_internal', result)

    def test_type_error_is_suppressed(self):
        result = self.controller._safe_exc_message(
            TypeError("unsupported operand type(s) for +: 'int' and 'res.partner()'")
        )
        self.assertEqual(result, "Internal error")
        self.assertNotIn('res.partner', result)

    def test_custom_fallback_for_generic_exception(self):
        result = self.controller._safe_exc_message(
            RuntimeError("psycopg2.errors.UniqueViolation: duplicate key value violates ..."),
            fallback="Database constraint violated",
        )
        self.assertEqual(result, "Database constraint violated")
        self.assertNotIn('psycopg2', result)
