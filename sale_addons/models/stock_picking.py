from odoo import api, fields, models



class StockPicking(models.Model):
    _inherit = 'stock.picking'
    
    delivery_note = fields.Char(string='Delivery Note', copy=False, readonly=True,
                                related='sale_id.delivery_note')
    date_deadline = fields.Datetime(string="Delivery Date (Deadline)", tracking=True)

    def get_picking_type_name(self):
        picking_type = self.picking_type_id.name
        return picking_type

    @api.onchange('scheduled_date')
    def _onchange_scheduled_date(self):
        if self.picking_type_id.is_dispatch and self.state != 'cancel' and (
                        self.scheduled_date.date() - fields.Date.today()).days == 1:
            self.sale_id.write({'odoo_only_shipment_tomorrow': True})
        else:
            self.sale_id.write({'odoo_only_shipment_tomorrow': False})
    
    def button_validate(self):
        res = super(StockPicking, self).button_validate()
        for record in self:
            for item in record.move_ids_without_package:
                if item.purchase_line_id.order_id:
                    order_id = item.purchase_line_id.order_id._get_sale_orders()
                    if order_id:
                        order_id._compute_reserved_line_status()
        return res

    @api.onchange('date_deadline')
    def _onchange_date_deadline(self):
        for rec in self:
            for item in self.move_ids_without_package:
                item.sale_line_id.expected_delivery_date = rec.date_deadline.date()
            