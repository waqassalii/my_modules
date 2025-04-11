# -*- coding: utf-8 -*-
import re

from odoo import models, fields, api
import xmlrpc.client


class PurchaseOrderApprovalLine(models.Model):
    _name = 'po.approval.line'
    _description = 'Approval Lines'

    def _default_currency(self):
        return self.env.user.company_id.currency_id.id
    currency_id = fields.Many2one(comodel_name="res.currency", readonly=True, default=_default_currency)
    is_line_changed = fields.Boolean(string='Is Changed')
    name = fields.Char(string='Name')
    tax_id_name = fields.Char(string='Tax')
    line_id = fields.Integer(string='orderLine ID')
    product_id = fields.Integer(string='Product ID')
    product_qty = fields.Float(string='Quantity')
    product_uom = fields.Char('UoM')
    price_unit = fields.Float(string='Unit Price')
    price_subtotal = fields.Monetary(string='Tax Excl.',store=True, readonly=True, compute='_compute_amounts_all')
    price_tax = fields.Monetary(string='Tax Amount',store=True, readonly=True, compute='_compute_amounts_all')
    price_total = fields.Monetary(string='Tax Incl.',store=True, readonly=True, compute='_compute_amounts_all')
    po_approval_id = fields.Many2one(comodel_name='po.approval', string='Approval Id',ondelete='cascade')

    @api.depends('product_qty','price_unit')
    def _compute_amounts_all(self):
        for rec in self:
            old_price_unit = rec._origin.price_unit if rec._origin else 0.0
            old_product_qty = rec._origin.product_qty if rec._origin else 0.0
            if (rec.price_unit != old_price_unit) or (rec.product_qty != old_product_qty):
                rec.is_line_changed = True
            amount_untaxed = rec.product_qty * rec.price_unit
            tax_name = rec.tax_id_name
            if tax_name:
                tax_number = int(re.search(r'\d+', tax_name).group())
                amount_tax = amount_untaxed * (tax_number / 100)
            else:
                amount_tax = 0
            rec.update({
                'price_subtotal': amount_untaxed if amount_untaxed else 0.0,
                'price_tax': amount_tax if amount_tax else 0.0,
                'price_total': amount_untaxed + amount_tax,
            })