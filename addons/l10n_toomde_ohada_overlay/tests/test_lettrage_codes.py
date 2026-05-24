# -*- coding: utf-8 -*-
"""Alphabetic lettrage codes on `account.full.reconcile`."""

from datetime import date

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.l10n_toomde_ohada_overlay.models.account_full_reconcile import (
    lettrage_code_to_int,
    lettrage_int_to_code,
)


def _ensure_recon_account(env, code='411LET', name='Test Receivable LET',
                         account_type='asset_receivable'):
    Account = env['account.account'].sudo()
    acc = Account.search([('code', '=', code)], limit=1)
    if not acc:
        acc = Account.create({
            'code': code,
            'name': name,
            'account_type': account_type,
            'reconcile': True,
        })
    return acc


def _ensure_misc_journal(env):
    J = env['account.journal'].sudo()
    j = J.search([('type', '=', 'general')], limit=1)
    if not j:
        j = J.create({'name': 'Test OD', 'code': 'TODL', 'type': 'general'})
    return j


def _make_balanced_move(env, account, journal, partner, debit, credit,
                        move_date=None, ref=None):
    move_date = move_date or date.today()
    counter = journal.default_account_id
    if not counter:
        Account = env['account.account'].sudo()
        counter = Account.search([('account_type', '=', 'asset_current')], limit=1)
        if not counter:
            counter = Account.create({
                'code': '512LET',
                'name': 'Test Bank LET',
                'account_type': 'asset_current',
                'reconcile': False,
            })
    Move = env['account.move'].sudo()
    if debit:
        lines = [
            (0, 0, {'account_id': account.id, 'partner_id': partner.id,
                    'name': ref or 'Debit', 'debit': debit, 'credit': 0}),
            (0, 0, {'account_id': counter.id, 'partner_id': partner.id,
                    'name': ref or 'Debit (cp)', 'debit': 0, 'credit': debit}),
        ]
    else:
        lines = [
            (0, 0, {'account_id': account.id, 'partner_id': partner.id,
                    'name': ref or 'Credit', 'debit': 0, 'credit': credit}),
            (0, 0, {'account_id': counter.id, 'partner_id': partner.id,
                    'name': ref or 'Credit (cp)', 'debit': credit, 'credit': 0}),
        ]
    move = Move.create({
        'journal_id': journal.id,
        'date': move_date,
        'ref': ref or '',
        'line_ids': lines,
    })
    move.action_post()
    return move


@tagged('post_install', '-at_install')
class TestLettrageCodes(TransactionCase):

    def test_int_to_code_round_trip(self):
        for n in [1, 2, 25, 26, 27, 52, 53, 100, 701, 702, 1000]:
            self.assertEqual(lettrage_code_to_int(lettrage_int_to_code(n)), n)

    def test_int_to_code_known(self):
        self.assertEqual(lettrage_int_to_code(1), 'A')
        self.assertEqual(lettrage_int_to_code(26), 'Z')
        self.assertEqual(lettrage_int_to_code(27), 'AA')
        self.assertEqual(lettrage_int_to_code(52), 'AZ')
        self.assertEqual(lettrage_int_to_code(53), 'BA')
        self.assertEqual(lettrage_int_to_code(702), 'ZZ')
        self.assertEqual(lettrage_int_to_code(703), 'AAA')

    def test_invalid_inputs(self):
        self.assertEqual(lettrage_int_to_code(0), '')
        self.assertEqual(lettrage_int_to_code(-5), '')
        self.assertEqual(lettrage_code_to_int(''), 0)
        self.assertEqual(lettrage_code_to_int('a'), 0)
        self.assertEqual(lettrage_code_to_int('A1'), 0)
        self.assertEqual(lettrage_code_to_int(None), 0)

    def test_lettrage_code_assigned_on_reconcile(self):
        env = self.env
        company = env.user.company_id
        partner = env['res.partner'].sudo().create({'name': 'LET Test Partner'})
        account = _ensure_recon_account(env)
        journal = _ensure_misc_journal(env)

        m1 = _make_balanced_move(env, account, journal, partner, debit=100, credit=0, ref='INV-A')
        m2 = _make_balanced_move(env, account, journal, partner, debit=0, credit=100, ref='PAY-A')
        lines = (m1.line_ids + m2.line_ids).filtered(lambda l: l.account_id == account)
        self.assertEqual(len(lines), 2)
        lines.reconcile()
        full = lines.mapped('full_reconcile_id')
        self.assertTrue(full, "Reconcile must produce a full_reconcile")
        self.assertEqual(full.lettrage_code, 'A')
        self.assertEqual(full.lettrage_account_id, account)
        self.assertEqual(full.lettrage_company_id, company)

    def test_lettrage_code_increments_per_account(self):
        env = self.env
        partner = env['res.partner'].sudo().create({'name': 'LET Partner 2'})
        account = _ensure_recon_account(env, code='411LET2', name='Test Recv 2')
        journal = _ensure_misc_journal(env)

        def reconcile_pair(amount, ref):
            m1 = _make_balanced_move(env, account, journal, partner, debit=amount, credit=0, ref=ref + '-D')
            m2 = _make_balanced_move(env, account, journal, partner, debit=0, credit=amount, ref=ref + '-C')
            lines = (m1.line_ids + m2.line_ids).filtered(lambda l: l.account_id == account)
            lines.reconcile()
            return lines.mapped('full_reconcile_id')

        f1 = reconcile_pair(100, 'P1')
        f2 = reconcile_pair(200, 'P2')
        f3 = reconcile_pair(300, 'P3')
        self.assertEqual(f1.lettrage_code, 'A')
        self.assertEqual(f2.lettrage_code, 'B')
        self.assertEqual(f3.lettrage_code, 'C')

    def test_lettrage_code_scoped_per_account(self):
        env = self.env
        partner = env['res.partner'].sudo().create({'name': 'LET Partner 3'})
        acc_a = _ensure_recon_account(env, code='411LETA', name='Test Recv A')
        acc_b = _ensure_recon_account(env, code='411LETB', name='Test Recv B')
        journal = _ensure_misc_journal(env)

        def reconcile_on(account, amount, ref):
            m1 = _make_balanced_move(env, account, journal, partner, debit=amount, credit=0, ref=ref + '-D')
            m2 = _make_balanced_move(env, account, journal, partner, debit=0, credit=amount, ref=ref + '-C')
            lines = (m1.line_ids + m2.line_ids).filtered(lambda l: l.account_id == account)
            lines.reconcile()
            return lines.mapped('full_reconcile_id')

        a1 = reconcile_on(acc_a, 100, 'A1')
        b1 = reconcile_on(acc_b, 100, 'B1')
        a2 = reconcile_on(acc_a, 200, 'A2')
        self.assertEqual(a1.lettrage_code, 'A')
        self.assertEqual(b1.lettrage_code, 'A',
                         "Each account gets its own series starting at A")
        self.assertEqual(a2.lettrage_code, 'B')

    def test_unreconcile_does_not_recycle_code(self):
        env = self.env
        partner = env['res.partner'].sudo().create({'name': 'LET Partner 4'})
        account = _ensure_recon_account(env, code='411LETU', name='Test Recv U')
        journal = _ensure_misc_journal(env)
        m1 = _make_balanced_move(env, account, journal, partner, debit=100, credit=0, ref='U1-D')
        m2 = _make_balanced_move(env, account, journal, partner, debit=0, credit=100, ref='U1-C')
        lines = (m1.line_ids + m2.line_ids).filtered(lambda l: l.account_id == account)
        lines.reconcile()
        first_code = lines.mapped('full_reconcile_id').lettrage_code
        self.assertEqual(first_code, 'A')

        lines.remove_move_reconcile()
        lines.reconcile()
        second_code = lines.mapped('full_reconcile_id').lettrage_code
        self.assertEqual(second_code, 'B',
                         "After délettrage + relettrage the series advances")
