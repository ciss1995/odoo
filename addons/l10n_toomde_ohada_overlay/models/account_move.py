"""E-invoicing QR-code support on `account.move` (AFR-2 / AFR-3).

Countries with an active or piloted e-invoicing mandate where this
overlay renders an automatic QR code on every posted customer invoice:

* **CI** — Côte d'Ivoire DGI "facture normalisée" + sticker DGI. Live
  mandate; the QR + a physical sticker are required on invoices above
  the threshold (currently 1M XOF cumulative annual sales).
* **SN** — Sénégal DGID e-facturation. Active pilot rolling out
  through 2026; the QR format is published, the verification API is
  still being staged.
* **CM** — Cameroun DGI "vignette fiscale numérique". Mandated since
  2023 for taxpayers in the "Régime Réel" — invoices carry a digital
  stamp (number + QR) cross-checked against DGI's verification portal.

Countries deliberately NOT in this set (BF, ML, NE, TG, BJ, GW, GA,
CD) don't yet mandate e-invoicing. Rendering a QR on their invoices
would be misleading — there's no authority to verify it against. When
their DGIs publish a spec, add them here.

Until each authority publishes its signed-payload API, this v1:

* generates a QR with a deterministic payload (NIF + invoice ref +
  amount + date),
* exposes it as a binary field for the QWeb invoice template to embed,
* falls back to a plain-text payload box if the `qrcode` Python lib
  is unavailable.

When CI / SN / CM expose certification endpoints, swap
`_toomde_einv_payload` for the certified payload format and have
`_toomde_einv_qr` call the authority's signing API.
"""

from __future__ import annotations

import base64
import io

from odoo import api, fields, models


_EINV_COUNTRIES = frozenset({"CI", "SN", "CM"})


class AccountMove(models.Model):
    _inherit = "account.move"

    toomde_einv_required = fields.Boolean(
        compute="_compute_toomde_einv_required",
        help="True when the company country requires e-invoicing (CI / SN).",
    )

    toomde_einv_qr = fields.Binary(
        compute="_compute_toomde_einv_qr",
        attachment=False,
        help="PNG of the e-invoicing QR code; empty when not applicable.",
    )

    @api.depends("company_id.country_id.code", "move_type", "state")
    def _compute_toomde_einv_required(self):
        for move in self:
            country = move.company_id.country_id.code if move.company_id else None
            move.toomde_einv_required = (
                country in _EINV_COUNTRIES
                and move.move_type in ("out_invoice", "out_refund")
                and move.state == "posted"
            )

    @api.depends("toomde_einv_required", "name", "amount_total_signed",
                 "invoice_date", "company_id.vat", "partner_id.vat")
    def _compute_toomde_einv_qr(self):
        for move in self:
            if not move.toomde_einv_required:
                move.toomde_einv_qr = False
                continue
            move.toomde_einv_qr = self._toomde_render_qr(self._toomde_einv_payload(move))

    @staticmethod
    def _toomde_einv_payload(move) -> str:
        """Compact textual payload encoded in the QR. Format chosen to be
        readable by a human inspector even without the certification
        endpoint — pipe-separated `key=value`, ASCII-only."""
        parts = [
            f"v=1",  # bump when authorities publish their format
            f"co={move.company_id.country_id.code or ''}",
            f"nif_iss={move.company_id.vat or ''}",
            f"nif_cli={move.partner_id.vat or ''}",
            f"ref={move.name or ''}",
            f"date={move.invoice_date.isoformat() if move.invoice_date else ''}",
            f"amt={move.amount_total_signed:.2f}",
            f"cur={move.currency_id.name or ''}",
        ]
        return "|".join(parts)

    @staticmethod
    def _toomde_render_qr(payload: str) -> bytes | bool:
        """Render a QR-code PNG and return its base64-encoded bytes.

        Uses `qrcode` from the Python stdlib-extras Odoo ships with by
        default. Returns False when the library is unavailable — the
        template falls back to a plain-text payload box."""
        try:
            import qrcode  # type: ignore
        except ImportError:
            return False
        img = qrcode.make(payload, box_size=4, border=1)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue())
