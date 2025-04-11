from odoo import api, fields, models

class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    is_default = fields.Boolean('Is Default')
    is_online_charge = fields.Boolean("Is Online Charge")