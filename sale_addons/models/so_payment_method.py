# -*- coding: utf-8 -*-

from odoo import models, fields


class SoPaymentMethod(models.Model):
    _name = 'so.payment.method'

    payment_method = fields.Selection([
        ('cash', 'Cash'),
        ('pos', 'POS'),
        ('online', 'Online'),
        ('wallet', 'Wallet'),
        ('link', 'Link')
    ], default='cash', string="Payment Method")
    amount = fields.Float(string="Amount")
    transaction_id = fields.Char('Transaction')
    provider = fields.Char('Provider')
    order_id = fields.Many2one(comodel_name='sale.order', string='Order ID')