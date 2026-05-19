# -*- coding: utf-8 -*-
"""Tests for /api/v2/public/branding — specifically the `features` block
the SPAs use to gate optional UI (product expiry, etc.)."""

import json

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestPublicBrandingFeatures(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.endpoint = '/api/v2/public/branding'

    def test_branding_includes_features_object(self):
        resp = self.url_open(self.endpoint)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
        data = body['data']
        self.assertIn('features', data)
        self.assertIsInstance(data['features'], dict)

    def test_features_product_expiry_is_boolean(self):
        resp = self.url_open(self.endpoint)
        data = json.loads(resp.content)['data']
        self.assertIn('product_expiry', data['features'])
        self.assertIsInstance(data['features']['product_expiry'], bool)

    def test_features_product_expiry_matches_field_presence(self):
        """The flag should reflect whether the product_expiry addon is
        installed (detected via field presence on stock.lot)."""
        resp = self.url_open(self.endpoint)
        data = json.loads(resp.content)['data']
        flag = data['features']['product_expiry']
        # Mirror the controller's heuristic to confirm
        expected = (
            'use_expiration_date' in self.env['stock.lot']._fields
            and 'use_expiration_date' in self.env['product.template']._fields
        )
        self.assertEqual(flag, expected)

    def test_branding_endpoint_remains_unauthenticated(self):
        # No headers at all — must succeed
        resp = self.url_open(self.endpoint, headers={})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body.get('success'))
