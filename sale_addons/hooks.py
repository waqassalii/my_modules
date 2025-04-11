from odoo.addons.sale_stock.models.sale_order import SaleOrderLine
from odoo.addons.delivery.models.sale_order import SaleOrder
from odoo import api


def post_load_hook():

    @api.depends('move_ids.state', 'move_ids.scrapped', 'move_ids.product_uom_qty', 'move_ids.product_uom')
    def _compute_qty_delivered(self):
        super(SaleOrderLine, self)._compute_qty_delivered()

        for line in self:  # TODO: maybe one day, this should be done in SQL for performance sake
            if line.qty_delivered_method == 'stock_move':
                if line.product_id.is_pack:
                    moves = line.x_studio_stock_moves.filtered(lambda x: x.picking_id.location_dest_id.usage == 'customer')
                    return_moves = line.x_studio_stock_moves.filtered(lambda x: x.picking_id.location_id.usage == 'customer')
                    delivered_qty = sum(moves.filtered(lambda x: x.state == 'done' and x.quantity_done > 0).mapped('quantity_done'))
                    return_moves_qty = sum(return_moves.filtered(lambda x: x.state == 'done' and x.quantity_done > 0).mapped('quantity_done'))
                    total_pack_products = sum([x.qty_uom for x in line.product_id.pack_ids])
                    if total_pack_products > 0:
                        delivered_qty = delivered_qty/total_pack_products
                        return_moves_qty = return_moves_qty/total_pack_products
                    qty_delivered = delivered_qty - return_moves_qty
                    line.qty_delivered = round(qty_delivered,2)
                else:
                    qty = 0.0
                    outgoing_moves, incoming_moves = line._get_outgoing_incoming_moves()
                    for move in outgoing_moves:
                        if move.state != 'done':
                            continue
                        qty += move.product_uom._compute_quantity(move.product_uom_qty, line.product_uom, rounding_method='HALF-UP')
                    for move in incoming_moves.filtered(lambda x: x.picking_id.location_id.usage == 'customer' and x.picking_id.x_studio_sales_order):
                        if move.state != 'done':
                            continue
                        qty -= move.product_uom._compute_quantity(move.product_uom_qty, line.product_uom, rounding_method='HALF-UP')
                    line.qty_delivered = qty
                
    SaleOrderLine._compute_qty_delivered = _compute_qty_delivered

    @api.depends('order_line.is_delivery','order_line.is_downpayment')
    def _get_invoice_status(self):
        super(SaleOrder, self)._get_invoice_status()
        for order in self:
            if order.invoice_status in ['no', 'invoiced']:
                continue
            order_lines = order.order_line.filtered(lambda x: not x.is_delivery  and not x.is_pos_charge and not x.is_downpayment and not x.display_type and x.invoice_status != 'invoiced')
            if all(line.product_id.invoice_policy == 'delivery' and line.invoice_status == 'no' for line in order_lines):
                order.invoice_status = 'no'
    
    SaleOrder._get_invoice_status = _get_invoice_status