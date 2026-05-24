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

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ISO2 country codes governed by the OHADA Acte Uniforme + SYSCOHADA chart.
# Mirrors `control-plane/.../provisioning_service._OHADA_L10N_COUNTRIES`
# and `control-plane/.../tenant_l10n_check._OHADA_COUNTRIES`. Keep these
# three lists in sync — updates need to happen everywhere.
_OHADA_COUNTRIES = frozenset({
    # West Africa — UEMOA
    "SN", "CI", "BF", "ML", "NE", "TG", "BJ", "GW",
    # Central Africa — CEMAC + DRC
    "CM", "GA", "CD",
})


def _pre_init_ohada_overlay(env):
    """Refuse install when no res.company is in the OHADA zone.

    The overlay enforces SYSCOHADA mandatory controls (gap-less invoice
    numbering, hash-chained posted journals, French defaults, mobile
    money journals). Those rules are legally meaningful only for
    companies governed by the AUDCIF Acte Uniforme; applying them to a
    US / EU / generic-chart company silently changes audit-trail
    semantics and locks fields the local tax authority doesn't expect.

    Belt to the control-plane provisioning suspenders: provisioning is
    already country-gated, but a manual ``-i l10n_toomde_ohada_overlay``
    from an admin shell would bypass it. This hook is the second
    barrier — it runs before any data file loads, so a refused install
    leaves the database completely untouched.

    Caller can recover by:
      1. setting the company country to an OHADA member, then
         re-running ``-i l10n_toomde_ohada_overlay``, OR
      2. accepting that the overlay isn't appropriate for this tenant.
    """
    companies = env["res.company"].sudo().search([])
    ohada_companies = companies.filtered(
        lambda c: c.country_id and c.country_id.code in _OHADA_COUNTRIES
    )
    if not ohada_companies:
        non_ohada = ", ".join(
            f"{c.name} ({c.country_id.code or 'no country'})"
            for c in companies
        ) or "no companies present"
        raise UserError(
            "l10n_toomde_ohada_overlay requires at least one res.company "
            "in an OHADA member state "
            "(SN, CI, BF, ML, NE, TG, BJ, GW, CM, GA, CD). "
            f"Found: {non_ohada}. "
            "Set the company country first, or do not install this addon."
        )


def _post_init_ohada_overlay(env):
    """Apply mandatory SYSCOHADA controls to every company on this tenant."""
    _set_invoice_sequences_no_gap(env)
    _enable_journal_hash_chain(env)
    _set_french_default_language(env)
    _enable_multi_currency(env)
    _seed_mobile_money_journals(env)
    _set_company_prices_tax_included(env)
    _rebind_domestic_fiscal_position(env)
    _bind_default_sale_tax_to_income_accounts(env)


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


def _rebind_domestic_fiscal_position(env):
    """Bind country-restricted fiscal positions to the company's real country.

    Background. Odoo's per-country l10n charts (l10n_bf, l10n_sn, l10n_ci…)
    declare their Domestic fiscal position with `country_id='base.<iso2>'`.
    During provisioning the chart is loaded right after `account`, when the
    company's `country_id` is still Odoo's default (typically United States).
    Whatever country was on the company at chart-load time is what ends up
    on the Domestic FP — not the company's eventual country. Once the
    operator sets country=BF (or SN, CI…) on the partner, the Domestic FP
    is permanently bound to the wrong country: BF customers can no longer
    match it, fall through to the catch-all "Foreign Trade" FP, and every
    sale tax gets mapped to 0% Exports. Visible symptom: invoices post
    with `amount_tax=0` even though the product carries a 15%/18% TVA.

    Fix. For every company with a country, point every auto-apply FP that
    has a country_id (i.e. the "Domestic" ones — the catch-all FPs use
    NULL country deliberately and are left alone) at the company's actual
    country. Then re-resolve `property_account_position_id` on every sale
    partner so existing customers stop being stuck on "Foreign Trade".

    Catch-all FPs (country_id=NULL) are by design — they match any partner
    that hasn't matched a country-specific FP first — so we never touch
    them. The condition `country_id != False AND != company.country_id`
    selects exactly the broken Domestic-style records.

    See tax.md Phase 7 (AFR-2 / TVA collectée chain).
    """
    Partner = env["res.partner"]
    FP = env["account.fiscal.position"]

    for company in env["res.company"].search([]):
        country = company.country_id
        if not country:
            continue

        misbound = FP.search([
            ("company_id", "=", company.id),
            ("auto_apply", "=", True),
            ("country_id", "!=", False),
            ("country_id", "!=", country.id),
        ])
        if misbound:
            misbound.country_id = country
            _logger.info(
                "OHADA overlay: rebound %d fiscal position(s) to %s on company %r",
                len(misbound), country.code, company.name,
            )

        # Re-resolve auto-apply on every sale partner so customers stuck on
        # the catch-all FP swap to the now-correct Domestic FP.
        partners = Partner.with_company(company).search([
            ("customer_rank", ">", 0),
            "|", ("company_id", "=", False), ("company_id", "=", company.id),
        ])
        rebound = 0
        for partner in partners:
            resolved = FP.with_company(company)._get_fiscal_position(partner)
            current = partner.with_company(company).property_account_position_id
            if resolved != current:
                partner.with_company(company).property_account_position_id = resolved
                rebound += 1
        if rebound:
            _logger.info(
                "OHADA overlay: re-applied fiscal position on %d/%d sale partner(s)",
                rebound, len(partners),
            )


def _bind_default_sale_tax_to_income_accounts(env):
    """Wire the standard sale tax to income accounts so service invoices
    bear TVA even when no product is set on the line.

    Background. Odoo's `account.move.line._compute_tax_ids` falls through
    in this order: product taxes → account default taxes → none. The
    OHADA per-country charts (l10n_bf, l10n_sn, l10n_ci…) declare TVA
    rates but never bind them to the income accounts as defaults. So
    when an operator creates a standalone customer invoice with a
    free-form description (consulting fee, repair, service) and no
    product, the line ends up with tax_ids=False — invoice posts with
    amount_tax=0, TVA collectée account 4431 stays empty, the DSF
    export under-reports. We saw this on demo for `FAC/2026/00006-00008`
    (consulting + atelier + agro-alimentaire service invoices).

    Hook walks each company, picks the highest-rate positive sale tax
    (highest because in West Africa the standard rate is the more
    common one — reduced rates apply only to specific food/basic
    necessities goods, which always go through a product line where
    product taxes win the precedence anyway), and binds it as the
    default on every income-type account that has no sale tax yet.
    Accounts that already have a sale tax bound are left untouched so
    we never trample manual operator configuration.

    The fallback is per-company so multi-company tenants get the
    right rate per company (BF=15%, SN=18%, CI=18%…).

    See tax.md Phase 7 (AFR-2 / TVA collectée chain).
    """
    Account = env["account.account"]
    Tax = env["account.tax"]

    for company in env["res.company"].search([]):
        default_tax = Tax.search([
            ("company_id", "=", company.id),
            ("type_tax_use", "=", "sale"),
            ("amount", ">", 0),
        ], order="amount desc, id asc", limit=1)
        if not default_tax:
            _logger.info(
                "OHADA overlay: company %r has no positive sale tax to bind",
                company.name,
            )
            continue

        income_accounts = Account.search([
            ("company_ids", "in", company.id),
            ("account_type", "in", ("income", "income_other")),
        ])
        bound = 0
        for account in income_accounts:
            existing_sale = account.tax_ids.filtered(
                lambda t: t.type_tax_use == "sale",
            )
            if existing_sale:
                continue
            account.tax_ids = [(4, default_tax.id)]
            bound += 1
        _logger.info(
            "OHADA overlay: bound default sale tax %r (%g%%) to %d/%d "
            "income account(s) on company %r",
            default_tax.name, default_tax.amount, bound, len(income_accounts),
            company.name,
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
