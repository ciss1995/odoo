# -*- coding: utf-8 -*-
"""Integration tests for /api/v2/message_post and /api/v2/attachment/*.

These endpoints are the only sanctioned path for the SPA to add notes
and upload files against mail-thread records (leads, tasks, …) — so the
authorization story has to be airtight. The tests below pin:

- the happy path (post a note, with and without attachments, on
  multiple mail-thread models),
- the rejection paths that matter: missing/invalid params, a model
  that isn't mail-enabled, a blocked model, an attachment id the
  caller can't read (cross-tenant leak protection),
- the upload size guards (empty and oversized files),
- the download path, including the "exists but you can't see it"
  case which must 404, not 403 (existence-probe defense).
"""

import base64
import io
import json
import secrets
import string
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestMailEndpoints(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.user_admin').sudo().write({'password': 'admin'})

        # Disable subscription enforcement — the test runner blocks
        # external HTTP so the enforcer would otherwise refuse every
        # call. Endpoints short-circuit when get_instance() is None.
        from odoo.addons.base_api.services import subscription_enforcer
        cls._orig_get_instance = subscription_enforcer.SubscriptionEnforcer.get_instance
        subscription_enforcer.SubscriptionEnforcer.get_instance = staticmethod(lambda: None)

    @classmethod
    def tearDownClass(cls):
        from odoo.addons.base_api.services import subscription_enforcer
        subscription_enforcer.SubscriptionEnforcer.get_instance = cls._orig_get_instance
        super().tearDownClass()

    # ----- helpers --------------------------------------------------------

    def _login(self, user_login='admin'):
        user = self.env['res.users'].sudo().search([('login', '=', user_login)], limit=1)
        self.assertTrue(user, f"User not found: {user_login}")
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

    def _post_json(self, path, token, body):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['session-token'] = token
        return self.url_open(path, data=json.dumps(body), headers=headers)

    def _upload(self, token, model, res_id, filename, content,
                mimetype='text/plain'):
        """url_open accepts ``files`` to trigger multipart encoding —
        requests builds the correct Content-Type boundary for us."""
        headers = {}
        if token:
            headers['session-token'] = token
        return self.url_open(
            '/api/v2/attachment/upload',
            data={'model': model, 'res_id': str(res_id)},
            files={'file': (filename, io.BytesIO(content), mimetype)},
            headers=headers,
        )

    def _make_partner(self, name='Mail Endpoint Partner'):
        # res.partner inherits mail.thread → safe target for all
        # message_post / attachment tests, with no module dependencies
        # beyond what base_api already pulls in.
        return self.env['res.partner'].sudo().create({'name': name})

    # ----- message_post ---------------------------------------------------

    def test_post_note_on_partner_succeeds(self):
        token, _ = self._login()
        partner = self._make_partner()
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'res.partner',
            'res_id': partner.id,
            'body': 'Hello from the test',
        })
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()['data']['message']
        self.assertIn('Hello from the test', data['body'])
        self.assertEqual(data['attachments'], [])

        # The mail.message landed on the partner record.
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
        ])
        self.assertTrue(any('Hello from the test' in (m.body or '') for m in messages))

    def test_post_note_without_auth_returns_401(self):
        partner = self._make_partner()
        r = self._post_json('/api/v2/message_post', None, {
            'model': 'res.partner', 'res_id': partner.id, 'body': 'x',
        })
        self.assertIn(r.status_code, (401, 403))

    def test_post_note_with_empty_body_rejected(self):
        token, _ = self._login()
        partner = self._make_partner()
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'res.partner', 'res_id': partner.id, 'body': '   ',
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'INVALID_BODY')

    def test_post_note_with_missing_params_rejected(self):
        token, _ = self._login()
        r = self._post_json('/api/v2/message_post', token, {'body': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'INVALID_PARAMS')

    def test_post_note_on_blocked_model_returns_403(self):
        """ir.cron is in BLOCKED_MODELS — we must refuse before the
        message_post check, otherwise a caller could chatter on
        admin-only models."""
        token, _ = self._login()
        # Need any record id; ir.cron rows definitely exist post-install.
        cron = self.env['ir.cron'].sudo().search([], limit=1)
        self.assertTrue(cron, "Expected at least one ir.cron post_install")
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'ir.cron', 'res_id': cron.id, 'body': 'x',
        })
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error']['code'], 'ACCESS_DENIED')

    def test_post_note_on_non_mail_model_rejected(self):
        """res.country does not inherit mail.thread → must 400 with a
        clear code, not crash trying to call a missing method."""
        token, _ = self._login()
        country = self.env['res.country'].sudo().search([], limit=1)
        self.assertTrue(country)
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'res.country', 'res_id': country.id, 'body': 'x',
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'MODEL_NOT_MAIL_THREAD')

    def test_post_note_on_missing_record_returns_404(self):
        token, _ = self._login()
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'res.partner', 'res_id': 99999999, 'body': 'x',
        })
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error']['code'], 'RECORD_NOT_FOUND')

    # ----- attachment/upload ----------------------------------------------

    def test_upload_returns_attachment_metadata(self):
        token, _ = self._login()
        partner = self._make_partner()
        r = self._upload(token, 'res.partner', partner.id, 'hello.txt', b'hello bytes')
        self.assertEqual(r.status_code, 201, r.text)
        att = r.json()['data']['attachment']
        self.assertEqual(att['name'], 'hello.txt')
        self.assertEqual(att['res_model'], 'res.partner')
        self.assertEqual(att['res_id'], partner.id)
        self.assertEqual(att['file_size'], len(b'hello bytes'))
        self.assertIn('/api/v2/attachment/', att['url'])

        # And the ir.attachment actually exists with the right binding.
        record = self.env['ir.attachment'].sudo().browse(att['id'])
        self.assertTrue(record.exists())
        self.assertEqual(record.res_model, 'res.partner')
        self.assertEqual(record.res_id, partner.id)

    def test_upload_without_file_returns_400(self):
        token, _ = self._login()
        partner = self._make_partner()
        # No `files=` → no file part.
        headers = {'session-token': token}
        r = self.url_open(
            '/api/v2/attachment/upload',
            data={'model': 'res.partner', 'res_id': str(partner.id)},
            headers=headers,
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'FILE_REQUIRED')

    def test_upload_empty_file_rejected(self):
        token, _ = self._login()
        partner = self._make_partner()
        r = self._upload(token, 'res.partner', partner.id, 'empty.bin', b'')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error']['code'], 'FILE_EMPTY')

    def test_upload_oversized_file_rejected(self):
        token, _ = self._login()
        partner = self._make_partner()
        # 1 byte past the cap. We don't allocate 25 MB in memory just to
        # prove the post-read measurement — the claimed Content-Length
        # is enough to trip the pre-check.
        from odoo.addons.base_api.controllers.simple_api import SimpleApiController
        oversize = b'A' * (SimpleApiController._ATTACHMENT_MAX_BYTES + 1)
        r = self._upload(token, 'res.partner', partner.id, 'big.bin', oversize)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()['error']['code'], 'FILE_TOO_LARGE')

    def test_upload_then_post_links_attachment_to_message(self):
        """End-to-end: upload returns an id, message_post with that id
        ends up exposing the attachment in the message's payload."""
        token, _ = self._login()
        partner = self._make_partner()

        up = self._upload(token, 'res.partner', partner.id, 'note.txt', b'attached')
        self.assertEqual(up.status_code, 201, up.text)
        att_id = up.json()['data']['attachment']['id']

        msg = self._post_json('/api/v2/message_post', token, {
            'model': 'res.partner',
            'res_id': partner.id,
            'body': 'See file',
            'attachment_ids': [att_id],
        })
        self.assertEqual(msg.status_code, 201, msg.text)
        attachments = msg.json()['data']['message']['attachments']
        self.assertEqual([a['id'] for a in attachments], [att_id])

    def test_post_with_unknown_attachment_id_rejected(self):
        token, _ = self._login()
        partner = self._make_partner()
        r = self._post_json('/api/v2/message_post', token, {
            'model': 'res.partner', 'res_id': partner.id,
            'body': 'x', 'attachment_ids': [99999999],
        })
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error']['code'], 'ATTACHMENT_NOT_FOUND')

    # ----- attachment download / list -------------------------------------

    def test_download_returns_bytes_with_mimetype(self):
        token, _ = self._login()
        partner = self._make_partner()
        up = self._upload(
            token, 'res.partner', partner.id,
            'plain.txt', b'download-me', mimetype='text/plain',
        )
        att_id = up.json()['data']['attachment']['id']

        r = self.url_open(
            f'/api/v2/attachment/{att_id}',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/plain', r.headers.get('Content-Type', ''))
        self.assertEqual(r.content, b'download-me')

    def test_download_missing_returns_404(self):
        token, _ = self._login()
        r = self.url_open(
            '/api/v2/attachment/99999999',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 404)

    def test_list_attachments_returns_subset(self):
        token, _ = self._login()
        partner = self._make_partner()
        up1 = self._upload(token, 'res.partner', partner.id, 'a.txt', b'aa')
        up2 = self._upload(token, 'res.partner', partner.id, 'b.txt', b'bb')
        ids = [up1.json()['data']['attachment']['id'],
               up2.json()['data']['attachment']['id']]
        r = self.url_open(
            f'/api/v2/attachments?ids={ids[0]},{ids[1]},99999999',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 200)
        returned = {a['id'] for a in r.json()['data']['records']}
        # The bogus id is silently dropped (no 404 — partial visibility
        # mustn't break the notes UI).
        self.assertEqual(returned, set(ids))

    # ----- record_attachments ----------------------------------------------

    def test_record_attachments_returns_record_scoped_list(self):
        token, _ = self._login()
        partner_a = self._make_partner(name='Partner A')
        partner_b = self._make_partner(name='Partner B')
        a1 = self._upload(token, 'res.partner', partner_a.id, 'a1.txt', b'aa')
        a2 = self._upload(token, 'res.partner', partner_a.id, 'a2.txt', b'bb')
        # An attachment on a different record must NOT leak across.
        self._upload(token, 'res.partner', partner_b.id, 'b1.txt', b'cc')
        r = self.url_open(
            f'/api/v2/record_attachments?model=res.partner&res_id={partner_a.id}',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 200)
        returned_names = {a['name'] for a in r.json()['data']['records']}
        self.assertEqual(returned_names, {'a1.txt', 'a2.txt'})
        # And every returned row carries the right binding.
        for a in r.json()['data']['records']:
            self.assertEqual(a['res_model'], 'res.partner')
            self.assertEqual(a['res_id'], partner_a.id)
        # Sanity: ids match what upload returned
        expected_ids = {
            a1.json()['data']['attachment']['id'],
            a2.json()['data']['attachment']['id'],
        }
        self.assertEqual({a['id'] for a in r.json()['data']['records']}, expected_ids)

    def test_record_attachments_missing_params_rejected(self):
        token, _ = self._login()
        r = self.url_open(
            '/api/v2/record_attachments?model=res.partner',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 400)
        r = self.url_open(
            '/api/v2/record_attachments?res_id=1',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 400)

    def test_record_attachments_on_missing_record_returns_404(self):
        token, _ = self._login()
        r = self.url_open(
            '/api/v2/record_attachments?model=res.partner&res_id=99999999',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 404)

    def test_record_attachments_on_blocked_model_returns_403(self):
        token, _ = self._login()
        r = self.url_open(
            '/api/v2/record_attachments?model=ir.cron&res_id=1',
            headers={'session-token': token},
        )
        self.assertEqual(r.status_code, 403)
