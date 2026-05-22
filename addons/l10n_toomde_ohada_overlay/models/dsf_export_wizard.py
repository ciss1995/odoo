"""Wizard that lets a tenant accountant download the SYSCOHADA filings.

UI surface: a menu under Accounting → SYSCOHADA → "Export annuel". The
user picks the format and year, hits "Générer", gets an XLSX download.

Default `export_kind` is derived from the company country at form open
so a Senegal tenant sees "DSF Sénégal" pre-selected instead of having
to scroll the list.
"""

from __future__ import annotations

import base64
from datetime import date

from odoo import api, fields, models

from .dsf_export import DSF_BY_COUNTRY


class OhadaAnnualExportWizard(models.TransientModel):
    _name = "toomde.ohada.annual.export.wizard"
    _description = "OHADA Annual Export Wizard"

    fiscal_year = fields.Integer(
        required=True,
        default=lambda self: date.today().year - 1,
        help="Calendar year to export. Defaults to last full year.",
    )
    export_kind = fields.Selection(
        [
            # West Africa — UEMOA / SYSCOHADA
            ("dsf_sn", "DSF Sénégal (DGID)"),
            ("dsf_ci", "DSF Côte d'Ivoire (DGI)"),
            ("dsf_bf", "DSF Burkina Faso (DGI)"),
            ("dsf_ml", "DSF Mali (DGI)"),
            ("dsf_ne", "DSF Niger (DGI)"),
            ("dsf_tg", "DSF Togo (OTR)"),
            ("dsf_bj", "DSF Bénin (DGI)"),
            ("dsf_gw", "DSF Guinée-Bissau (DGCI)"),
            # Central Africa — CEMAC / SYSCOHADA + RDC
            ("dsf_cm", "DSF Cameroun (DGI)"),
            ("dsf_ga", "DSF Gabon (DGI)"),
            ("dsf_cd", "DSF RD Congo (DGI)"),
            # OHADA-wide fallback
            ("form_1004", "Formulaire OHADA 1004"),
        ],
        required=True,
        default=lambda self: self._default_export_kind(),
    )
    output_file = fields.Binary(string="Fichier généré", readonly=True)
    output_filename = fields.Char(readonly=True)

    @api.model
    def _default_export_kind(self):
        """Pre-select the country DSF that matches the company country.

        Falls back to OHADA Form 1004 for countries we don't have a
        country-specific DSF for yet — gives the user something useful
        on every OHADA tenant out of the box.
        """
        iso2 = self.env.company.country_id.code if self.env.company.country_id else ""
        if iso2 in DSF_BY_COUNTRY:
            return f"dsf_{iso2.lower()}"
        return "form_1004"

    def action_generate(self):
        self.ensure_one()
        company = self.env.company
        if self.export_kind == "form_1004":
            content = self.env["toomde.ohada.form1004"].xlsx(company, self.fiscal_year)
            filename = f"OHADA_1004_{company.name}_{self.fiscal_year}.xlsx"
        else:
            # export_kind is "dsf_<iso2>" — map back to the model name.
            iso2 = self.export_kind.removeprefix("dsf_").upper()
            model_name = DSF_BY_COUNTRY[iso2]
            content = self.env[model_name].xlsx(company, self.fiscal_year)
            filename = f"DSF_{iso2}_{company.name}_{self.fiscal_year}.xlsx"
        self.write({
            "output_file": base64.b64encode(content),
            "output_filename": filename,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
