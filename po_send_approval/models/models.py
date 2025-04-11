# -*- coding: utf-8 -*-
from odoo import models, fields, api
import xmlrpc.client

from odoo.exceptions import ValidationError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    is_approved = fields.Boolean()
    is_request_sent = fields.Boolean()
    is_cancelled = fields.Boolean()
    hide_confirm_button = fields.Boolean()
    po_approval_id = fields.Integer('Approval id')
    is_sentback = fields.Boolean()
    update_lognote = fields.Boolean()
    sendback_reason = fields.Char(string='Send Back Reason')
    lognote_message = fields.Html(string='Log Note')
    alter_user_name = fields.Char()
    alter_user_login = fields.Char()
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')

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

    def _check_purchase_order_approval(self, cred, uid):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        available_models = models.execute_kw(cred.db_name, uid, cred.user_password, 'ir.model', 'search_read',
                                             [[('model', '=', 'po.approval')]], {'limit': 1})
        if not available_models:
            raise ValidationError("Purchase Order Approval is not installed in Main DB")
        return available_models

    def _prepare_approval_values(self, cred):
        return {
            'from_url': cred.current_url,
            'db_name': cred.current_db_name,
            'user_name': self.env.user.name,
            'user_login': cred.current_user_login,
            'user_password': cred.current_user_password,
            'po_id': self.id,
            'po_name': self.name,
            'amount_untaxed': self.amount_untaxed,
            'amount_tax': self.amount_tax,
            'amount_total': self.amount_total,
            'description': self.tax_totals,
            'notes': self.notes,
            'partner_ref': self.partner_ref,
            'vendor_name': self.partner_id.name,
            'date_order': self.date_order,
            'date_planned': self.date_planned,
            'company_name': self.company_id.name,
            'location_name': self.picking_type_id.display_name,
        }

    def _prepare_line_values(self):
        values = []
        for line in self.order_line:
            tax_names = ' '.join([tax.name for tax in line.taxes_id])
            values.append((0, 0, {
                'line_id': line.id,
                'name': line.name,
                'product_qty': line.product_qty,
                'product_id': line.product_id.id,
                'product_uom': line.product_uom.name,
                'price_unit': line.price_unit,
                'price_subtotal': line.price_subtotal,
                'price_tax': line.price_tax,
                'price_total': line.price_total,
                'tax_id_name': tax_names
            }))
        return values

    def _create_approval(self, cred, uid, vals, line_vals):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        vals['approval_lines'] = line_vals if line_vals else False
        vals['is_procurement'] = True
        new_approval = models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval', 'create', [vals])
        if new_approval:
            self.is_request_sent = True
            self.sudo().message_post(body="Request has been sent")

    def _handle_sentback_approval(self, cred, uid, vals, line_vals):
        models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
        existing_lines = models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval.line', 'search_read', [[('po_approval_id', '=', self.po_approval_id)]])
        existing_line_ids = [line['id'] for line in existing_lines]
        models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval.line', 'unlink', [existing_line_ids])
        vals['approval_lines'] = line_vals if line_vals else False
        vals['is_sentback'] = False
        models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval', 'write', [[self.po_approval_id], vals])
        self.is_request_sent = True
        self.is_sentback = False

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

    def action_button_send_approval(self):
        cred = self._get_credentials()
        uid = self._authenticate(cred)
        available_models = self._check_purchase_order_approval(cred, uid)
        vals = self._prepare_approval_values(cred)
        line_vals = self._prepare_line_values()
        current_user = self.env.user
        vals.update({
            'alter_user_name': current_user.name,
            'alter_user_login': current_user.login,
        })
        attachment_ids_list = []
        if self.attachment_ids:
            attachment_ids_list = self._get_attachment_ids(self.attachment_ids,cred,uid)
        if not self.is_sentback and not self.is_request_sent:
            if attachment_ids_list:
                vals['attachment_ids'] = [(6, 0, attachment_ids_list)]
            self._create_approval(cred, uid, vals, line_vals)
        elif self.is_sentback and not self.is_request_sent:
            self._handle_sentback_approval(cred, uid, vals, line_vals)

    def button_cancel(self):
        res = super(PurchaseOrder, self).button_cancel()
        if self.is_request_sent:
            cred = self._get_credentials()
            uid = self._authenticate(cred)
            models = xmlrpc.client.ServerProxy(f'{cred.url}/xmlrpc/2/object')
            domain = [('po_id', '=', self.id)]
            po_rec = models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval', 'search', [domain])
            if po_rec:
                vals = {'is_cancelled': True, }
                models.execute_kw(cred.db_name, uid, cred.user_password, 'po.approval', 'write', [[po_rec[0]], vals])
            else:
                raise ValidationError("Related record is not found in Main DB")
        return res

    def action_user_existence(self, name, email):
        user_rec = self.env['res.users'].sudo().search([('login', '=', email)], limit=1)
        if not user_rec:
            user_rec = self.env['res.users'].sudo().create({'name': name, 'login': email})
        return user_rec

    def action_post_message(self, values):
        self.lognote_message = values.get('lognote_message')
        if values.get('lognote_message'):
            alter_user = self.action_user_existence(values.get('alter_user_name'), values.get('alter_user_login'))
            if alter_user and self.is_approved:
                self.with_user(alter_user).sudo().message_post(body=self.lognote_message)
                self.with_user(alter_user).sudo().button_confirm()
            elif alter_user and self.is_cancelled:
                self.with_user(alter_user).sudo().button_cancel()
            else:
                self.with_user(alter_user).sudo().message_post(body=self.lognote_message)
        else:
            self.sudo().message_post(body=self.lognote_message)

    def write(self, values):
        record = super(PurchaseOrder, self).write(values)
        if values.get('is_approved'):
            self.action_post_message(values)
        elif values.get('is_cancelled'):
            self.action_post_message(values)
        elif values.get('is_sentback'):
            self.action_post_message(values)
        elif values.get('update_lognote'):
            self.action_post_message(values)
        return record

    @api.onchange('partner_id')
    def check_external_partner(self):
        for rec in self:
            if rec.partner_id:
                if rec.partner_id.is_external_customer:
                    rec.hide_confirm_button = True
                else:
                    rec.hide_confirm_button = False

    def button_draft(self):
        res = super(PurchaseOrder, self).button_draft()
        self.is_approved = False
        self.is_request_sent = False
        self.is_cancelled = False
        self.is_sentback = False
        self.sendback_reason = False
        return res

    @api.model
    def create(self, values):
        record = super(PurchaseOrder, self).create(values)
        for rec in record:
            if rec.attachment_ids:
                rec.attachment_ids.write({'res_model': self._name, 'res_id': rec.id})
        return record
