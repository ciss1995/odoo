"""Tests for the OHADA overlay post-install controls.

Wired into the base-api-tests workflow via
`/l10n_toomde_ohada_overlay:TestOhadaOverlay`.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "ohada_overlay")
class TestOhadaOverlayPostInit(TransactionCase):
    """Verify post-install hook applied the mandatory SYSCOHADA controls."""

    def test_customer_invoice_sequences_are_no_gap(self):
        """COMP-4: customer-invoice sequences must be implementation='no_gap'.

        Without `no_gap`, deleting a draft invoice burns a number and the
        sequence has a hole — violates SYSCOHADA anti-fraud rules.
        """
        sale_journals = self.env["account.journal"].search([("type", "=", "sale")])
        self.assertTrue(
            sale_journals,
            "expected at least one sale journal to be configured after install",
        )
        for journal in sale_journals:
            if journal.sequence_id:
                self.assertEqual(
                    journal.sequence_id.implementation,
                    "no_gap",
                    f"sale journal {journal.name} sequence is not no_gap "
                    f"(got {journal.sequence_id.implementation!r}) — "
                    f"SYSCOHADA requires gap-less invoice numbering",
                )

    def test_critical_journals_have_hash_chain_enabled(self):
        """COMP-7: sale/purchase/general journals must hash-chain posted entries."""
        target_types = ("sale", "purchase", "general")
        journals = self.env["account.journal"].search([("type", "in", target_types)])
        self.assertTrue(
            journals,
            "expected sale/purchase/general journals to exist after install",
        )
        for journal in journals:
            if "restrict_mode_hash_table" in journal._fields:
                self.assertTrue(
                    journal.restrict_mode_hash_table,
                    f"journal {journal.name} ({journal.type}) is not hash-locked "
                    f"— SYSCOHADA requires immutable posted entries",
                )

    def test_fr_FR_language_is_active(self):
        """COMP-6: French must be active so SYSCOHADA labels match the UI."""
        fr = (
            self.env["res.lang"]
            .with_context(active_test=False)
            .search([("code", "=", "fr_FR")], limit=1)
        )
        self.assertTrue(fr, "fr_FR language record must exist after install")
        self.assertTrue(fr.active, "fr_FR must be active after OHADA overlay install")

    def test_multi_currency_enabled_for_internal_users(self):
        """AFR-5: every internal user must be in `base.group_multi_currency`
        so the UI exposes the foreign-currency column on quotes / invoices /
        payments. Without it, XOF→EUR FX gain/loss never gets posted —
        a SYSCOHADA violation for tenants trading outside UEMOA."""
        group_mc = self.env.ref("base.group_multi_currency", raise_if_not_found=False)
        self.assertTrue(group_mc, "base.group_multi_currency missing")
        internal_users = self.env["res.users"].search([("share", "=", False)])
        for user in internal_users:
            self.assertIn(
                group_mc,
                user.groups_id,
                f"internal user {user.login} is missing multi-currency group",
            )

    def test_journal_code_helper_truncates_and_upper(self):
        """AFR-1: journal codes must fit Odoo's 5-char limit + be uppercase
        alphanumeric. Tested as a pure unit so we don't need a multi-company
        SN setup to exercise the seeding logic."""
        from odoo.addons.l10n_toomde_ohada_overlay.hooks import _journal_code

        self.assertEqual(_journal_code("Orange Money"), "ORANG")
        self.assertEqual(_journal_code("Wave"), "WAVE")
        self.assertEqual(_journal_code("MTN MoMo"), "MTNMO")
        self.assertEqual(_journal_code("M-Pesa"), "MPESA")
        # Fallback when input has no alphanumerics — defends against future
        # entries with only punctuation.
        self.assertEqual(_journal_code("---"), "MMNY")


@tagged("post_install", "-at_install", "ohada_overlay")
class TestEInvoicingQR(TransactionCase):
    """AFR-2 / AFR-3: e-invoicing QR rendered only for CI / SN posted
    customer invoices. Other countries / draft / vendor bills must opt
    out so we don't slap an "official QR" on documents that shouldn't
    have one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AccountMove = cls.env["account.move"]

    def _make_partner(self):
        return self.env["res.partner"].create({"name": "Client Test", "vat": "SN1234"})

    def test_einv_required_true_for_sn_posted_customer_invoice(self):
        company = self.env.company
        company.country_id = self.env.ref("base.sn")
        invoice = self.AccountMove.create({
            "move_type": "out_invoice",
            "partner_id": self._make_partner().id,
            "company_id": company.id,
        })
        # Posted invoice in SN → required.
        invoice.state = "posted"
        invoice.invalidate_recordset(["toomde_einv_required"])
        self.assertTrue(invoice.toomde_einv_required)

    def test_einv_required_false_for_us_company(self):
        company = self.env.company
        company.country_id = self.env.ref("base.us")
        invoice = self.AccountMove.create({
            "move_type": "out_invoice",
            "partner_id": self._make_partner().id,
            "company_id": company.id,
        })
        invoice.state = "posted"
        invoice.invalidate_recordset(["toomde_einv_required"])
        self.assertFalse(invoice.toomde_einv_required)

    def test_einv_required_false_for_draft(self):
        company = self.env.company
        company.country_id = self.env.ref("base.sn")
        invoice = self.AccountMove.create({
            "move_type": "out_invoice",
            "partner_id": self._make_partner().id,
            "company_id": company.id,
        })
        # Stays in draft → must NOT render the QR (premature; invoice
        # could be edited before posting).
        self.assertEqual(invoice.state, "draft")
        self.assertFalse(invoice.toomde_einv_required)

    def test_einv_payload_format_is_deterministic(self):
        """Payload format must be stable — downstream auditors will rely
        on it. Test ensures the contract."""
        from odoo.addons.l10n_toomde_ohada_overlay.models.account_move import AccountMove

        # Use a mock-like object — AccountMove._toomde_einv_payload is a
        # staticmethod that reads attributes off `move`.
        class _M:
            class _co:
                class country_id:
                    code = "SN"
                vat = "SN999"
            company_id = _co()
            class _p:
                vat = "SN111"
            partner_id = _p()
            name = "INV/2026/0001"
            class _d:
                @staticmethod
                def isoformat():
                    return "2026-05-22"
            invoice_date = _d()
            amount_total_signed = 100000.0
            class _c:
                name = "XOF"
            currency_id = _c()
        payload = AccountMove._toomde_einv_payload(_M())
        # Stable key order; pipe-separated; ASCII-only.
        self.assertIn("co=SN", payload)
        self.assertIn("ref=INV/2026/0001", payload)
        self.assertIn("amt=100000.00", payload)
        self.assertIn("cur=XOF", payload)
        self.assertEqual(payload.count("|"), 7)  # 8 fields → 7 separators


@tagged("post_install", "-at_install", "ohada_overlay")
class TestAnnualExports(TransactionCase):
    """COMP-8 / COMP-9 / COMP-10: SYSCOHADA financial statements + DSF +
    Form 1004 produce XLSX content (bytes) without raising."""

    def test_class_totals_returns_eight_keys(self):
        """Defensive: SYSCOHADA chart has 8 classes — the roll-up must
        always cover Class 1 through Class 8 so downstream consumers
        don't need to defend against missing keys."""
        totals = self.env["toomde.ohada.annual.export.base"]._class_totals(
            self.env.company, 2025,
        )
        self.assertEqual(set(totals.keys()), {"1", "2", "3", "4", "5", "6", "7", "8"})
        for value in totals.values():
            self.assertIsInstance(value, float)

    def test_dsf_sn_xlsx_is_valid_zip(self):
        """XLSX is a ZIP — quick byte-sanity check that the export
        actually produced a real workbook (and didn't silently emit an
        empty string on a missing dependency)."""
        content = self.env["toomde.ohada.dsf.sn"].xlsx(self.env.company, 2025)
        self.assertIsInstance(content, bytes)
        # ZIP local file header magic: PK\x03\x04
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_form1004_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.form1004"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_wizard_generates_file(self):
        wizard = self.env["toomde.ohada.annual.export.wizard"].create({
            "fiscal_year": 2025,
            "export_kind": "dsf_sn",
        })
        wizard.action_generate()
        self.assertTrue(wizard.output_file)
        self.assertTrue(wizard.output_filename.endswith(".xlsx"))

    def test_bilan_report_definition_exists(self):
        """COMP-8: the Bilan account.report record must exist after install
        so the SPA can route /accounting/reports/bilan to it."""
        bilan = self.env.ref(
            "l10n_toomde_ohada_overlay.syscohada_bilan_report",
            raise_if_not_found=False,
        )
        self.assertTrue(bilan, "Bilan SYSCOHADA report record must exist")
        self.assertEqual(bilan.name, "Bilan SYSCOHADA")

    def test_resultat_report_definition_exists(self):
        resultat = self.env.ref(
            "l10n_toomde_ohada_overlay.syscohada_resultat_report",
            raise_if_not_found=False,
        )
        self.assertTrue(resultat)
        self.assertIn("Compte de résultat SYSCOHADA", resultat.name)

    def test_tafire_report_definition_exists(self):
        """COMP-8: TAFIRE — mandatory third statement of Système Normal."""
        tafire = self.env.ref(
            "l10n_toomde_ohada_overlay.syscohada_tafire_report",
            raise_if_not_found=False,
        )
        self.assertTrue(tafire, "TAFIRE report record must exist")
        self.assertIn("TAFIRE", tafire.name)

    def test_notes_annexes_report_definition_exists(self):
        """COMP-8: notes annexes — the 35-note structural backbone."""
        notes = self.env.ref(
            "l10n_toomde_ohada_overlay.syscohada_notes_report",
            raise_if_not_found=False,
        )
        self.assertTrue(notes, "Notes annexes report record must exist")
        self.assertIn("Notes annexes", notes.name)

    def test_bilan_has_audcif_line_codes(self):
        """The Bilan must carry AUDCIF letter codes (AD, AE, ... DR) so
        downstream consumers (DSF, Form 1004) can reference lines by the
        official code, not by Toomde's free-form names."""
        bilan = self.env.ref("l10n_toomde_ohada_overlay.syscohada_bilan_report")
        codes = {line.code for line in bilan.line_ids if line.code}
        # Spot-check the headline AUDCIF codes
        self.assertIn("SC_BIL_AD", codes)  # Charges immobilisées
        self.assertIn("SC_BIL_BG", codes)  # Créances clients
        self.assertIn("SC_BIL_CA", codes)  # Capital social
        self.assertIn("SC_BIL_DH", codes)  # Fournisseurs

    def test_per_country_dsf_models_exist(self):
        """COMP-9: every OHADA country we claim DSF support for must
        have an installed Odoo model. The wizard's dropdown is derived
        from DSF_BY_COUNTRY — if a model in that map doesn't exist,
        the user picks a DSF and gets a KeyError at generation time."""
        from odoo.addons.l10n_toomde_ohada_overlay.models.dsf_export import DSF_BY_COUNTRY

        for iso2, model_name in DSF_BY_COUNTRY.items():
            self.assertIn(
                model_name,
                self.env,
                f"DSF model {model_name!r} for country {iso2} not in registry",
            )

    def test_dsf_ci_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.ci"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_bf_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.bf"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_ml_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.ml"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_cm_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.cm"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_ne_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.ne"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_tg_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.tg"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_bj_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.bj"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_gw_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.gw"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_ga_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.ga"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_dsf_cd_xlsx_is_valid_zip(self):
        content = self.env["toomde.ohada.dsf.cd"].xlsx(self.env.company, 2025)
        self.assertEqual(content[:4], b"PK\x03\x04")

    def test_every_ohada_country_has_a_dsf_model(self):
        """COMP-9 closure: each of the 11 OHADA countries in the
        provisioning whitelist must have a DSF model installed. If
        someone adds CG / TD / CF / GQ to the OHADA list in the future
        without shipping a DSF subclass, this test fails — loud — so
        the marketing claim doesn't drift from the implementation."""
        from odoo.addons.l10n_toomde_ohada_overlay.models.dsf_export import DSF_BY_COUNTRY

        # The 11 countries we provision today (see
        # provisioning_service.py:_OHADA_L10N_COUNTRIES in the platform
        # repo). Hard-coded here because the overlay addon must run
        # standalone in Odoo CI without the control-plane code.
        expected_iso2 = {
            "SN", "CI", "BF", "ML", "NE", "TG", "BJ", "GW",  # West
            "CM", "GA", "CD",                                # Central
        }
        self.assertEqual(
            set(DSF_BY_COUNTRY.keys()),
            expected_iso2,
            "DSF coverage drifted from the OHADA provisioning whitelist",
        )

    def test_wizard_default_export_kind_picks_country_dsf(self):
        """When the company country has a country-specific DSF, the
        wizard pre-selects it. UX nicety — the user shouldn't have to
        find their country in the dropdown if we already know it."""
        company = self.env.company
        company.country_id = self.env.ref("base.sn")
        wizard = self.env["toomde.ohada.annual.export.wizard"].create({
            "fiscal_year": 2025,
        })
        self.assertEqual(wizard.export_kind, "dsf_sn")

    def test_wizard_default_falls_back_to_form_1004(self):
        """When the company country is OHADA but doesn't have a
        country-specific DSF yet (e.g. NE, TG, BJ, GW, GA, CD), the
        wizard falls back to the OHADA-wide Form 1004."""
        company = self.env.company
        company.country_id = self.env.ref("base.ne")
        wizard = self.env["toomde.ohada.annual.export.wizard"].create({
            "fiscal_year": 2025,
        })
        self.assertEqual(wizard.export_kind, "form_1004")
