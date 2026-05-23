"""Post-install hook applying SYSCOHADA mandatory controls.

The OHADA Acte Uniforme + each member state's tax code require:

  - Sequential, gap-less customer-invoice numbering (anti-fraud rule —
    if a draft invoice is deleted, the next invoice must still take
    the next number, no gap allowed).
  - Posted journal entries must be immutable. Odoo provides
    `restrict_mode_hash_table` on `account.journal` which chains posted
    entries with SHA-256 hashes — turning this on is the SYSCOHADA-
    equivalent of the FEC integrity control.
  - Default UI language is French (SYSCOHADA labels/reports are in
    French; English locale on a SYSCOHADA chart looks broken).

This hook runs once per tenant when the module installs. It is
idempotent — safe to re-run via `-u l10n_toomde_ohada_overlay`.

See tax.md Phase 7 (COMP-4 / COMP-5 / COMP-6 / COMP-7).
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def _post_init_ohada_overlay(env):
    """Apply mandatory SYSCOHADA controls to every company on this tenant."""
    _set_invoice_sequences_no_gap(env)
    _enable_journal_hash_chain(env)
    _set_french_default_language(env)
    _enable_multi_currency(env)
    _seed_mobile_money_journals(env)
    _set_company_prices_tax_included(env)


# Mobile money operators available per OHADA member country, normalized
# to the operator brands an African business owner expects to see in a
# payment-method dropdown. Source: each operator's country-coverage page
# (Orange Money: orange.com, Wave: wave.com/countries, MTN MoMo: mtn.com,
# Moov: moov-africa.com, Airtel Money: airtel.africa).
# Note: TG/GA/CD list Airtel Money even though they're outside Airtel
# Africa's main 14-country map — they aren't, removed those.
# See tax.md Phase 7 AFR-1.
_MOBILE_MONEY_BY_COUNTRY = {
    # West Africa — UEMOA / OHADA
    "SN": ("Wave", "Orange Money", "Free Money"),
    "CI": ("Orange Money", "MTN MoMo", "Moov Money", "Wave"),
    "BF": ("Orange Money", "Moov Money"),
    "ML": ("Orange Money", "Moov Money"),
    "NE": ("Orange Money", "Airtel Money", "Moov Money"),
    "TG": ("T-Money", "Moov Money"),
    "BJ": ("MTN MoMo", "Moov Money", "Celtiis"),
    "GW": ("MTN MoMo", "Orange Money"),
    # Central Africa — CEMAC / OHADA
    "CM": ("MTN MoMo", "Orange Money"),
    "GA": ("Airtel Money", "Moov Money"),
    "CD": ("Orange Money", "Airtel Money", "M-Pesa", "Africell Money"),
}


def _set_invoice_sequences_no_gap(env):
    """Switch customer-invoice sequences to `no_gap` implementation.

    SYSCOHADA / OHADA: customer invoice numbers must be sequential without
    gaps. Odoo's default `standard` implementation allows gaps when a draft
    is deleted before posting — switch to `no_gap` so deletes don't burn
    a number.

    Odoo 17+ removed `account.journal.sequence_id` — invoice numbering is
    now assigned at POST time directly on `account.move.name`, so drafts
    never reserve a number and the SYSCOHADA gap-less rule is satisfied
    by the engine. When the field is absent we no-op and log.
    """
    Journal = env["account.journal"]
    if "sequence_id" not in Journal._fields:
        _logger.info(
            "OHADA overlay: account.journal.sequence_id absent (Odoo 17+); "
            "customer-invoice numbering is gap-less by post-time assignment"
        )
        return

    # Older Odoo (≤16) path — flip each company's sale-journal sequence to
    # no_gap. `sequence_id` is the year-agnostic parent; year/period
    # sub-sequences (`date_range_ids`) inherit.
    sale_journals = Journal.search([("type", "=", "sale")])
    count = 0
    for journal in sale_journals:
        seq = journal.sequence_id
        if seq and seq.implementation != "no_gap":
            seq.implementation = "no_gap"
            count += 1
    _logger.info(
        "OHADA overlay: switched %d customer-invoice sequence(s) to no_gap",
        count,
    )


def _enable_journal_hash_chain(env):
    """Turn on hash-chained immutable journal entries on critical journals.

    Once enabled, posted entries cannot be edited or deleted — each
    posting computes a SHA-256 hash including the previous entry's hash,
    forming an integrity chain. This is the same control Odoo uses for
    French FEC compliance; SYSCOHADA audit rules require an equivalent
    integrity guarantee.

    Restricted to sale / purchase / miscellaneous journals (the journals
    that carry tax-relevant postings). Cash and bank journals are
    intentionally left out — operational journals see corrections often
    enough that hash-locking them creates more support burden than audit
    value.
    """
    target_types = ("sale", "purchase", "general")
    journals = env["account.journal"].search([("type", "in", target_types)])
    count = 0
    for journal in journals:
        # Field name historically `restrict_mode_hash_table`; some Odoo
        # builds rename it. Set defensively.
        for field_name in ("restrict_mode_hash_table",):
            if field_name in journal._fields and not journal[field_name]:
                journal[field_name] = True
                count += 1
                break
    _logger.info(
        "OHADA overlay: enabled hash-chain on %d journal(s)", count
    )


def _set_french_default_language(env):
    """Activate French and set it as the default for OHADA-zone companies.

    Tenants can override in user preferences; this is just a sane default
    so SYSCOHADA account labels (already French in `l10n_syscohada`) line
    up with a French UI rather than mixed FR/EN.
    """
    fr_lang = env["res.lang"].with_context(active_test=False).search(
        [("code", "=", "fr_FR")], limit=1
    )
    if not fr_lang:
        _logger.warning("OHADA overlay: fr_FR language not present; skipping default-lang fix")
        return
    if not fr_lang.active:
        fr_lang.active = True

    # Set partner language on every company partner (admin user inherits).
    for company in env["res.company"].search([]):
        partner = company.partner_id
        if partner and (not partner.lang or partner.lang == "en_US"):
            partner.lang = "fr_FR"


def _enable_multi_currency(env):
    """Activate Odoo's multi-currency feature for OHADA tenants (AFR-5).

    Many African businesses straddle UEMOA / CEMAC zones (XOF ↔ XAF, both
    fixed-pegged to EUR) or invoice in USD/EUR for export. Without
    multi-currency on, an invoice in EUR on an XOF company silently uses
    today's rate but never accrues FX gain/loss postings — wrong for
    SYSCOHADA. Flip the group on every company.
    """
    group_mc = env.ref("base.group_multi_currency", raise_if_not_found=False)
    if not group_mc:
        _logger.warning("OHADA overlay: base.group_multi_currency missing; skipping multi-currency enable")
        return
    # Add the group to every internal user. Avoids the "Show me the
    # currency column" toggle hidden in Settings.
    # Odoo 17+ renamed `res.users.groups_id` to `group_ids`; fall back
    # to the legacy name when running on ≤16.
    Users = env["res.users"]
    group_field = "group_ids" if "group_ids" in Users._fields else "groups_id"
    admins = Users.search([("share", "=", False)])
    if admins:
        admins.write({group_field: [(4, group_mc.id)]})
    _logger.info(
        "OHADA overlay: enabled multi-currency for %d internal user(s)",
        len(admins),
    )


def _seed_mobile_money_journals(env):
    """Seed mobile-money payment journals per OHADA country (AFR-1).

    Phase 1 = capture-only: each operator gets a bank-type journal so
    tenants can mark invoices/receipts as paid via Orange Money / Wave /
    MTN MoMo with the operator's transaction reference. Direct API
    integration with each operator is a separate project (per tax.md
    AFR-1 — explicitly Phase 2, out of scope here).
    """
    journal_model = env["account.journal"]
    companies = env["res.company"].search([])
    created = 0
    for company in companies:
        iso2 = company.country_id and company.country_id.code
        if iso2 not in _MOBILE_MONEY_BY_COUNTRY:
            continue
        for operator in _MOBILE_MONEY_BY_COUNTRY[iso2]:
            # Idempotency: skip if a journal with this code+company already exists.
            code = _journal_code(operator)
            existing = journal_model.search(
                [("code", "=", code), ("company_id", "=", company.id)], limit=1
            )
            if existing:
                continue
            journal_model.create(
                {
                    "name": operator,
                    "code": code,
                    "type": "bank",
                    "company_id": company.id,
                    "currency_id": company.currency_id.id,
                }
            )
            created += 1
    _logger.info(
        "OHADA overlay: seeded %d mobile-money journal(s) across %d company/ies",
        created,
        len(companies),
    )


def _set_company_prices_tax_included(env):
    """Default OHADA companies to tax-included (TTC) sale prices.

    West African retail (OHADA zone) convention: shelf prices, receipts,
    and storefront listings show one price that already includes TVA
    (TTC). Cashiers enter the customer-paying amount; Odoo derives the
    HT base and the embedded tax internally.

    Implementation: flip `res.company.account_price_include='tax_included'`.
    This is the same setting Odoo exposes in Settings → Accounting →
    "Default Sales Price Include" — it cascades to every existing AND
    future sale tax on the company (each tax's `price_include` is a
    computed field that falls back to the company default unless the
    tax has an explicit `price_include_override`).

    Constraint: Odoo refuses to change `account_price_include` on a
    company that already has any `account.move.line` records (see
    `account.company._check_set_account_price_include`). We catch the
    ValidationError and skip — that company stays HT until an operator
    clears its accounting and re-runs `-u l10n_toomde_ohada_overlay`.
    This is the safe default: existing tenants with real history are
    untouched; fresh tenants get TTC end-to-end.

    See tax.md Phase 7 (AFR-2 / store-app TTC parity).
    """
    from odoo.exceptions import ValidationError

    set_count = 0
    skipped = []
    for company in env["res.company"].search([]):
        if company.account_price_include == "tax_included":
            continue
        try:
            company.account_price_include = "tax_included"
            set_count += 1
        except ValidationError:
            skipped.append(company.name)
    _logger.info(
        "OHADA overlay: defaulted %d company/ies to tax-included (TTC) sale prices",
        set_count,
    )
    if skipped:
        _logger.warning(
            "OHADA overlay: %d company/ies already have accounting entries "
            "and stayed HT — clear move lines and re-run -u to apply TTC: %s",
            len(skipped),
            ", ".join(skipped),
        )


def _journal_code(operator: str) -> str:
    """Build the 5-char Odoo journal code from an operator brand.

    Odoo enforces a 5-character upper-bound on `account.journal.code`.
    Lower-case the operator name, strip spaces and non-alphanumerics,
    truncate to 5. `Orange Money` -> `ORANG`, `Wave` -> `WAVE`,
    `MTN MoMo` -> `MTNMO`.
    """
    cleaned = "".join(c for c in operator if c.isalnum()).upper()
    return cleaned[:5] or "MMNY"
