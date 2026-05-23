{
    "name": "Toomde — OHADA / SYSCOHADA compliance overlay",
    "version": "19.0.1.2.0",
    "category": "Accounting/Localizations",
    "summary": "Enforces SYSCOHADA mandatory controls on top of Odoo's per-country l10n_<iso2> modules.",
    "description": """
Adds the controls SYSCOHADA / OHADA require but Odoo upstream does not
enable by default:

* sequential, gap-less customer invoice numbering (anti-fraud)
* hash-chained immutable posted entries on customer/vendor/misc journals
* French locale defaults on the admin partner + company
* period locking surfaced server-side
* customer-facing TVA marked tax-included so storefront prices are TTC end-to-end

Installed automatically on tenants whose `res.company.country_id` is in
the OHADA zone (SN, CI, BF, ML, NE, TG, BJ, GW, CM, GA, CD).

See `tax.md` Phase 7 (COMP-4 / COMP-5 / COMP-6 / COMP-7).
""",
    "author": "Toomde",
    "license": "LGPL-3",
    "depends": [
        "account",
        "l10n_syscohada",
    ],
    "data": [
        "data/account_report_bilan.xml",
        "data/account_report_resultat.xml",
        "data/account_report_tafire.xml",
        "data/account_report_notes.xml",
        "reports/withholding_certificate.xml",
        "reports/invoice_qr.xml",
        "views/annual_export_wizard.xml",
    ],
    "post_init_hook": "_post_init_ohada_overlay",
    "installable": True,
    "auto_install": False,
}
