# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo import models, fields, api
import xmlrpc.client


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_approved = fields.Boolean()
    is_request_sent = fields.Boolean()
    is_cancelled = fields.Boolean()
    acc_payment_approval_id = fields.Integer('Payment Approval id')
    is_sentback = fields.Boolean()
    update_lognote = fields.Boolean()
    reason = fields.Char(string='Reason')
    ref_num = fields.Char(string='Reference No.', readonly=True)
    lognote_message = fields.Html()
    alter_user_name = fields.Char()
    alter_user_login = fields.Char()
    attachment_ids = fields.Many2many(comodel_name='ir.attachment',relation='send_account_payment_ir_attachment_rel',
                                      column1='send_account_payment', column2='attachment_id', string='Attachments')
    # to filter the rec for journal payments
    is_journal_payment = fields.Boolean()

    @api.model
    def create(self, values):
        record = super(AccountPayment, self).create(values)
        for rec in record:
            if rec.attachment_ids:
                rec.attachment_ids.write({'res_model': self._name, 'res_id': rec.id})
        return record

    def _action_create_seq(self):
        if not self.ref_num:
            ref_num = self.env['ir.sequence'].next_by_code('account.payment') or 'new'
            return ref_num

    def _get_credentials(self):
        cred = self.env['db.credential'].search([], limit=1)
        if not cred:
            raise ValidationError("Credentials are not Provided")
        return cred

    def _authenticate(self, cred):
        common = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/common')
        uid = common.authenticate(cred.db_name, cred.user_login, cred.user_password, {})
        if not uid:
            raise ValidationError("Authentication failed")
        return uid

    def _check_account_payment_approval(self, cred, uid):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        available_models = models.execute_kw(cred.db_name, uid, cred.user_password, 'ir.model', 'search_read',
                                             [[('model', '=', 'account.payment.approval')]], {'limit': 1})
        if not available_models:
            raise ValidationError("Payment Approval App is not installed in Main DB")
        return available_models

    def _prepare_values(self, cred):
        return {
            'request_url': cred.current_url,
            'request_db_name': cred.current_db_name,
            'requester_name': self.env.user.name,
            'requester_login': cred.current_user_login,
            'requester_password': cred.current_user_password,
            'acc_payment_id': self.id,
            'acc_payment_name': self.ref_num,
            'currency_id': self.currency_id.id,
            'amount': self.amount,
            'vendor_name': self.partner_id.name,
            'date': self.date,
            'ref': self.ref,
            'company_name': self.company_id.name,
            'journal_name': self.journal_id.name,
            'bank_name': self.partner_bank_id.acc_number,
            'payment_method_name': self.payment_method_line_id.name,
            'payment_type': self.payment_type,
            'is_journal_payment': self.is_journal_payment,
        }

    def _get_attachment_ids(self,attachment_ids,cred,uid):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        attachment_ids_list = []
        for attachment in attachment_ids:
            attachment_data = {
                'name': attachment.name,
                'datas': attachment.datas,
                'store_fname': attachment.store_fname,
                'res_model': 'purchase.order',
                'res_id': self.id,
            }
            attachment_id = models.execute_kw(cred.db_name, uid, cred.user_password, 'ir.attachment', 'create',
                                              [attachment_data])
            attachment_ids_list.append(attachment_id)
        return attachment_ids_list

    def _create_payment_approval(self, cred, uid, vals):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        new_approval = models.execute_kw(cred.db_name, uid, cred.user_password, 'account.payment.approval', 'create', [vals])
        if new_approval:
            return True

    def _handle_sentback_approval(self, cred, uid, vals):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        vals['is_sentback'] = False
        update_approval = models.execute_kw(cred.db_name, uid, cred.user_password, 'account.payment.approval', 'write',
                          [[self.acc_payment_approval_id], vals])
        if update_approval:
            return True

    def action_send_approval(self):
        cred = self._get_credentials()
        uid = self._authenticate(cred)
        if uid:
            available_models = self._check_account_payment_approval(cred, uid)
            if available_models:
                self.ref_num = self._action_create_seq()
                vals = self._prepare_values(cred)
                attachment_ids_list = []
                if self.attachment_ids:
                    attachment_ids_list = self._get_attachment_ids(self.attachment_ids, cred, uid)
                if self.is_sentback and not self.is_request_sent:
                    vals['is_sentback'] = False
                    update_approval = self._handle_sentback_approval(cred, uid, vals)
                    if update_approval:
                        self.is_request_sent = True
                        self.is_sentback = False
                        self.sudo().message_post(body="Payment Approval Request has been sent again")
                if not self.is_sentback and not self.is_request_sent:
                    vals['is_fm'] = True
                    if attachment_ids_list:
                        vals['attachment_ids'] = [(6, 0, attachment_ids_list)]
                    new_approval = self._create_payment_approval(cred, uid, vals)
                    if new_approval:
                        self.is_request_sent = True
                        self.sudo().message_post(body="Payment Approval Request has been sent")
            else:
                raise ValidationError("Payment Approval is not installed in Main DB")

    def action_user_existence(self,name,email):
        user_rec = self.env['res.users'].sudo().search([('login', '=', email)], limit=1)
        if not user_rec:
            user_rec = self.env['res.users'].sudo().create({'name':name, 'login': email})
        return user_rec

    def action_post_message(self, values):
        if values.get('lognote_message'):
            alter_user = self.action_user_existence(values.get('alter_user_name'), values.get('alter_user_login'))
            self.lognote_message = values.get('lognote_message')
            self.with_user(alter_user).sudo().message_post(body=self.lognote_message)
        else:
            self.sudo().message_post(body="no reason provided")

    def write(self, values):
        record = super(AccountPayment, self).write(values)
        if values.get('is_approved'):
            self.action_post_message(values)
            self.sudo().action_post()
        if values.get('is_cancelled'):
            self.action_post_message(values)
            self.sudo().action_cancel()
        if values.get('is_sentback'):
            self.action_post_message(values)
        if values.get('update_lognote'):
            self.action_post_message(values)
        return record

    def action_cancel(self):
        res = super(AccountPayment,self).action_cancel()
        if self.is_request_sent:
            cred = self._get_credentials()
            uid = self._authenticate(cred)
            models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
            domain = [('acc_payment_id', '=', self.id)]
            pa_rec = models.execute_kw(cred.db_name, uid, cred.user_password, 'account.payment.approval', 'search', [domain])
            if pa_rec:
                self.is_request_sent = False
                vals = {'is_cancelled': True,}
                models.execute_kw(cred.db_name, uid, cred.user_password, 'account.payment.approval', 'write', [[pa_rec[0]], vals])
            else:
                raise ValidationError("Related record is not found in Main DB")
        return res
