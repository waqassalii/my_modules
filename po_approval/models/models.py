# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, ValidationError
from odoo import models, fields, api
import xmlrpc.client
import logging
_logger = logging.getLogger(__name__)


class PurchaseOrderApproval(models.Model):
    _name = 'po.approval'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Purchase Order Request'
    _rec_name = "name"
    _order = "id desc"

    def _default_currency(self):
        return self.env.user.company_id.currency_id.id
    # credential fields
    from_url = fields.Char(string='URL of requesting DB', readonly=True)
    db_name = fields.Char(string='Data Base Name', readonly=True)
    user_name = fields.Char(string='Requested By', readonly=True)
    user_login = fields.Char(string='User Login', readonly=True)
    user_password = fields.Char(string='Password', readonly=True)
    # model fields
    company_name = fields.Char(string='Company Name', readonly=True)
    location_name = fields.Char(string='Delivery Location', readonly=True)
    po_name = fields.Char(string='PO Reference', readonly=True)
    name = fields.Char(string='Name', default="Request for Approval", readonly=True)
    vendor_name = fields.Char(string='Vendor', readonly=True)
    partner_ref = fields.Char(string='Vendor Reference', readonly=True)
    cancel_reason = fields.Char(string='Cancel Reason', readonly=True)
    sendback_reason = fields.Char(string='Send Back Reason', readonly=True)
    po_id = fields.Integer(string='PO ID', readonly=True)
    user_id = fields.Many2one(comodel_name='res.users', string='Responsible', tracking=True, default=lambda self: self.env.user)
    date_request = fields.Datetime('Date Requested', default=fields.Datetime.now(), readonly=True)
    date_approve = fields.Datetime('Date Approved', readonly=True)
    date_order = fields.Datetime('Order Deadline', readonly=True)
    date_planned = fields.Datetime('Expected Arrival', readonly=True)
    description = fields.Text()
    notes = fields.Text()
    approval_lines = fields.One2many(comodel_name='po.approval.line', inverse_name='po_approval_id', string='Approval Lines')
    currency_id = fields.Many2one(comodel_name="res.currency", readonly=True, default=_default_currency)
    amount_untaxed = fields.Monetary(string='Untaxed Amount',store=True, readonly=True, compute='_amount_all')
    amount_tax = fields.Monetary(string='Tax Amount', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total Amount', store=True, readonly=True, compute='_amount_all')
    is_sentback = fields.Boolean(readonly=True)
    is_cancelled = fields.Boolean(readonly=True)
    is_procurement = fields.Boolean(readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('to_approve', 'To Approve'),
        ('approve', 'Approved'),
        ('confirm', 'Confirmed'),
        ('cancel', 'Cancelled'),
    ], string="status", default='draft', copy=False, tracking=True)
    lognote_message = fields.Html()
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')
    alter_user_name = fields.Char()
    alter_user_login = fields.Char()

    def _update_purchase_order(self, vals):
        url, db, user_login, password = self.from_url, self.db_name, self.user_login, self.user_password
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        uid = common.authenticate(db, user_login, password, {})
        models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))
        domain = [('id', '=', self.po_id), ('name', '=', self.po_name)]
        po_rec = models.execute_kw(db, uid, password, 'purchase.order', 'search', [domain])
        if po_rec:
            current_user = self.env.user
            vals.update({
                'alter_user_name': current_user.name,
                'alter_user_login': current_user.login,
                'po_approval_id': self.id,
            })
            po_rec_updated = models.execute_kw(db, uid, password, 'purchase.order', 'write', [[self.po_id], vals])
            if po_rec_updated:
                return True
        else:
            raise ValidationError(f"Purchase order {self.po_name} Does not exist")

    def action_update_sub_db_lines(self,message):
        update_commands = []
        for line in self.approval_lines:
            if line.is_line_changed:
                updated_values = {
                    'product_qty': line.product_qty,
                    'price_unit': line.price_unit,
                }
                update_commands.append((1, line.line_id, updated_values))
                line.is_line_changed = False
        vals = {
            'order_line': update_commands,
            'update_lognote': True,
            'lognote_message': message,
        }
        updated_po = self._update_purchase_order(vals)
        if updated_po:
            _logger.info(f" {self.po_name} is updated")

    def action_draft(self):
        self.is_cancelled = False
        message = f""" <b><span>Rest to</span> <i class="fa fa-long-arrow-right mx-1 text-600"></i>
                                        <span class='text-info'>Draft</span></b><i class='text-muted'>(Status)</i>
                                      """
        vals = {
            'update_lognote': True,
            'lognote_message': message,
        }
        self._update_purchase_order(vals)
        self.state = 'draft'

    def action_confirm(self):
        is_changed = self.approval_lines.mapped('is_line_changed')
        message = f""" <b>approved</b> <i class="fa fa-long-arrow-right mx-1 text-600"></i>
                                <b><span class='text-info'>Confirmed the Order:</span></b>"""
        if True in is_changed:
            self.action_update_sub_db_lines(message)
        else:
            vals = {'is_cancelled': False,
                    'is_approved': True,
                    'lognote_message': message}
            updating_po_rec = self._update_purchase_order(vals)
        self.date_approve = fields.Datetime.now()
        self.state = 'confirm'

    def action_cancel(self):
        if self.is_cancelled:
            self.is_cancelled = False
            self.state = 'cancel'
            return True
        body = self.cancel_reason if self.cancel_reason else "Reason not Provided"
        self.sudo().message_post(body=body)
        message = f"""
                   <b><span class='text-danger'>Cancelled:</span></b> <i class="fa fa-long-arrow-right mx-1 text-600"></i> {self.cancel_reason}
                   <i class='text-muted'>(Status)</i>"""
        vals = {'is_cancelled': True,
                'is_approved': False,
                'is_sentback': False,
                'is_request_sent': False,
                'lognote_message':  message}
        update_po_rec = self._update_purchase_order(vals)
        if update_po_rec:
            self.state = 'cancel'

    def action_open_cancel_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cancel Reason',
            'view_mode': 'form',
            'res_model': 'cancel.reason.wizard',
            'target': 'new',
        }

    def action_to_approve(self):
        group_id = self.env.ref('po_approval.group_po_cost_controller')
        self.sudo().action_create_activity(group_id)
        is_changed = self.approval_lines.mapped('is_line_changed')
        message = f""" <b><span class='text-info'>Draft</span>
                   <i class="fa fa-long-arrow-right mx-1 text-600"></i><span class='text-info'>to Approve</span></b><i class='text-muted'>(Status)</i>
                  """
        if True in is_changed:
            self.action_update_sub_db_lines(message)
        else:
            vals = {
                'update_lognote': True,
                'lognote_message': message,
            }
            self._update_purchase_order(vals)
        self.state = 'to_approve'

    def action_approve(self):
        group_id = self.env.ref('po_approval.group_po_finance_manager')
        self.sudo().action_create_activity(group_id)
        is_changed = self.approval_lines.mapped('is_line_changed')
        message = f""" <b><span class='text-info'>To Approve</span>
                           <i class="fa fa-long-arrow-right mx-1 text-600"></i><span class='text-info'>Approved</span></b><i class='text-muted'>(Status)</i>
                          """
        if True in is_changed:
            self.action_update_sub_db_lines(message)
        else:
            vals = {
                'update_lognote': True,
                'lognote_message': message,
            }
            self._update_purchase_order(vals)
        self.state = 'approve'

    def action_user_existence(self, name, email):
        user_rec = self.env['res.users'].sudo().search([('login', '=', email)], limit=1)
        if not user_rec:
            user_rec = self.env['res.users'].sudo().create({'name': name, 'login': email})
        return user_rec

    @api.model
    def create(self, values):
        record = super(PurchaseOrderApproval, self).create(values)
        if record.alter_user_login and record.alter_user_name:
            alter_user = self.action_user_existence(record.alter_user_name, record.alter_user_login)
            message = "Purchase Order Request created"
            record.with_user(alter_user).sudo().message_post(body=message)
        if record.attachment_ids:
            record.attachment_ids.write({'res_model': self._name, 'res_id': record.id})
        if record.is_procurement:
            group_id = self.env.ref('po_approval.group_po_procurement_manager')
            record.action_create_activity(group_id)
        return record

    def _check_exisitng_activity(self,activity_type):
        existing_activity = self.env['mail.activity'].search([
            ('res_id', '=', self.id), ('activity_type_id', '=', activity_type.id)])
        if existing_activity:
            existing_activity.action_feedback()

    def action_create_activity(self,group_id=False):
        activty_type = self.env.ref('mail.mail_activity_data_todo')
        self._check_exisitng_activity(activty_type)
        if not group_id:
            group_id = self.env.ref('base.group_system')
        activity_user = self.env['res.users'].sudo().search([('groups_id', '=', group_id.id)], limit=1)
        if activity_user:
            for record in self:
                vals = {
                    'summary': 'PO Approval Request',
                    'activity_type_id': activty_type.id,
                    'res_model': 'po.approval',
                    'res_model_id': self.env['ir.model'].search([('model', '=', 'po.approval')], limit=1).id,
                    'res_id': record.id,
                    'user_id': activity_user.id,
                }
                self.env['mail.activity'].sudo().create(vals)
        else:
            raise ValidationError(f"NO User has been assigned the group {group_id.name}")

    def action_send_back(self):
        if self.is_sentback:
            raise ValidationError('Record is 21?.,mnAlready Sent back')
        body = self.sendback_reason if self.sendback_reason else "Sent back, but Reason not Provided"
        self.sudo().message_post(body=body)
        send_back_message = f"""
                           <b><span class='text-warning'>Sent Back:</span></b> 
                            <i class="fa fa-long-arrow-right mx-1 text-600"></i> {self.sendback_reason} <i class='text-muted'>(Status)</i>
                           """
        vals = {'is_request_sent': False,
                'is_cancelled': False,
                'is_sentback': True,
                'lognote_message': send_back_message,
                'is_approved': False}
        line_vals = []
        for line in self.approval_lines:
            line_vals.append((0, 0, {
                'name': line.name,
                'product_qty': line.product_qty,
                'product_id': line.product_id,
                'price_unit': line.price_unit,
            }))
        update_po_rec = self._update_purchase_order(vals)
        if update_po_rec:
            self.is_sentback = True
            self.state = 'draft'

    def action_open_sendback_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Send Back',
            'view_mode': 'form',
            'res_model': 'send.back.wizard',
            'target': 'new',
        }

    def unlink(self):
        # Check if the user belongs to the 'Settings' group
        if not self.env.user.has_group('base.group_system'):
            raise AccessError("You don't have the required permission to delete records.")
        return super(PurchaseOrderApproval, self).unlink()

    def write(self, values):
        if values.get('is_cancelled'):
            alter_user = self.env['res.users'].browse(1)
            for rec in self:
                res = super(PurchaseOrderApproval, rec.with_user(alter_user).sudo()).write(values)
                rec.sudo().action_cancel()
                return res
        else:
            return super(PurchaseOrderApproval, self).write(values)

    @api.depends('approval_lines.price_total')
    def _amount_all(self):
        for rec in self:
            approval_line = rec.approval_lines
            amount_untaxed = max([sum(approval_line.mapped('price_subtotal')),0.0])
            total_amount_tax = max([sum(approval_line.mapped('price_tax')),0.0])
            rec.amount_untaxed = amount_untaxed
            rec.amount_tax = total_amount_tax
            rec.amount_total = rec.amount_untaxed + rec.amount_tax