import base64
from datetime import datetime

import xlrd

from odoo import models, fields, _
from odoo.exceptions import UserError


class CancelReasonWizard(models.TransientModel):
    _name = 'cancel.reason.wizard'

    cancel_reason = fields.Char(string="Cancel Reason")

    def action_confirm_cancel(self):
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        active_rec = self.env[active_model].browse(active_id)
        if active_rec:
            if active_model == 'account.payment.approval':
                active_rec.reason = self.cancel_reason
            else:
                active_rec.cancel_reason = self.cancel_reason
            active_rec.action_cancel()