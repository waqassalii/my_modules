from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    discount_product_id = fields.Many2one(
        'product.product', string='Discount Product', readonly=True,
        states={'draft': [('readonly', False)]},
        help="Product used to apply discount on the order line.")