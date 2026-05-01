# Payroll — Future Plan

**Status:** not built. SPA hides the Payroll tab when `GET /api/v2/fields/hr.payslip` returns 404, which is the case on every community-edition tenant we run.

**Decision (recorded):** when payroll becomes a real requirement, integrate an external payroll API. Do **not** build an in-house tax-calculation engine.

---

## Why external, not in-house

`hr_payroll` is Enterprise-only in Odoo. The math (gross → withholding → net) is the easy part; the hard part is owning a regulatory product — federal/state/municipal tax tables that change yearly, year-end documents (W-2, T4, 1099), bank file formats for direct deposit, and audit liability when a tenant gets a withholding wrong. That's not a feature, it's a permanent maintenance burden with legal exposure. SaaS companies our size do not win that fight.

External payroll APIs already solved this problem and price it per-employee. We push employee + contract data, they return the payslip and handle the filings.

## Vendor shortlist

Pick one when the requirement lands, not now.

- **Check (`checkhq.com`)** — embedded payroll API designed for SaaS platforms to white-label. US-only. Their model is "you build the UI, we run payroll." Best fit if we want the payroll tab to feel native to our SPA.
- **Gusto Embedded** — similar embedded model, US-only, more mature. Heavier integration but more brand recognition with users.
- **Deel** — multi-country including contractor payments. Better fit if our tenants hire internationally.
- **ADP / Paychex APIs** — older, more enterprise. Skip unless a specific tenant demands it.

Decision criteria: pick by **tenant geography** first, then by which API is least painful to integrate. Pricing is in the same ballpark across all of them (~$5–$20/employee/month, passed through to tenants).

## Architecture sketch

The whole thing should be a new addon — `hr_payroll_external` or similar — sitting next to `base_api` in `/addons/`. Same constraint as the rest of our fork: zero changes to core Odoo modules.

**Models (small)**
- `hr.payslip.external` — local mirror of payslips fetched from the provider. Fields: `employee_id`, `period_start`, `period_end`, `gross`, `net`, `provider_payslip_id`, `pdf_url`, `status`. Indexed on `employee_id + period_start`.
- `hr.payroll.config` — per-tenant provider credentials (encrypted), provider key, default schedule.
- Optional `hr.contract` extension — only if the chosen provider needs contract data we don't already store on `hr.employee`.

**Controller**
- `GET /api/v2/payroll/payslips` — list, scoped by current user's role
- `GET /api/v2/payroll/payslips/<id>` — detail + signed PDF URL
- `POST /api/v2/payroll/payslips/sync` — webhook receiver from provider; updates local mirror
- `POST /api/v2/payroll/run` — kick off a pay run (gated to admin/HR role)

**Background job**
- Daily cron syncs employee → provider so terminations and new hires propagate without manual action. One-way sync, our DB is the source of truth for employees.

**Auth flow for the provider**
- Per-tenant OAuth or API key, stored encrypted in `hr.payroll.config`. The Control Plane manages the secret rotation, same pattern as `INTERNAL_API_KEY` for the subscription enforcer.

## What lives where

| Concern | Owner |
|---|---|
| Employee + contract data | Our DB (`hr.employee`, optionally `hr.contract`) |
| Tax calculations | Provider |
| Withholding tables, yearly updates | Provider |
| Bank file generation, direct deposit | Provider |
| Year-end forms (W-2, 1099, T4) | Provider |
| Payslip storage / audit trail | Both — provider is source of truth, we mirror for SPA speed |
| UI rendering | SPA + our `hr.payslip.external` model |

## Build order when we start

1. Pick vendor based on first-tenant geography + API quality.
2. Stand up sandbox account, authenticate from our Control Plane, mint per-tenant keys.
3. Build `hr.payroll.config` model + admin UI for entering provider credentials.
4. Build `hr.payslip.external` model + sync logic (one tenant, one employee, end-to-end).
5. Build the four controller routes above.
6. Wire SPA's existing `getPayrollRecords()` mock call to `GET /api/v2/payroll/payslips`.
7. Drop the `MODEL_NOT_FOUND → hide tab` logic for Payroll once the new endpoint exists.

Estimated scope: **2–3 weeks engineering** for v1 with a single provider, single country. Multi-country adds work proportional to the provider's coverage, not ours.

## Open questions for when this lands

- Do tenants run their own payroll provider account, or do we proxy everything through one platform account? Affects pricing and onboarding friction.
- Where does the provider's pay-run approval UI live — embedded in our SPA, or do we redirect users to the provider's portal for the actual "run payroll" button? Affects UX and integration depth.
- How do we handle tenants who are already running payroll elsewhere and just want a viewer? See PAYROLL_PLAN.md path #3 in earlier discussion (manual import) — could be a v0 before v1.
- Do contractor payments (1099 / non-employee) need a different flow? Most providers split this.

## Don't build until

- A real tenant requests it as a deal-breaker, **or**
- We hit the third "where's payroll?" sales conversation in a quarter.

Until then, leave the SPA's `MODEL_NOT_FOUND → empty state` in place. The tab is hidden, no user is blocked, and we're not on the hook for any tax math.
