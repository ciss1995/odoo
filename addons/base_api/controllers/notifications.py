# -*- coding: utf-8 -*-
"""Dedicated, ACL-aware notifications endpoints for the SPAs.

Endpoints (all under /api/v2/notifications/*, session-token auth):

  GET  /summary                    -> {needaction_count, feed_unread_count}
  GET  /inbox                      -> mail.message addressed to the user
  GET  /feed                       -> mail.activity assigned to the user
  POST /mark-read                  -> {ids, kind: inbox|feed}
  POST /mark-all-read              -> {kind: inbox|feed|all}
  POST /<id>/star                  -> star a mail.message (idempotent)
  POST /<id>/unstar                -> unstar a mail.message (idempotent)

Read-state for the feed is stored in api.notification.dismissal — a table
this addon owns. We never touch core mail.activity schema. Completing an
activity (action_done) and dismissing the bell entry are independent.

Inbox response items have plain int ids (mail.message.id). Feed items are
also plain ints — disambiguated by `type` on the response and by `kind` on
mark-read requests. The optional `link` field from the original draft was
dropped: backend returns model + res_id + record_name and the SPA owns
route mapping.
"""

import json
import logging

from odoo import http
from odoo.http import request
from odoo.tools import html2plaintext

from .base import BaseApiController


_logger = logging.getLogger(__name__)


PREVIEW_MAX = 300
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_MODEL_FILTER = 20

INBOX_FILTERS = ('all', 'needaction', 'starred')
MARK_READ_KINDS = ('inbox', 'feed')
MARK_ALL_KINDS = ('inbox', 'feed', 'all')


class NotificationsController(BaseApiController):

    # ===== helpers ============================================================

    def _parse_pagination(self):
        """Parse limit/offset from query args. Returns (limit, offset) or
        (None, error_response)."""
        try:
            limit = int(request.httprequest.args.get('limit', DEFAULT_LIMIT))
            offset = int(request.httprequest.args.get('offset', 0))
        except (TypeError, ValueError):
            return None, self._error_response(
                "limit and offset must be integers", 400, "INVALID_INPUT",
            )
        if limit < 1 or offset < 0:
            return None, self._error_response(
                "limit must be >= 1, offset must be >= 0", 400, "INVALID_INPUT",
            )
        limit = min(limit, MAX_LIMIT)
        return (limit, offset), None

    def _parse_model_filter(self):
        """Parse comma-separated `model` query param.

        Returns (list_or_none, error_response). list is None when no filter
        was provided. Validates that each model exists in the registry.
        """
        raw = request.httprequest.args.get('model')
        if not raw:
            return None, None
        names = [n.strip() for n in raw.split(',') if n.strip()]
        # dedupe preserving order
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        if not seen:
            return None, None
        if len(seen) > MAX_MODEL_FILTER:
            return None, self._error_response(
                f"too many models in filter, max {MAX_MODEL_FILTER}",
                400, "INVALID_INPUT",
            )
        unknown = [n for n in seen if n not in request.env]
        if unknown:
            return None, self._error_response(
                f"unknown model(s): {', '.join(unknown)}",
                400, "INVALID_INPUT",
            )
        return seen, None

    def _read_json_body(self):
        """Parse JSON body. Returns (dict, error_response)."""
        try:
            raw = request.httprequest.get_data(as_text=True) or '{}'
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None, self._error_response(
                "Invalid JSON body", 400, "INVALID_INPUT",
            )
        if not isinstance(data, dict):
            return None, self._error_response(
                "Body must be a JSON object", 400, "INVALID_INPUT",
            )
        return data, None

    def _make_preview(self, body_html):
        """Strip HTML and truncate to PREVIEW_MAX chars."""
        if not body_html:
            return ''
        try:
            text = html2plaintext(body_html) or ''
        except Exception:
            text = ''
        text = ' '.join(text.split())  # collapse whitespace
        if len(text) > PREVIEW_MAX:
            text = text[:PREVIEW_MAX].rstrip() + '…'
        return text

    def _iso(self, dt):
        if not dt:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    def _author_payload(self, partner):
        if not partner:
            return None
        return {
            'id': partner.id,
            'name': partner.name or '',
            'avatar_url': f'/api/v2/avatars/res.partner/{partner.id}',
        }

    def _serialize_message(self, msg):
        return {
            'id': msg.id,
            'subject': msg.subject or None,
            'preview': self._make_preview(msg.body),
            'author': self._author_payload(msg.author_id),
            'date': self._iso(msg.date),
            'message_type': msg.message_type,
            'model': msg.model or None,
            'res_id': msg.res_id or None,
            'record_name': msg.record_name or None,
            'starred': bool(msg.starred),
            'needaction': bool(msg.needaction),
        }

    def _serialize_activity(self, act, dismissed_ids):
        return {
            'id': act.id,
            'type': 'activity',
            'title': act.summary or (
                act.activity_type_id.name if act.activity_type_id else 'Activity'
            ),
            'description': self._make_preview(act.note),
            'is_read': act.id in dismissed_ids,
            'date': self._iso(act.date_deadline) or self._iso(act.create_date),
            'model': act.res_model or None,
            'res_id': act.res_id or None,
            'record_name': act.res_name or None,
            'author': self._author_payload(act.create_uid.partner_id) if act.create_uid else None,
        }

    def _auth_and_enforce(self):
        """Run the standard auth + subscription + quota stack.

        Returns (user, None) on success; (None, error_response) on failure.
        """
        ok, user_or_err = self._authenticate_session()
        if not ok:
            return None, user_or_err
        for check in (self._enforce_subscription, self._enforce_api_quota):
            err = check()
            if err is not None:
                return None, err
        return user_or_err, None

    # ===== endpoints ==========================================================

    @http.route(
        '/api/v2/notifications/summary',
        type='http', auth='none', methods=['GET'], csrf=False,
    )
    def summary(self, **_kwargs):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err
        try:
            needaction_count = request.env['mail.message'].search_count(
                [('needaction', '=', True)]
            )
        except Exception as e:
            _logger.warning("needaction count failed: %s", e)
            needaction_count = 0

        try:
            activities = request.env['mail.activity'].search(
                [('user_id', '=', user.id)]
            )
            dismissed = request.env['api.notification.dismissal'].dismissed_ids_for(
                user.id, 'activity', activities.ids,
            )
            feed_unread_count = max(0, len(activities) - len(dismissed))
        except Exception as e:
            _logger.warning("feed unread count failed: %s", e)
            feed_unread_count = 0

        return self._json_response(data={
            'needaction_count': needaction_count,
            'feed_unread_count': feed_unread_count,
        })

    @http.route(
        '/api/v2/notifications/inbox',
        type='http', auth='none', methods=['GET'], csrf=False,
    )
    def inbox(self, **_kwargs):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err

        page, page_err = self._parse_pagination()
        if page_err is not None:
            return page_err
        limit, offset = page

        models_filter, model_err = self._parse_model_filter()
        if model_err is not None:
            return model_err

        filt = request.httprequest.args.get('filter', 'all')
        if filt not in INBOX_FILTERS:
            return self._error_response(
                f"filter must be one of {', '.join(INBOX_FILTERS)}",
                400, "INVALID_INPUT",
            )

        domain = []
        if filt == 'needaction':
            domain.append(('needaction', '=', True))
        elif filt == 'starred':
            domain.append(('starred', '=', True))
        else:
            # 'all' inbox = messages addressed to the user (needaction OR starred)
            domain += ['|', ('needaction', '=', True), ('starred', '=', True)]

        if models_filter:
            domain.append(('model', 'in', models_filter))

        Message = request.env['mail.message']
        try:
            total = Message.search_count(domain)
            records = Message.search(domain, limit=limit, offset=offset, order='date desc, id desc')
        except Exception as e:
            _logger.error("inbox search failed: %s", e)
            return self._error_response(
                "Could not load inbox", 500, "INBOX_ERROR",
            )

        items = [self._serialize_message(m) for m in records]
        return self._json_response(data={
            'items': items,
            'total': total,
            'has_more': (offset + len(items)) < total,
        })

    @http.route(
        '/api/v2/notifications/feed',
        type='http', auth='none', methods=['GET'], csrf=False,
    )
    def feed(self, **_kwargs):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err

        page, page_err = self._parse_pagination()
        if page_err is not None:
            return page_err
        limit, offset = page

        models_filter, model_err = self._parse_model_filter()
        if model_err is not None:
            return model_err

        filt = request.httprequest.args.get('filter', 'unread')
        if filt not in ('all', 'unread'):
            return self._error_response(
                "filter must be 'all' or 'unread'", 400, "INVALID_INPUT",
            )

        domain = [('user_id', '=', user.id)]
        if models_filter:
            domain.append(('res_model', 'in', models_filter))

        Activity = request.env['mail.activity']
        try:
            all_for_user = Activity.search(domain, order='date_deadline asc, id desc')
            dismissed = request.env['api.notification.dismissal'].dismissed_ids_for(
                user.id, 'activity', all_for_user.ids,
            )

            if filt == 'unread':
                visible = all_for_user.filtered(lambda a: a.id not in dismissed)
            else:
                visible = all_for_user

            total = len(visible)
            page_records = visible[offset:offset + limit]
        except Exception as e:
            _logger.error("feed search failed: %s", e)
            return self._error_response(
                "Could not load feed", 500, "FEED_ERROR",
            )

        items = [self._serialize_activity(a, dismissed) for a in page_records]
        return self._json_response(data={
            'items': items,
            'total': total,
            'has_more': (offset + len(items)) < total,
        })

    @http.route(
        '/api/v2/notifications/mark-read',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def mark_read(self, **_kwargs):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err

        body, body_err = self._read_json_body()
        if body_err is not None:
            return body_err

        kind = body.get('kind')
        ids = body.get('ids')

        if kind not in MARK_READ_KINDS:
            return self._error_response(
                f"kind must be one of {', '.join(MARK_READ_KINDS)}",
                400, "INVALID_INPUT",
            )
        if not isinstance(ids, list) or not ids:
            return self._error_response(
                "ids must be a non-empty list of integers", 400, "INVALID_INPUT",
            )
        try:
            ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return self._error_response(
                "ids must be integers", 400, "INVALID_INPUT",
            )

        if kind == 'inbox':
            updated = self._mark_inbox_read(ids)
        else:  # 'feed'
            updated = self._mark_feed_read(user, ids)

        return self._json_response(data={'updated': updated})

    @http.route(
        '/api/v2/notifications/mark-all-read',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def mark_all_read(self, **_kwargs):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err

        body, body_err = self._read_json_body()
        if body_err is not None:
            return body_err

        kind = body.get('kind', 'all')
        if kind not in MARK_ALL_KINDS:
            return self._error_response(
                f"kind must be one of {', '.join(MARK_ALL_KINDS)}",
                400, "INVALID_INPUT",
            )

        updated = 0
        if kind in ('inbox', 'all'):
            inbox_msgs = request.env['mail.message'].search(
                [('needaction', '=', True)]
            )
            updated += self._mark_inbox_read(inbox_msgs.ids)
        if kind in ('feed', 'all'):
            activities = request.env['mail.activity'].search(
                [('user_id', '=', user.id)]
            )
            updated += self._mark_feed_read(user, activities.ids)

        return self._json_response(data={'updated': updated})

    @http.route(
        '/api/v2/notifications/<int:msg_id>/star',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def star(self, msg_id, **_kwargs):
        return self._star_toggle(msg_id, want_starred=True)

    @http.route(
        '/api/v2/notifications/<int:msg_id>/unstar',
        type='http', auth='none', methods=['POST'], csrf=False, readonly=False,
    )
    def unstar(self, msg_id, **_kwargs):
        return self._star_toggle(msg_id, want_starred=False)

    # ===== private write helpers =============================================

    def _mark_inbox_read(self, message_ids):
        """Clear needaction state for the current user on these messages.

        Updates mail.notification rows where the recipient is the calling
        user's partner and is_read is False. Returns the count of rows
        actually flipped.
        """
        if not message_ids:
            return 0
        partner_id = request.env.user.partner_id.id
        if not partner_id:
            return 0
        notifs = request.env['mail.notification'].sudo().search([
            ('mail_message_id', 'in', message_ids),
            ('res_partner_id', '=', partner_id),
            ('is_read', '=', False),
        ])
        if not notifs:
            return 0
        notifs.write({'is_read': True})
        return len(notifs)

    def _mark_feed_read(self, user, activity_ids):
        """Insert dismissal rows for the user's accessible activities."""
        if not activity_ids:
            return 0
        # Re-scope to activities the user actually owns to avoid leaking
        # dismissal of someone else's row through ID guessing.
        own_ids = request.env['mail.activity'].search([
            ('id', 'in', activity_ids),
            ('user_id', '=', user.id),
        ]).ids
        if not own_ids:
            return 0
        return request.env['api.notification.dismissal'].dismiss_many(
            user.id, 'activity', own_ids,
        )

    def _star_toggle(self, msg_id, want_starred):
        user, err = self._auth_and_enforce()
        if err is not None:
            return err
        msg = request.env['mail.message'].browse(msg_id)
        try:
            msg.check_access('read')
        except Exception:
            return self._error_response(
                "Message not found", 404, "NOT_FOUND",
            )
        if not msg.exists():
            return self._error_response(
                "Message not found", 404, "NOT_FOUND",
            )
        currently_starred = bool(msg.starred)
        if currently_starred != want_starred:
            try:
                msg.toggle_message_starred()
            except Exception as e:
                _logger.error("star toggle failed for msg %s: %s", msg_id, e)
                return self._error_response(
                    "Could not toggle star", 500, "STAR_ERROR",
                )
        return self._json_response(data={'starred': want_starred})
