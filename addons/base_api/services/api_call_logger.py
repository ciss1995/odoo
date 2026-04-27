# -*- coding: utf-8 -*-
"""Non-blocking API call logger.

Buffers API call counts in memory and flushes to the Control Plane
in batches every N calls or every M seconds, whichever comes first.

This runs inside each Odoo container. It uses the same env vars as
the subscription enforcer (TENANT_ID, CONTROL_PLANE_URL, CONTROL_PLANE_TOKEN).
"""

import logging
import os
import threading
import time

import requests

_logger = logging.getLogger(__name__)


class ApiCallLogger:
    _instance = None
    _lock = threading.Lock()

    FLUSH_INTERVAL = 30       # seconds between flushes
    FLUSH_THRESHOLD = 50      # flush after this many buffered calls

    def __init__(self, tenant_id, control_plane_url, control_plane_token):
        self.tenant_id = tenant_id
        self.cp_url = control_plane_url.rstrip('/')
        self.cp_token = control_plane_token
        self._buffer = {
            "calls": 0, "read_calls": 0, "write_calls": 0,
            "delete_calls": 0, "failed_calls": 0, "response_ms": 0,
        }
        self._buffer_lock = threading.Lock()
        self._start_flush_timer()

    @classmethod
    def get_instance(cls):
        """Singleton. Returns None if env vars not set."""
        if cls._instance is not None:
            return cls._instance
        tenant_id = os.environ.get('TENANT_ID', '').strip()
        cp_url = os.environ.get('CONTROL_PLANE_URL', '').strip()
        cp_token = os.environ.get('CONTROL_PLANE_TOKEN', '').strip()
        if not tenant_id or not cp_url or not cp_token:
            return None
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(tenant_id, cp_url, cp_token)
        return cls._instance

    def log_call(self, method, status_code, response_time_ms):
        """Record a single API call. Non-blocking."""
        with self._buffer_lock:
            self._buffer["calls"] += 1
            self._buffer["response_ms"] += response_time_ms
            if method in ('GET', 'HEAD', 'OPTIONS'):
                self._buffer["read_calls"] += 1
            elif method == 'DELETE':
                self._buffer["delete_calls"] += 1
            else:
                self._buffer["write_calls"] += 1
            if status_code >= 400:
                self._buffer["failed_calls"] += 1
            if self._buffer["calls"] >= self.FLUSH_THRESHOLD:
                self._flush_async()

    def _flush_async(self):
        """Flush buffer to Control Plane in a background thread."""
        with self._buffer_lock:
            if self._buffer["calls"] == 0:
                return
            batch = self._buffer.copy()
            self._buffer = {
                "calls": 0, "read_calls": 0, "write_calls": 0,
                "delete_calls": 0, "failed_calls": 0, "response_ms": 0,
            }
        threading.Thread(target=self._send, args=(batch,), daemon=True).start()

    def _send(self, batch):
        """Send buffered counts to the Control Plane."""
        try:
            requests.put(
                f"{self.cp_url}/internal/tenants/{self.tenant_id}/usage/increment",
                json=batch,
                headers={"Authorization": f"Bearer {self.cp_token}"},
                timeout=5,
            )
        except Exception as e:
            _logger.warning("Failed to send usage data to Control Plane: %s", e)

    def _start_flush_timer(self):
        """Periodically flush the buffer."""
        def _timer_loop():
            while True:
                time.sleep(self.FLUSH_INTERVAL)
                self._flush_async()
        t = threading.Thread(target=_timer_loop, daemon=True)
        t.start()
