# -*- coding: utf-8 -*-
"""Best-effort push of phone enrollments to the control-plane directory.

The authoritative phone identity lives in this tenant's own Odoo
(``api.phone_identity``). The control plane keeps a cross-tenant *index*
(phone → tenant) so the shared ai-agent can resolve an inbound WhatsApp number
to the owning tenant without fanning out to every tenant on the hot path.

This module pushes an enrollment to that index. It is **best-effort and
non-fatal**: a failed push never fails the enroll (the identity still works for
login), but it IS logged at ERROR so a persistently-broken index is alertable
rather than silently stale. Uses the same env contract as the subscription
enforcer: ``TENANT_ID`` / ``CONTROL_PLANE_URL`` / ``CONTROL_PLANE_TOKEN``.
"""

import logging
import os

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_logger = logging.getLogger(__name__)


def _cp_env():
    tenant_id = os.environ.get('TENANT_ID', '').strip()
    cp_url = os.environ.get('CONTROL_PLANE_URL', '').strip()
    cp_token = os.environ.get('CONTROL_PLANE_TOKEN', '').strip()
    if not tenant_id or not cp_url or not cp_token:
        return None
    return tenant_id, cp_url.rstrip('/'), cp_token


def push_enrollment(phone_e164, user_id, wa_id=None):
    """Upsert this tenant's (phone → user) mapping in the CP directory.

    Returns True on success, False on any failure (missing config, network,
    non-2xx). Never raises — callers treat the directory as a cache.
    """
    if requests is None:
        return False
    env = _cp_env()
    if env is None:
        # No control plane configured (e.g. standalone dev tenant) — skip quietly.
        return False
    tenant_id, cp_url, cp_token = env
    url = f"{cp_url}/internal/tenants/{tenant_id}/phone-directory"
    payload = {'phone_e164': phone_e164, 'user_id': user_id}
    if wa_id:
        payload['wa_id'] = wa_id
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {cp_token}"}, timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — index push must not break enroll
        _logger.error(
            "Phone-directory push failed for %s (tenant=%s): %s — login still "
            "works locally, but the WhatsApp resolver won't see this number "
            "until re-pushed.",
            phone_e164, tenant_id, exc,
        )
        return False


def remove_enrollment(phone_e164):
    """Best-effort removal of this tenant's binding for a phone (on unenroll)."""
    if requests is None:
        return False
    env = _cp_env()
    if env is None:
        return False
    tenant_id, cp_url, cp_token = env
    url = f"{cp_url}/internal/tenants/{tenant_id}/phone-directory"
    try:
        resp = requests.delete(
            url, params={'phone_e164': phone_e164},
            headers={"Authorization": f"Bearer {cp_token}"}, timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "Phone-directory removal failed for %s (tenant=%s): %s",
            phone_e164, tenant_id, exc,
        )
        return False
