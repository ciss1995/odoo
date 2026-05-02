# -*- coding: utf-8 -*-
import json
import secrets
import string
from datetime import datetime, timedelta
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestProductCategoriesApi(HttpCase):
    """Integration tests for the product-category endpoints in base_api."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.list_url = '/api/v2/product-categories'
        cls.products_url = '/api/v2/products'
        cls.search_template_url = '/api/v2/search/product.template'

        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        Category = cls.env['product.category']
        cls.parent_category = Category.create({'name': 'API Test Parent'})
        cls.child_category = Category.create({
            'name': 'API Test Child',
            'parent_id': cls.parent_category.id,
        })
        cls.sibling_category = Category.create({'name': 'API Test Sibling'})

        Product = cls.env['product.template']
        cls.product_in_child = Product.create({
            'name': 'API Test Product (child)',
            'list_price': 10.0,
            'sale_ok': True,
            'categ_id': cls.child_category.id,
        })
        cls.product_in_parent = Product.create({
            'name': 'API Test Product (parent)',
            'list_price': 20.0,
            'sale_ok': True,
            'categ_id': cls.parent_category.id,
        })
        cls.product_in_sibling = Product.create({
            'name': 'API Test Product (sibling)',
            'list_price': 30.0,
            'sale_ok': True,
            'categ_id': cls.sibling_category.id,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _login(self, login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', login)], limit=1)
        self.assertTrue(user, f"User not found: {login}")
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
        token_hash = self.env['api.session']._hash_token(token)
        self.env['api.session'].sudo().create({
            'user_id': user.id,
            'token': token_hash,
            'expires_at': datetime.now() + timedelta(hours=24),
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'active': True,
        })
        return token

    def _get(self, url, token=None):
        headers = {}
        if token:
            headers['session-token'] = token
        return self.url_open(url, headers=headers)

    def _post(self, url, payload, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(url, data=json.dumps(payload), headers=headers)

    # ------------------------------------------------------------------
    # GET /api/v2/product-categories
    # ------------------------------------------------------------------

    def test_list_categories_requires_auth(self):
        """Unauthenticated request is rejected."""
        resp = self._get(self.list_url)
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertEqual(body['error']['code'], 'MISSING_API_KEY')

    def test_list_categories_returns_seeded_records(self):
        """Authenticated list includes the seeded test categories."""
        token = self._login()
        resp = self._get(f'{self.list_url}?limit=1000', token=token)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertIn('categories', body['data'])
        self.assertIn('can_create', body['data'])
        ids = [c['id'] for c in body['data']['categories']]
        self.assertIn(self.parent_category.id, ids)
        self.assertIn(self.child_category.id, ids)

    def test_list_categories_can_create_true_for_admin(self):
        """Admin user has rights to create product.category."""
        token = self._login()
        resp = self._get(self.list_url, token=token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['data']['can_create'])

    def test_list_categories_search_filter(self):
        """`search` filters by complete_name (case-insensitive)."""
        token = self._login()
        resp = self._get(f'{self.list_url}?search=API+Test+Sibling&limit=50', token=token)
        self.assertEqual(resp.status_code, 200)
        cats = resp.json()['data']['categories']
        names = [c['complete_name'] for c in cats]
        self.assertTrue(any('API Test Sibling' in n for n in names))
        self.assertFalse(any('API Test Child' in n for n in names))

    def test_list_categories_parent_id_filter(self):
        """parent_id filter restricts to direct children."""
        token = self._login()
        resp = self._get(
            f'{self.list_url}?parent_id={self.parent_category.id}&limit=50',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        cats = resp.json()['data']['categories']
        ids = [c['id'] for c in cats]
        self.assertIn(self.child_category.id, ids)
        self.assertNotIn(self.sibling_category.id, ids)
        self.assertNotIn(self.parent_category.id, ids)

    def test_list_categories_invalid_parent_id(self):
        """Non-integer parent_id is rejected."""
        token = self._login()
        resp = self._get(f'{self.list_url}?parent_id=abc', token=token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_list_categories_product_count_includes_descendants(self):
        """product_count on a parent includes products in subcategories."""
        token = self._login()
        resp = self._get(f'{self.list_url}?limit=1000', token=token)
        self.assertEqual(resp.status_code, 200)
        cats_by_id = {c['id']: c for c in resp.json()['data']['categories']}
        parent = cats_by_id[self.parent_category.id]
        # parent has its own product + the child's product
        self.assertGreaterEqual(parent['product_count'], 2)

    # ------------------------------------------------------------------
    # GET /api/v2/products?category_id=...
    # ------------------------------------------------------------------

    def test_products_filtered_by_category_id_includes_descendants(self):
        """category_id filter on /products returns descendants too."""
        token = self._login()
        resp = self._get(
            f'{self.products_url}?category_id={self.parent_category.id}&limit=1000',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        names = [p['name'] for p in resp.json()['data']['products']]
        self.assertIn('API Test Product (parent)', names)
        self.assertIn('API Test Product (child)', names)
        self.assertNotIn('API Test Product (sibling)', names)

    def test_products_response_includes_category_id(self):
        """Response shape now includes both category_id and category name."""
        token = self._login()
        resp = self._get(
            f'{self.products_url}?category_id={self.sibling_category.id}&limit=10',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        products = resp.json()['data']['products']
        self.assertTrue(products)
        for product in products:
            self.assertIn('category_id', product)
            self.assertIn('category', product)
            self.assertEqual(product['category_id'], self.sibling_category.id)

    def test_products_invalid_category_id(self):
        """Non-integer category_id is rejected."""
        token = self._login()
        resp = self._get(f'{self.products_url}?category_id=oops', token=token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_products_unknown_category_id(self):
        """Nonexistent category_id returns 404."""
        token = self._login()
        resp = self._get(f'{self.products_url}?category_id=999999999', token=token)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error']['code'], 'CATEGORY_NOT_FOUND')

    # ------------------------------------------------------------------
    # GET /api/v2/search/product.template?category_id=...
    # ------------------------------------------------------------------

    def test_search_template_filtered_by_category_id_includes_descendants(self):
        """category_id filter on /search/product.template returns descendants too."""
        token = self._login()
        resp = self._get(
            f'{self.search_template_url}?category_id={self.parent_category.id}'
            f'&fields=id,name,categ_id&limit=1000',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        names = [r['name'] for r in resp.json()['data']['records']]
        self.assertIn('API Test Product (parent)', names)
        self.assertIn('API Test Product (child)', names)
        self.assertNotIn('API Test Product (sibling)', names)

    def test_search_template_category_id_returns_full_template_fields(self):
        """Inventory grid fields (qty_available, etc.) are reachable via fields=."""
        token = self._login()
        fields = 'id,name,qty_available,is_storable,write_date'
        resp = self._get(
            f'{self.search_template_url}?category_id={self.sibling_category.id}'
            f'&fields={fields}&limit=10',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['data']['records']
        self.assertTrue(records)
        for record in records:
            self.assertIn('qty_available', record)
            self.assertIn('is_storable', record)
            self.assertIn('write_date', record)

    def test_search_template_invalid_category_id(self):
        """Non-integer category_id is rejected."""
        token = self._login()
        resp = self._get(f'{self.search_template_url}?category_id=oops', token=token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_search_template_unknown_category_id(self):
        """Nonexistent category_id returns 404 CATEGORY_NOT_FOUND."""
        token = self._login()
        resp = self._get(
            f'{self.search_template_url}?category_id=999999999',
            token=token,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error']['code'], 'CATEGORY_NOT_FOUND')

    def test_search_template_category_id_respects_pagination(self):
        """limit/offset still work with category_id."""
        token = self._login()
        resp = self._get(
            f'{self.search_template_url}?category_id={self.parent_category.id}'
            f'&fields=id,name&limit=1&offset=0',
            token=token,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body['data']['records']), 1)
        # parent + child each contributed one product → total_count >= 2
        self.assertGreaterEqual(body['data']['total_count'], 2)

    # ------------------------------------------------------------------
    # POST /api/v2/product-categories
    # ------------------------------------------------------------------

    def test_create_category_success(self):
        """Admin can create a top-level category."""
        token = self._login()
        resp = self._post(self.list_url, {'name': 'Brand New Category'}, token=token)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['name'], 'Brand New Category')
        self.assertFalse(body['data']['parent_id'])
        # Verify it was actually created
        created = self.env['product.category'].browse(body['data']['id'])
        self.assertTrue(created.exists())
        self.assertEqual(created.name, 'Brand New Category')

    def test_create_category_with_parent(self):
        """Creating with parent_id sets the hierarchy."""
        token = self._login()
        resp = self._post(
            self.list_url,
            {'name': 'Sub Of Parent', 'parent_id': self.parent_category.id},
            token=token,
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['data']['parent_id'], self.parent_category.id)
        self.assertIn('API Test Parent', body['data']['complete_name'])

    def test_create_category_missing_name(self):
        """Empty name returns 400 MISSING_NAME."""
        token = self._login()
        resp = self._post(self.list_url, {'name': '   '}, token=token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'MISSING_NAME')

    def test_create_category_unknown_parent(self):
        """Nonexistent parent_id returns 404 PARENT_NOT_FOUND."""
        token = self._login()
        resp = self._post(
            self.list_url,
            {'name': 'Orphan', 'parent_id': 999999999},
            token=token,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error']['code'], 'PARENT_NOT_FOUND')

    def test_create_category_invalid_parent_type(self):
        """Non-integer parent_id is rejected."""
        token = self._login()
        resp = self._post(
            self.list_url,
            {'name': 'Bad Parent', 'parent_id': 'not-an-int'},
            token=token,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_PARAMS')

    def test_create_category_requires_auth(self):
        """Unauthenticated POST is rejected."""
        resp = self._post(self.list_url, {'name': 'Anon'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['error']['code'], 'MISSING_API_KEY')

    def test_create_category_rejects_non_json(self):
        """Wrong Content-Type is rejected with 400."""
        token = self._login()
        headers = {'Content-Type': 'text/plain', 'session-token': token}
        resp = self.url_open(self.list_url, data='name=foo', headers=headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_CONTENT_TYPE')
