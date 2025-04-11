from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    timeout_hours = fields.Float(
        string='Timeout Hours',
        default=72,
        help='Number of hours before a sale order is considered abandoned'
    )