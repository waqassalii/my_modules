from odoo import api, fields, models


class RemainingSaleOrderLine(models.Model):
    _name = 'remaining.sale.order.line'
    _description = 'Remaining Sale Order Line'

    product_id = fields.Many2one('product.product', string='Product', required=True)
    remaining_qty = fields.Integer(string="Remaining Qty", copy=False)
    order_id = fields.Many2one('sale.order', string='Order', required=True, ondelete='cascade')
