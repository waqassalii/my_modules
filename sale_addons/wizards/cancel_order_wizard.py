from odoo import fields, models, api, _
from odoo.exceptions import UserError
import json


class CancelOrderWizard(models.TransientModel):
    _name = 'cancel.order.wizard'

    order_id = fields.Many2one('sale.order', string="Sale Order")
    cancellation_reason = fields.Selection(
        string='Cancellation Reason',
        selection=[('Ordered by mistake', 'Ordered by mistake'), 
                    ('User not qualified', 'User not qualified'),
                    ("Purchasing couldn't fulfill the order", "Purchasing couldn't fulfill the order"),
                    ("Missed delivery window", "Missed delivery window"),
                    ("User found better price", "User found better price"),
                    ("User couldn't apply promo", "User couldn't apply promo")]
    )
    
    def action_cancel(self):
        self.order_id.cancellation_reason = self.cancellation_reason
        return self.order_id.with_context({'disable_cancel_warning': True, 'api': True}).action_cancel()
