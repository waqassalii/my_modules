# -*- coding: utf-8 -*-

from odoo import models, fields


class SoCreditNote(models.Model):
    _name = 'so.credit.note'

    credit_note_id = fields.Many2one(comodel_name='account.move',string='Credit Note')
    amount = fields.Float(string="Amount")
    order_id = fields.Many2one(comodel_name='sale.order', string='Order ID')