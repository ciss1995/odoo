# -*- coding: utf-8 -*-
"""Phone + PIN identity for the WhatsApp entry point (Phase 2).

WhatsApp hands us a *verified* phone number (the sender ``wa_id``) for free, but
a phone number alone can't be trusted to mutate money or stock — SIM-swap and
shared-handset realities in our market mean we still gate sensitive actions
behind a short PIN. This model maps an E.164 phone to exactly one Odoo user and
holds the PIN (hashed, never plaintext) plus the brute-force lockout state.

The PIN is hashed with passlib's pbkdf2_sha256 (salt embedded in the hash
string, so no separate salt column) — the same family Odoo uses for passwords.

``authenticate_phone`` is the single choke point the ``/api/v2/auth/phone-login``
endpoint calls. It is written to be **anti-enumeration**: an unknown phone and a
wrong PIN take the same code path (a hash verify is always burned) and surface
the same generic failure, so an attacker can't tell "no such number" from "wrong
PIN" by response content or timing.
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

try:
    from passlib.hash import pbkdf2_sha256
except ImportError:  # pragma: no cover — passlib is an Odoo dependency
    pbkdf2_sha256 = None

_logger = logging.getLogger(__name__)

# A real pbkdf2 hash of a value no PIN can equal (contains non-digits, and PINs
# are validated digits-only). Verifying a wrong PIN against THIS on the
# unknown-phone path performs the same KDF work as a real verify, so the
# unknown-phone and wrong-PIN paths are timing-indistinguishable. Computed once
# at import (same cost profile as a stored hash) rather than hand-written, so it
# actually parses and runs the full KDF.
_DUMMY_PIN_HASH = pbkdf2_sha256.hash("~no-such-pin~") if pbkdf2_sha256 else None

# Result codes returned by authenticate_phone (never leaked verbatim to the
# caller for the failure cases — the endpoint maps them to a generic message).
AUTH_OK = "ok"
AUTH_INVALID = "invalid"       # unknown phone OR wrong PIN (indistinguishable)
AUTH_LOCKED = "locked"         # too many recent failures


class ApiPhoneIdentity(models.Model):
    _name = 'api.phone_identity'
    _description = 'Phone + PIN identity (WhatsApp login)'
    _order = 'create_date desc'
    _rec_name = 'phone_e164'

    phone_e164 = fields.Char(
        string='Phone (E.164)', required=True, index=True,
        help="Normalized login phone, e.g. +221771234567. Matched against the "
             "WhatsApp sender number.",
    )
    user_id = fields.Many2one(
        'res.users', string='User', required=True, ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company,
    )
    pin_hash = fields.Char(
        string='PIN Hash', required=True,
        help="pbkdf2_sha256 hash of the PIN (salt embedded). Never the PIN itself.",
        groups='base.group_system',
    )
    wa_id = fields.Char(string='WhatsApp ID', index=True)
    verified_at = fields.Datetime(string='Verified At')
    last_login_at = fields.Datetime(string='Last Login At')
    failed_attempts = fields.Integer(string='Failed Attempts', default=0)
    locked_until = fields.Datetime(string='Locked Until')
    active = fields.Boolean(string='Active', default=True)

    _phone_company_unique = models.Constraint(
        'UNIQUE(phone_e164, company_id)',
        'A phone number can map to only one user per company.',
    )

    # -- lockout policy ----------------------------------------------------
    # Lock after LOCK_THRESHOLD consecutive failures, with exponential backoff
    # so a determined guesser is slowed geometrically (a 4-digit PIN is 10k
    # combinations; 15min→30→60→… caps the throughput to a few tries an hour).
    LOCK_THRESHOLD = 5
    LOCK_BASE_SECONDS = 900          # 15 minutes at the first lock
    LOCK_MAX_SECONDS = 86_400        # cap the backoff at 24h

    # -- PIN policy --------------------------------------------------------
    PIN_MIN_LEN = 4
    PIN_MAX_LEN = 8

    # ---------------------------------------------------------------------
    # PIN hashing
    # ---------------------------------------------------------------------
    @staticmethod
    def _hash_pin(pin):
        """Return a pbkdf2_sha256 hash (with embedded salt) for a raw PIN."""
        return pbkdf2_sha256.hash(pin)

    @api.model
    def _validate_pin_format(self, pin):
        """Return None if the PIN is acceptable, else a human message.

        Kept deliberately simple: numeric, 4–8 digits. We reject obviously
        trivial PINs (all-same / straight sequences) since the keyspace is tiny.
        """
        if not pin or not isinstance(pin, str):
            return "PIN is required"
        if not pin.isdigit():
            return "PIN must be digits only"
        if not (self.PIN_MIN_LEN <= len(pin) <= self.PIN_MAX_LEN):
            return f"PIN must be {self.PIN_MIN_LEN}-{self.PIN_MAX_LEN} digits"
        if len(set(pin)) == 1:
            return "PIN is too weak"
        if pin in ("1234", "12345", "123456", "1234567", "12345678",
                   "0123", "01234", "012345"):
            return "PIN is too weak"
        return None

    def _verify_pin(self, pin):
        """Constant-ish-time verify of a raw PIN against this record's hash."""
        self.ensure_one()
        if not self.pin_hash or not pin:
            return False
        try:
            return pbkdf2_sha256.verify(pin, self.pin_hash)
        except (ValueError, TypeError):
            return False

    # ---------------------------------------------------------------------
    # Lockout state
    # ---------------------------------------------------------------------
    def _is_locked(self):
        self.ensure_one()
        return bool(self.locked_until and self.locked_until > fields.Datetime.now())

    def _register_failure(self):
        """Record a failed attempt and (re)arm the lockout with backoff."""
        self.ensure_one()
        attempts = (self.failed_attempts or 0) + 1
        vals = {'failed_attempts': attempts}
        if attempts >= self.LOCK_THRESHOLD:
            over = attempts - self.LOCK_THRESHOLD          # 0 at the threshold
            seconds = min(self.LOCK_BASE_SECONDS * (2 ** over), self.LOCK_MAX_SECONDS)
            vals['locked_until'] = fields.Datetime.now() + timedelta(seconds=seconds)
        self.sudo().write(vals)

    def _register_success(self):
        """Clear failure/lock state and stamp the login time."""
        self.ensure_one()
        self.sudo().write({
            'failed_attempts': 0,
            'locked_until': False,
            'last_login_at': fields.Datetime.now(),
        })

    def _lock_retry_after(self):
        """Seconds until this identity unlocks (0 if not locked)."""
        self.ensure_one()
        if not self._is_locked():
            return 0
        return int((self.locked_until - fields.Datetime.now()).total_seconds()) + 1

    # ---------------------------------------------------------------------
    # Enrollment
    # ---------------------------------------------------------------------
    @api.model
    def enroll(self, user_id, phone_e164, pin, wa_id=None, company_id=None):
        """Create or update the identity binding for a user + set the PIN.

        Idempotent per (phone, company): re-enrolling the same phone rebinds
        the user and resets the PIN + lockout state. Raises ValueError on a bad
        PIN — the controller maps that to a 400.
        """
        err = self._validate_pin_format(pin)
        if err:
            raise ValueError(err)
        company_id = company_id or self.env.company.id
        identity = self.sudo().search([
            ('phone_e164', '=', phone_e164),
            ('company_id', '=', company_id),
        ], limit=1)
        vals = {
            'user_id': user_id,
            'phone_e164': phone_e164,
            'company_id': company_id,
            'pin_hash': self._hash_pin(pin),
            'verified_at': fields.Datetime.now(),
            'failed_attempts': 0,
            'locked_until': False,
            'active': True,
        }
        if wa_id:
            vals['wa_id'] = wa_id
        if identity:
            identity.write(vals)
        else:
            identity = self.create(vals)
        return identity

    # ---------------------------------------------------------------------
    # Authentication (the single choke point for phone-login)
    # ---------------------------------------------------------------------
    @api.model
    def authenticate_phone(self, phone_e164, pin, company_id=None):
        """Verify a phone+PIN. Returns (result_code, identity_or_None).

        Anti-enumeration: an unknown phone burns a dummy hash verify and returns
        the same ``AUTH_INVALID`` as a wrong PIN, so unknown-vs-wrong-PIN is
        indistinguishable by content or timing. Locked identities short-circuit
        to ``AUTH_LOCKED`` (only reachable for a real phone the caller already
        battered — it does not leak existence to a cold prober).
        """
        domain = [('phone_e164', '=', phone_e164), ('active', '=', True)]
        if company_id:
            domain.append(('company_id', '=', company_id))
        identity = self.sudo().search(domain, limit=1)

        if not identity:
            # Equalize timing with the real path — burn a real KDF verify.
            if _DUMMY_PIN_HASH:
                try:
                    pbkdf2_sha256.verify(pin or '', _DUMMY_PIN_HASH)
                except (ValueError, TypeError):
                    pass
            return AUTH_INVALID, None

        if identity._is_locked():
            return AUTH_LOCKED, identity

        if identity._verify_pin(pin):
            identity._register_success()
            return AUTH_OK, identity

        identity._register_failure()
        # If that failure just tripped the lock, surface it so the caller can
        # send a Retry-After rather than another "wrong PIN".
        if identity._is_locked():
            return AUTH_LOCKED, identity
        return AUTH_INVALID, identity
