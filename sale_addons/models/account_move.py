# -*- coding: utf-8 -*-

from odoo import models, fields, _, api


class AccountMove(models.Model):
    _inherit = 'account.move'

    sale_order_id = fields.Many2one('sale.order', string="Sale Order")
    online_payment_number = fields.Char(readonly=True)
    payment_method = fields.Selection([('cash', 'Cash'),
                                       ('online', 'Online'),
                                       ('pos', 'POS'),
                                       ('link', 'Link as delivery')],
                                      string='Payment Method', readonly=True)
    sp_payment_id = fields.Many2one('account.payment',
                                    string='Payment', readonly=True)
    used_amount = fields.Float(string="Used Amount")
    balance_credit_amount = fields.Float(string="Balance Amount", compute='_compute_balance_to_use')

    def _compute_balance_to_use(self):
        for rec in self:
            rec.balance_credit_amount = rec.amount_residual - rec.used_amount

