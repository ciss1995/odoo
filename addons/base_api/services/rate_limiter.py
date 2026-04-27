# -*- coding: utf-8 -*-
"""In-memory rate limiter for API endpoints.

Provides two rate limiting strategies:
1. Per-IP login throttle — prevents brute-force password attacks.
2. Per-user API throttle — prevents authenticated users from spamming endpoints.

Thread-safe. Stale entries are cleaned up periodically to prevent memory leaks.

For multi-worker / multi-container deployments, replace _storage dicts with
Redis (INCR + EXPIRE) so limits are shared across processes.
"""

import logging
import threading
import time

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Login rate limiter (per IP)
# ---------------------------------------------------------------------------

_login_attempts = {}  # ip -> list of timestamps
_login_lock = threading.Lock()

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300  # 5 minutes
LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes after exceeding limit


def check_login_rate_limit(ip_address):
    """Check if the IP is allowed to attempt a login.

    Returns:
        (allowed: bool, retry_after: int or None)
        If not allowed, retry_after is seconds until the client can try again.
    """
    now = time.time()

    with _login_lock:
        record = _login_attempts.get(ip_address)
        if record is None:
            _login_attempts[ip_address] = [now]
            return True, None

        # Purge attempts outside the window
        record = [t for t in record if now - t < LOGIN_WINDOW_SECONDS]

        # Check if currently locked out (last attempt was a lockout trigger)
        if len(record) >= LOGIN_MAX_ATTEMPTS:
            oldest_over_limit = record[-LOGIN_MAX_ATTEMPTS]
            lockout_expires = oldest_over_limit + LOGIN_LOCKOUT_SECONDS
            if now < lockout_expires:
                retry_after = int(lockout_expires - now) + 1
                _login_attempts[ip_address] = record
                return False, retry_after
            # Lockout expired — reset
            record = []

        record.append(now)
        _login_attempts[ip_address] = record
        return True, None


def record_failed_login(ip_address):
    """Record a failed login attempt (call after credentials are rejected)."""
    # The attempt is already recorded in check_login_rate_limit.
    # This is a hook for future enhancements (e.g., progressive delay).
    pass


# ---------------------------------------------------------------------------
# Per-user API rate limiter
# ---------------------------------------------------------------------------

_api_usage = {}  # user_id -> list of timestamps
_api_lock = threading.Lock()

API_RATE_LIMIT = 120  # max requests per window
API_RATE_WINDOW = 60  # seconds


def check_api_rate_limit(user_id):
    """Check if the authenticated user is within their per-minute rate limit.

    Returns:
        (allowed: bool, retry_after: int or None, remaining: int)
    """
    now = time.time()

    with _api_lock:
        record = _api_usage.get(user_id)
        if record is None:
            _api_usage[user_id] = [now]
            return True, None, API_RATE_LIMIT - 1

        # Purge timestamps outside the window
        record = [t for t in record if now - t < API_RATE_WINDOW]

        if len(record) >= API_RATE_LIMIT:
            oldest = record[0]
            retry_after = int((oldest + API_RATE_WINDOW) - now) + 1
            _api_usage[user_id] = record
            return False, retry_after, 0

        record.append(now)
        _api_usage[user_id] = record
        remaining = API_RATE_LIMIT - len(record)
        return True, None, remaining


# ---------------------------------------------------------------------------
# Periodic cleanup to prevent unbounded memory growth
# ---------------------------------------------------------------------------

_cleanup_started = False
_cleanup_lock = threading.Lock()
CLEANUP_INTERVAL = 600  # every 10 minutes


def _start_cleanup():
    global _cleanup_started
    with _cleanup_lock:
        if _cleanup_started:
            return
        _cleanup_started = True

    def _cleanup_loop():
        while True:
            time.sleep(CLEANUP_INTERVAL)
            now = time.time()
            with _login_lock:
                stale = [
                    ip for ip, attempts in _login_attempts.items()
                    if not attempts or now - attempts[-1] > LOGIN_LOCKOUT_SECONDS
                ]
                for ip in stale:
                    del _login_attempts[ip]

            with _api_lock:
                stale = [
                    uid for uid, attempts in _api_usage.items()
                    if not attempts or now - attempts[-1] > API_RATE_WINDOW * 2
                ]
                for uid in stale:
                    del _api_usage[uid]

    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()


# Start cleanup on module import
_start_cleanup()
