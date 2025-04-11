import base64
from datetime import datetime

import xlrd

from odoo import models, fields, _
from odoo.exceptions import UserError


class SendBackWizard(models.TransientModel):
    _name = 'send.back.wizard'

    sendback_reason = fields.Char(string="Reason")

    def action_confirm_send_back(self):
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        active_rec = self.env[active_model].browse(active_id)
        if active_rec:
            if active_model == 'account.payment.approval':
                active_rec.reason = self.sendback_reason
            else:
                active_rec.sendback_reason = self.sendback_reason
            active_rec.action_send_back()