from odoo import fields, models, api, _
from odoo.exceptions import UserError
import json


class ChooseDeliveryCarrier(models.TransientModel):
    _name = 'choose.delivery.carrier'
    _inherit = ['choose.delivery.carrier', 'hasura.mixin']

    def button_confirm(self):
        self.order_id.set_delivery_line(self.carrier_id, self.delivery_price)
        self.order_id.write({
            'recompute_delivery_price': False,
            'delivery_message': self.delivery_message,
        })
        #add shipping to backend