from odoo import models, fields


class MarkDeliveredWizard(models.TransientModel):
    _name = 'mark.delivered.wizard'
    _description = 'Mark Delivered Wizard'
    _inherit = ['hasura.mixin']

    
    reason = fields.Selection(
        string='Reason for closing',
        selection=[('cancel', ' Customer canceled the rest of the order'), 
                   ('po_not_fullfilled', "Purchasing couldn't fulfilled the rest of the quantity"),
                   ('discrepancy_in_transfer', "Discrepancy in moving items from warehouse to customer's location"),
                   ('delayed', "Delayed Delivery"),
                   ('done', "Order Done"),
                   ]
    )    
    
    def action_mark_done(self):
        sale_id = self.env.context.get('active_ids', False)
        sale_order_ids = self.env['sale.order'].browse(sale_id)
        for rec in sale_order_ids:
            if not rec.is_confirmed and rec.state == 'sale':
                value = {'ref_id': self.id, 'status': "DELIVERED"}
                query = self.prepare_query("updateOrderFromOdoo","OrderInputDTO!","input","ok")
                variable = {
                    "object": value
                } 
                self.run_query(query, variable,delivery=True)
                rec.is_confirmed = True
                rec.reason = self.reason
                rec.is_force_closed = True
                picking_ids = rec.picking_ids.filtered(lambda x: x.state in
                                                        ('draft','waiting','confirmed','assigned'))
                for item in picking_ids:
                    cancellation_reason = self.env['cancellation.reason'].search([('is_default', '=', True)],limit=1)
                    if cancellation_reason:
                        item.cancellation_reason = cancellation_reason.id
                    item.sudo().with_context({'api':True}).action_cancel()
                program_ids = rec.no_code_promo_program_ids + rec.code_promo_program_id
                if program_ids:
                    rec.with_context({'skip': True}).recompute_multi_coupon_lines(program_ids.ids, True)
                if rec.invoice_status == 'to invoice':
                    rec._create_invoices()