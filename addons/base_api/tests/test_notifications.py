# -*- coding: utf-8 -*-
"""Integration tests for /api/v2/notifications/*."""

import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestNotificationsEndpoints(HttpCase):
    """Cover the dedicated notifications controller end-to-end."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_base = '/api/v2/notifications'
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})
        # The test runner blocks external HTTP, so the SubscriptionEnforcer
        # can't reach the Control Plane. Disable enforcement for the duration
        # of the test class — endpoints already short-circuit when get_instance
        # returns None.
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    # ----- helpers ------------------------------------------------------------

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
        return token, user

    def _get(self, path, token=None, params=None):
        qs = ''
        if params:
            qs = '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        headers = {}
        if token:
            headers['session-token'] = token
        return self.url_open(f'{self.api_base}{path}{qs}', headers=headers)

    def _post(self, path, token=None, body=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(
            f'{self.api_base}{path}',
            data=json.dumps(body or {}),
            headers=headers,
        )

    def _make_partner(self, name='NotifTest Partner'):
        return self.env['res.partner'].sudo().create({'name': name})

    def _post_inbox_message(self, partner, recipient_user, body='Hello world'):
        """Create a mail.message + mail.notification(is_read=False) so the
        recipient sees it as needaction.

        We bypass message_post and directly create the records because
        message_post does not produce a needaction when the author is also
        the recipient, and using a separate "author" user complicates setup.
        Direct creation gives us the exact state we need.
        """
        bot = self.env.ref('base.partner_root', raise_if_not_found=False)
        author_id = bot.id if bot else False
        msg = self.env['mail.message'].sudo().create({
            'body': body,
            'subject': 'Test message',
            'message_type': 'comment',
            'subtype_id': self.env.ref('mail.mt_comment').id,
            'model': 'res.partner',
            'res_id': partner.id,
            'author_id': author_id,
        })
        self.env['mail.notification'].sudo().create({
            'mail_message_id': msg.id,
            'res_partner_id': recipient_user.partner_id.id,
            'is_read': False,
            'notification_type': 'inbox',
        })
        return msg

    def _make_activity(self, user, partner, summary='Call client'):
        return self.env['mail.activity'].sudo().create({
            'summary': summary,
            'user_id': user.id,
            'res_model_id': self.env.ref('base.model_res_partner').id,
            'res_model': 'res.partner',
            'res_id': partner.id,
            'date_deadline': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'),
        })

    # ===== auth ===============================================================

    def test_summary_requires_session_token(self):
        resp = self._get('/summary')
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertEqual(body['error']['code'], 'UNAUTHORIZED')

    def test_inbox_rejects_invalid_token(self):
        resp = self._get('/inbox', token='not-a-real-token')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['error']['code'], 'UNAUTHORIZED')

    # ===== summary ============================================================

    def test_summary_returns_two_counts(self):
        token, user = self._login()
        partner = self._make_partner()
        self._post_inbox_message(partner, user)
        self._make_activity(user, partner)

        resp = self._get('/summary', token=token)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()['data']
        self.assertIn('needaction_count', data)
        self.assertIn('feed_unread_count', data)
        self.assertGreaterEqual(data['feed_unread_count'], 1)

    # ===== inbox ==============================================================

    def test_inbox_basic_shape(self):
        token, user = self._login()
        partner = self._make_partner()
        self._post_inbox_message(partner, user, body='<p>Look at this <b>thing</b></p>')

        resp = self._get('/inbox', token=token, params={'filter': 'needaction'})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()['data']
        self.assertIn('items', data)
        self.assertIn('total', data)
        self.assertIn('has_more', data)
        self.assertGreaterEqual(data['total'], 1)
        item = data['items'][0]
        for key in ('id', 'subject', 'preview', 'author', 'date',
                    'message_type', 'model', 'res_id', 'record_name',
                    'starred', 'needaction'):
            self.assertIn(key, item, f"missing key: {key}")
        # preview should be HTML-stripped
        self.assertNotIn('<', item['preview'])
        self.assertIn('thing', item['preview'])

    def test_inbox_pagination_caps_limit(self):
        token, _ = self._login()
        # limit=9999 should be silently capped to 100; no error
        resp = self._get('/inbox', token=token, params={'limit': 9999})
        self.assertEqual(resp.status_code, 200)

    def test_inbox_invalid_filter(self):
        token, _ = self._login()
        resp = self._get('/inbox', token=token, params={'filter': 'banana'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')

    def test_inbox_invalid_pagination(self):
        token, _ = self._login()
        resp = self._get('/inbox', token=token, params={'limit': 'abc'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')

    # ===== model filter parsing ==============================================

    def test_inbox_model_filter_single(self):
        token, user = self._login()
        partner = self._make_partner()
        self._post_inbox_message(partner, user)

        resp = self._get(
            '/inbox', token=token,
            params={'filter': 'needaction', 'model': 'res.partner'},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        for item in resp.json()['data']['items']:
            self.assertEqual(item['model'], 'res.partner')

    def test_inbox_model_filter_comma_list(self):
        token, _ = self._login()
        resp = self._get(
            '/inbox', token=token,
            params={'model': 'res.partner,sale.order'},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_inbox_model_filter_unknown_returns_invalid_input(self):
        token, _ = self._login()
        resp = self._get('/inbox', token=token, params={'model': 'foo.bar'})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body['error']['code'], 'INVALID_INPUT')
        self.assertIn('foo.bar', body['error']['message'])

    def test_inbox_model_filter_too_many(self):
        token, _ = self._login()
        # 21 model names — over the 20 cap
        names = ','.join(f'm{i}' for i in range(21))
        resp = self._get('/inbox', token=token, params={'model': names})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')
        self.assertIn('max 20', resp.json()['error']['message'])

    def test_inbox_model_filter_dedupes(self):
        token, _ = self._login()
        resp = self._get(
            '/inbox', token=token,
            params={'model': 'res.partner,res.partner,res.partner'},
        )
        # dedupe → just res.partner once → no validation error
        self.assertEqual(resp.status_code, 200, resp.text)

    # ===== feed ==============================================================

    def test_feed_basic_shape(self):
        token, user = self._login()
        partner = self._make_partner()
        self._make_activity(user, partner, summary='Follow up')

        resp = self._get('/feed', token=token, params={'filter': 'unread'})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()['data']
        self.assertGreaterEqual(data['total'], 1)
        item = next(i for i in data['items'] if i['title'] == 'Follow up')
        self.assertEqual(item['type'], 'activity')
        self.assertFalse(item['is_read'])
        self.assertEqual(item['model'], 'res.partner')

    def test_feed_dismissed_excluded_from_unread(self):
        token, user = self._login()
        partner = self._make_partner()
        act = self._make_activity(user, partner, summary='Dismissable')

        # dismiss it
        resp = self._post('/mark-read', token=token, body={
            'kind': 'feed', 'ids': [act.id],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()['data']['updated'], 1)

        # unread feed should now omit it
        resp = self._get('/feed', token=token, params={'filter': 'unread'})
        ids = [i['id'] for i in resp.json()['data']['items']]
        self.assertNotIn(act.id, ids)

        # all feed should still include it, with is_read=true
        resp = self._get('/feed', token=token, params={'filter': 'all'})
        items = resp.json()['data']['items']
        match = next((i for i in items if i['id'] == act.id), None)
        self.assertIsNotNone(match)
        self.assertTrue(match['is_read'])

    # ===== mark-read =========================================================

    def test_mark_read_inbox_clears_needaction(self):
        token, user = self._login()
        partner = self._make_partner()
        msg = self._post_inbox_message(partner, user)

        # confirm it is needaction
        resp = self._get('/inbox', token=token, params={'filter': 'needaction'})
        self.assertIn(msg.id, [i['id'] for i in resp.json()['data']['items']])

        resp = self._post('/mark-read', token=token, body={
            'kind': 'inbox', 'ids': [msg.id],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()['data']['updated'], 1)

        # No longer needaction
        resp = self._get('/inbox', token=token, params={'filter': 'needaction'})
        self.assertNotIn(msg.id, [i['id'] for i in resp.json()['data']['items']])

    def test_mark_read_feed_idempotent(self):
        token, user = self._login()
        partner = self._make_partner()
        act = self._make_activity(user, partner)

        first = self._post('/mark-read', token=token, body={
            'kind': 'feed', 'ids': [act.id],
        }).json()
        self.assertEqual(first['data']['updated'], 1)

        # second call: nothing new to insert
        second = self._post('/mark-read', token=token, body={
            'kind': 'feed', 'ids': [act.id],
        }).json()
        self.assertEqual(second['data']['updated'], 0)

    def test_mark_read_invalid_kind(self):
        token, _ = self._login()
        resp = self._post('/mark-read', token=token, body={
            'kind': 'whatever', 'ids': [1],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')

    def test_mark_read_missing_ids(self):
        token, _ = self._login()
        resp = self._post('/mark-read', token=token, body={'kind': 'feed'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')

    def test_mark_read_feed_does_not_dismiss_other_users_activities(self):
        """Even if a user POSTs another user's activity id, dismissal
        should silently no-op (we re-scope by user_id)."""
        token, user = self._login()
        # Create an activity owned by a different user (demo user)
        other = self.env['res.users'].sudo().search([('login', '=', 'demo')], limit=1)
        if not other:
            other = self.env['res.users'].sudo().create({
                'name': 'Other User', 'login': f'other-{secrets.token_hex(4)}',
                'email': 'other@test.local',
            })
        partner = self._make_partner()
        act = self._make_activity(other, partner)

        resp = self._post('/mark-read', token=token, body={
            'kind': 'feed', 'ids': [act.id],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['updated'], 0)

        # confirm no dismissal got written for the calling user
        dismissed = self.env['api.notification.dismissal'].sudo().search([
            ('user_id', '=', user.id),
            ('source_kind', '=', 'activity'),
            ('source_id', '=', act.id),
        ])
        self.assertFalse(dismissed)

    # ===== mark-all-read =====================================================

    def test_mark_all_read_feed(self):
        token, user = self._login()
        partner = self._make_partner()
        a1 = self._make_activity(user, partner, summary='A1')
        a2 = self._make_activity(user, partner, summary='A2')

        resp = self._post('/mark-all-read', token=token, body={'kind': 'feed'})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(resp.json()['data']['updated'], 2)

        # Both should be dismissed now
        resp = self._get('/feed', token=token, params={'filter': 'unread'})
        ids = [i['id'] for i in resp.json()['data']['items']]
        self.assertNotIn(a1.id, ids)
        self.assertNotIn(a2.id, ids)

    def test_mark_all_read_invalid_kind(self):
        token, _ = self._login()
        resp = self._post('/mark-all-read', token=token, body={'kind': 'banana'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error']['code'], 'INVALID_INPUT')

    # ===== star / unstar =====================================================

    def test_star_unstar_idempotent(self):
        token, user = self._login()
        partner = self._make_partner()
        msg = self._post_inbox_message(partner, user)

        # star
        r1 = self._post(f'/{msg.id}/star', token=token).json()
        self.assertTrue(r1['data']['starred'])
        # star again — still starred
        r2 = self._post(f'/{msg.id}/star', token=token).json()
        self.assertTrue(r2['data']['starred'])
        # unstar
        r3 = self._post(f'/{msg.id}/unstar', token=token).json()
        self.assertFalse(r3['data']['starred'])
        # unstar again
        r4 = self._post(f'/{msg.id}/unstar', token=token).json()
        self.assertFalse(r4['data']['starred'])

    def test_star_missing_message(self):
        token, _ = self._login()
        resp = self._post('/999999999/star', token=token)
        # Either 404 NOT_FOUND or access-denied 404 — both acceptable
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error']['code'], 'NOT_FOUND')


@tagged('post_install', '-at_install')
class TestApiNotificationDismissalModel(HttpCase):
    """Direct unit tests on the dismissal helper methods."""

    def test_dismiss_many_inserts_only_new(self):
        user = self.env.ref('base.user_admin')
        Dismissal = self.env['api.notification.dismissal']

        first = Dismissal.dismiss_many(user.id, 'activity', [1, 2, 3])
        self.assertEqual(first, 3)
        again = Dismissal.dismiss_many(user.id, 'activity', [1, 2, 3])
        self.assertEqual(again, 0)
        mixed = Dismissal.dismiss_many(user.id, 'activity', [3, 4, 5])
        self.assertEqual(mixed, 2)

    def test_dismissed_ids_for_returns_set(self):
        user = self.env.ref('base.user_admin')
        Dismissal = self.env['api.notification.dismissal']
        Dismissal.dismiss_many(user.id, 'activity', [10, 20, 30])

        out = Dismissal.dismissed_ids_for(user.id, 'activity', [10, 30, 99])
        self.assertEqual(out, {10, 30})

        empty = Dismissal.dismissed_ids_for(user.id, 'activity', [])
        self.assertEqual(empty, set())
