# -*- coding: utf-8 -*-
import base64

from odoo import models, fields, _, api
from lxml import etree
from itertools import groupby
import json
from datetime import datetime, timedelta, time
from odoo.exceptions import UserError, AccessError, ValidationError
from graphqlclient import GraphQLClient

from odoo.tests import Form
from odoo.tools.float_utils import float_is_zero, float_compare
from collections import defaultdict
import logging
from odoo.tools.misc import formatLang
from functools import reduce
import time as delay
from urllib.error import URLError
import odoo.addons.s3_addons.s3_helper as s3

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'hasura.mixin']

    @api.model
    def _lang_get(self):
        return self.env['res.lang'].get_installed()

    payment_method = fields.Selection([('cash', 'Cash'),
                                       ('online', 'Online'),
                                       ('pos', 'POS'),
                                       ('link', 'Link as delivery')],
                                      string='Payment Type')
    payment_id = fields.Many2one('account.payment', ondelete='restrict',
                                 readonly=True)
    online_payment_number = fields.Char(string='Online Payment Number', readonly=True)
    state = fields.Selection(
        selection_add=[
            ("pending", "Pending"),
            ("sent",),
        ],
        ondelete={"pending": "set default"}
    )
    commitment_date = fields.Datetime(tracking=True)
    is_old_bundle_order = fields.Boolean(string="Old bundle")
    vat_length = fields.Integer(string='Tax ID Length', compute='_compute_vat_length', store=True)
    is_api = fields.Boolean(string="Api", copy=False)
    is_synced = fields.Boolean(string="Synced", copy=False)
    partner_id = fields.Many2one(
        domain="['|','&', ('company_id', '=', False), ('company_id', '=', company_id), ('type','=','invoice')]")
    partner_shipping_id = fields.Many2one(
        domain="[('parent_id', '=', partner_id),('type','=','delivery')]")
    proforma_tax_totals_json = fields.Char(compute='_compute_proforma_tax_totals_json')
    proforma_delivered_tax_totals_json = fields.Char(compute='_compute_proforma_delivered_tax_totals_json')
    transit_discount = fields.Float(compute='_compute_transit_discount')
    delivered_discount = fields.Float(compute='_compute_delivered_discount')
    use_delivered_qty = fields.Boolean(string="Use delivered qty")
    lang = fields.Selection(_lang_get,
                            string='Language',
                            default='ar_001')
    delivery_note = fields.Char(string="Delivery Note", copy=False)
    # _compute_margin is implemented in sale_margin module
    margin = fields.Monetary("Margin", compute='_compute_margin', store=True, groups="base.group_no_one")
    margin_percent = fields.Float("Margin (%)", compute='_compute_margin',
                                  store=True, group_operator='avg', groups="base.group_no_one")
    total_odoo_amount = fields.Float(string="Total odoo amount", compute='_compute_total_amount', store=True)
    expected_delivery_date = fields.Date(string='Expected Delivery Date')
    cycle_time = fields.Integer(string="Cycle Time")
    down_payment = fields.Float(string="Down Payment")
    ordered_by = fields.Char(string="Ordered By")
    is_confirmed = fields.Boolean(string="Confirmed", copy=False)
    no_of_days = fields.Integer(string="No of days")
    use_credit_balance = fields.Float(string="Used Credit Balance", compute='_get_used_balance', copy=False)
    reconciled_balance = fields.Float(string="Reconciled Balance", copy=False)
    balance_credit_balance = fields.Float(string="Balance Credit balance", compute='_compute_balance_credit')
    is_use_credit_balance = fields.Boolean(string="Use Credit Balance", copy=False)
    is_use_custom_balance = fields.Boolean(string="Use Custom Balance For Proforma", copy=False)
    custom_balance = fields.Float(string="Custom Balance", copy=False)
    printed_balance = fields.Float(string="Printed Balance", default=0, copy=False)
    balance_left = fields.Float(string="Balance Left", compute='_compute_balance_left')
    show_balance = fields.Float(string="Show balance", default=0, copy=False)
    reason = fields.Selection(
        string='Reason for closing',
        selection=[('cancel', ' Customer canceled the rest of the order'),
                   ('po_not_fullfilled', "Purchasing couldn't fulfilled the rest of the quantity"),
                   ('discrepancy_in_transfer', "Discrepancy in moving items from warehouse to customer's location"),
                   ('delayed', "Delayed Delivery"),
                   ('done', "Order Done")]
    )
    is_force_closed = fields.Boolean(string="Force closed")
    out_of_zone = fields.Boolean(string="Out of Zone", compute='_compute_out_of_zone')
    merge_shipment = fields.Boolean(string="Merge Shipment")
    credit_note_ids = fields.One2many(comodel_name='so.credit.note', inverse_name='order_id', string='Credit Note Info')
    so_payment_method_ids = fields.One2many(comodel_name='so.payment.method', inverse_name='order_id',
                                            string='Payment Method')
    online_payment_fail = fields.Boolean(string="Online Payment Fail", copy=False)
    past_number_of_orders = fields.Integer(string="Past Number of Orders", compute='_compute_past_number_of_orders')
    shipment_count = fields.Integer(string="Number of Shipments", compute='_compute_shipment_count')
    remaining_order_line_ids = fields.One2many(comodel_name='remaining.sale.order.line', inverse_name='order_id',
                                               string='Remaining Order Lines',
                                               compute='_compute_remaining_order_line_ids')
    number_of_deliveries = fields.Integer(string="Number of Deliveries", compute='_compute_number_of_deliveries')
    current_delivery_number = fields.Integer(string="Current Delivery Number", copy=False)
    cancellation_reason = fields.Char(string="Cancellation reason", copy=False, tracking=True)
    line_status = fields.Char(string="Line Status", copy=False,compute='_compute_reserved_line_status')
    transfer_ids = fields.One2many('stock.picking', 'sale_id', string="Transfers", domain=['|',('is_dispatch', '=', True),('is_dropship', '=', True)])
    is_backend_order = fields.Boolean(string='Backend Order', copy=False)
    total_order_margin = fields.Float(string="SO Margin", compute='_compute_order_margin')
    total_opex_cost = fields.Float(string='Total OPEX Cost', compute='_compute_total_opex_cost')
    profitability = fields.Float(string="Profitability", compute='_compute_profitability')

    @api.depends('order_line', 'order_line.total_margin')
    def _compute_order_margin(self):
        for order in self:
            total_margin = sum(order.order_line.mapped('total_margin'))
            if total_margin:
                order.total_order_margin = total_margin
            else:
                order.total_order_margin = 0

    @api.depends('order_line.opex_cost_line')
    def _compute_total_opex_cost(self):
        for order in self:
            total_opex_cost = sum(order.order_line.mapped('opex_cost_line'))
            order.total_opex_cost = max(total_opex_cost,0)

    @api.depends('total_opex_cost', 'total_order_margin')
    def _compute_profitability(self):
        for order in self:
            order.profitability = (order.total_order_margin - order.total_opex_cost) or 0

    @api.depends('picking_ids')
    def _compute_number_of_deliveries(self):
        for order in self:
            order.number_of_deliveries = len(order.picking_ids.filtered(lambda x: x.picking_type_id.is_out_delivery and x.state != 'cancel'))
            order.current_delivery_number = len(order.picking_ids.filtered(lambda x: x.picking_type_id.is_truck_loading and x.state == 'done'))

    @api.depends('credit_note_ids')
    def _get_used_balance(self):
        for rec in self:
            rec.use_credit_balance = sum(rec.credit_note_ids.mapped('amount'))
    
    @api.depends('partner_id')
    def _compute_vat_length(self):
        for rec in self:
            rec.vat_length = len(rec.partner_id.vat) if rec.partner_id.vat else 0

    @api.model
    def fields_view_get(self, view_id=None, view_type='form',
                        toolbar=False, submenu=False):
        res = super(SaleOrder, self).fields_view_get(
            view_id=view_id, view_type=view_type,
            toolbar=toolbar, submenu=submenu)
        context = self._context
        # if context.get('turn_view_readonly'):  # Check for context value
        doc = etree.XML(res['arch'])
        if view_type == 'form' and self.env.user.has_group(
                'sale_addons.group_sale_readonly'):  # Applies only for form view
            for node in doc.xpath("//field"):  # All the view fields to readonly
                node.set('readonly', '1')
                modifiers = json.loads(node.get("modifiers"))
                modifiers['readonly'] = True
                node.set("modifiers", json.dumps(modifiers))
            for node in doc.xpath("//button"):
                node.set('invisible', '1')
                if node.get("modifiers"):
                    modifiers = json.loads(node.get("modifiers"))
                    modifiers['invisible'] = True
                    node.set("modifiers", json.dumps(modifiers))
        res['arch'] = etree.tostring(doc, encoding='unicode')
        return res

    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a sales order. This method may be
        overridden to implement custom invoice generation (making sure to call super() to establish
        a clean extension chain).
        """
        self.ensure_one()
        journal = self.env['account.move'].with_context(default_move_type='out_invoice')._get_default_journal()
        if not journal:
            raise UserError(
                _('Please define an accounting sales journal for the company %s (%s).', self.company_id.name,
                  self.company_id.id))

        invoice_vals = {
            'ref': self.client_order_ref or '',
            'move_type': 'out_invoice',
            'narration': self.note,
            'currency_id': self.pricelist_id.currency_id.id,
            'sale_order_id': self.id,
            'campaign_id': self.campaign_id.id,
            'medium_id': self.medium_id.id,
            'source_id': self.source_id.id,
            'user_id': self.user_id.id,
            'invoice_user_id': self.user_id.id,
            'team_id': self.team_id.id,
            'partner_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'fiscal_position_id': (self.fiscal_position_id or self.fiscal_position_id.get_fiscal_position(
                self.partner_invoice_id.id)).id,
            'partner_bank_id': self.company_id.partner_id.bank_ids[:1].id,
            'journal_id': journal.id,  # company comes from the journal
            'invoice_origin': self.name,
            'invoice_payment_term_id': self.payment_term_id.id,
            'payment_reference': self.reference,
            'transaction_ids': [(6, 0, self.transaction_ids.ids)],
            'invoice_line_ids': [],
            'company_id': self.company_id.id,
            'use_credit_balance': self.balance_credit_balance,
            'payment_method': self.payment_method,
            'online_payment_number': self.online_payment_number,
            'sp_payment_id': self.payment_id.id
        }
        return invoice_vals

    def _get_invoiceable_lines(self, final=False):
        """Return the invoiceable lines for order `self`."""
        down_payment_line_ids = []
        invoiceable_line_ids = []
        pending_section = None
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        # suplyd-mod: avoid discount lines
        for line in self.order_line.filtered(lambda x: x.price_unit > 0):
            if line.display_type == 'line_section':
                # Only invoice the section if one of its lines is invoiceable
                pending_section = line
                continue
            if line.display_type != 'line_note' and float_is_zero(line.qty_to_invoice, precision_digits=precision):
                continue
            if line.qty_to_invoice > 0 or (line.qty_to_invoice < 0 and final) or line.display_type == 'line_note':
                if line.is_downpayment:
                    # Keep down payment lines separately, to put them together
                    # at the end of the invoice, in a specific dedicated section.
                    down_payment_line_ids.append(line.id)
                    continue
                if pending_section:
                    invoiceable_line_ids.append(pending_section.id)
                    pending_section = None
                invoiceable_line_ids.append(line.id)

        return self.env['sale.order.line'].browse(invoiceable_line_ids + down_payment_line_ids)

    def _create_invoices(self, grouped=False, final=False, date=None):
        """
        Create the invoice associated to the SO.
        :param grouped: if True, invoices are grouped by SO id. If False, invoices are grouped by
                        (partner_invoice_id, currency)
        :param final: if True, refunds will be generated if necessary
        :returns: list of created invoices
        """
        if not self.env['account.move'].check_access_rights('create', False):
            try:
                self.check_access_rights('write')
                self.check_access_rule('write')
            except AccessError:
                return self.env['account.move']

        # 1) Create invoices.
        invoice_vals_list = []
        invoice_item_sequence = 0  # Incremental sequencing to keep the lines order on the invoice.
        for order in self:
            order = order.with_company(order.company_id)
            current_section_vals = None
            down_payments = order.env['sale.order.line']

            invoice_vals = order._prepare_invoice()
            invoiceable_lines = order._get_invoiceable_lines(final)

            if not any(not line.display_type for line in invoiceable_lines):
                continue

            invoice_line_vals = []
            down_payment_section_added = False
            for line in invoiceable_lines.filtered(lambda x: not x.product_id.is_pack):
                if not down_payment_section_added and line.is_downpayment:
                    # Create a dedicated section for the down payments
                    # (put at the end of the invoiceable_lines)
                    invoice_line_vals.append(
                        (0, 0, order._prepare_down_payment_section_line(
                            sequence=invoice_item_sequence,
                        )),
                    )
                    down_payment_section_added = True
                    invoice_item_sequence += 1
                invoice_line_vals.append(
                    (0, 0, line._prepare_invoice_line(
                        sequence=invoice_item_sequence,
                    )),
                )
                invoice_item_sequence += 1

            bundle_discount = 0

            for line in invoiceable_lines.filtered(lambda x: x.product_id.is_pack):
                quantity_to_invoice = line.qty_delivered - line.qty_invoiced
                bundle_discount += quantity_to_invoice * line.bundle_discount

                if not down_payment_section_added and line.is_downpayment:
                    # Create a dedicated section for the down payments
                    # (put at the end of the invoiceable_lines)
                    invoice_line_vals.append(
                        (0, 0, order._prepare_down_payment_section_line(
                            sequence=invoice_item_sequence,
                        )),
                    )
                    down_payment_section_added = True
                    invoice_item_sequence += 1
                bundle_lines = line._prepare_bundle_invoice_line(
                    sequence=invoice_item_sequence,
                )
                for item in bundle_lines:
                    invoice_line_vals.append(
                        (0, 0, item))

            # suplyd mod :- to add the discount per delivered product
            discount_totals = {}
            account_ids = {}
            for rec in invoiceable_lines:
                if rec.discount_per_line < 0:
                    discount_per_line = rec.discount_per_line / rec.product_uom_qty
                    quantity_to_invoice = rec.qty_delivered - rec.qty_invoiced
                    discount = discount_per_line * quantity_to_invoice
                    tax_id = rec.tax_id.id
                    category_id = rec.product_id.categ_id.id
                    tax_category_id = (tax_id, category_id)
                    if category_id not in account_ids:
                        account_ids[category_id] = rec.product_id.categ_id.promotion_account_id.id
                    if tax_category_id in discount_totals:
                        discount_totals[tax_category_id] += discount
                    else:
                        discount_totals[tax_category_id] = discount
            for (tax_id, category_id), discount in discount_totals.items():
                product_id = self.env['product.product'].search([('is_promo_discount', '=', True)], limit=1)
                invoice_val = {
                    'name': "Discount",
                    'product_id': product_id.id if product_id else False,
                    'price_unit': discount,
                    'product_uom_id': product_id.uom_id.id if product_id else False,
                    'tax_ids': [(4, tax_id)],
                    'quantity': 1,
                }
                if account_ids.get(category_id):
                    invoice_val['account_id'] = account_ids.get(category_id)
                invoice_line_vals.append((0, 0, invoice_val))
                invoice_item_sequence += 1

            if bundle_discount < 0:
                product_id = self.env['product.product'].search([('is_bundle_discount', '=', True)])
                tax_id = self.env['account.tax'].search([('name', '=', 'Exempt'), ('type_tax_use', '=', 'sale')],
                                                        limit=1)
                for line in invoiceable_lines.filtered(lambda x: x.product_id.is_pack):
                    for product in line.product_id.pack_ids:
                        # todo : bundle discount id should be created in the account.move.line to be assigned here
                        quantity_to_invoice = line.qty_delivered - line.qty_invoiced
                        sale_discount_price = (product.product_id.list_price - product.product_pack_discount) * (
                                quantity_to_invoice * product.qty_uom)
                        invoice_line_vals.append((0, 0, {
                            'name': "Bundle Discount %s" % product.product_id.name,
                            'discount_product_id': product.product_id.id if product.product_id else False,
                            'product_id': product_id.id if product_id else False,
                            'price_unit': sale_discount_price * -1,
                            'product_uom_id': product_id.uom_id.id if product_id else False,
                            'tax_ids': [(4, tax_id.id)],
                            'quantity': 1,
                        }))
                        invoice_item_sequence += 1
            invoice_vals['invoice_line_ids'] += invoice_line_vals
            invoice_vals_list.append(invoice_vals)
        if not invoice_vals_list:
            raise self._nothing_to_invoice_error()

        # 2) Manage 'grouped' parameter: group by (partner_id, currency_id).
        if not grouped:
            new_invoice_vals_list = []
            invoice_grouping_keys = self._get_invoice_grouping_keys()
            invoice_vals_list = sorted(
                invoice_vals_list,
                key=lambda x: [
                    x.get(grouping_key) for grouping_key in invoice_grouping_keys
                ]
            )
            for grouping_keys, invoices in groupby(invoice_vals_list,
                                                   key=lambda x: [x.get(grouping_key) for grouping_key in
                                                                  invoice_grouping_keys]):
                origins = set()
                payment_refs = set()
                refs = set()
                ref_invoice_vals = None
                for invoice_vals in invoices:
                    if not ref_invoice_vals:
                        ref_invoice_vals = invoice_vals
                    else:
                        ref_invoice_vals['invoice_line_ids'] += invoice_vals['invoice_line_ids']
                    origins.add(invoice_vals['invoice_origin'])
                    payment_refs.add(invoice_vals['payment_reference'])
                    refs.add(invoice_vals['ref'])
                ref_invoice_vals.update({
                    'ref': ', '.join(refs)[:2000],
                    'invoice_origin': ', '.join(origins),
                    'payment_reference': len(payment_refs) == 1 and payment_refs.pop() or False,
                })
                new_invoice_vals_list.append(ref_invoice_vals)
            invoice_vals_list = new_invoice_vals_list

        # 3) Create invoices.

        # As part of the invoice creation, we make sure the sequence of multiple SO do not interfere
        # in a single invoice. Example:
        # SO 1:
        # - Section A (sequence: 10)
        # - Product A (sequence: 11)
        # SO 2:
        # - Section B (sequence: 10)
        # - Product B (sequence: 11)
        #
        # If SO 1 & 2 are grouped in the same invoice, the result will be:
        # - Section A (sequence: 10)
        # - Section B (sequence: 10)
        # - Product A (sequence: 11)
        # - Product B (sequence: 11)
        #
        # Resequencing should be safe, however we resequence only if there are less invoices than
        # orders, meaning a grouping might have been done. This could also mean that only a part
        # of the selected SO are invoiceable, but resequencing in this case shouldn't be an issue.
        if len(invoice_vals_list) < len(self):
            SaleOrderLine = self.env['sale.order.line']
            for invoice in invoice_vals_list:
                sequence = 1
                for line in invoice['invoice_line_ids']:
                    line[2]['sequence'] = SaleOrderLine._get_invoice_line_sequence(new=sequence,
                                                                                   old=line[2]['sequence'])
                    sequence += 1

        # Manage the creation of invoices in sudo because a salesperson must be able to generate an invoice from a
        # sale order without "billing" access rights. However, he should not be able to create an invoice from scratch.
        moves = self.env['account.move'].sudo().with_context(default_move_type='out_invoice').create(invoice_vals_list)

        # 4) Some moves might actually be refunds: convert them if the total amount is negative
        # We do this after the moves have been created since we need taxes, etc. to know if the total
        # is actually negative or not
        if final:
            moves.sudo().filtered(lambda m: m.amount_total < 0).action_switch_invoice_into_refund_credit_note()
        for move in moves:
            move.message_post_with_view('mail.message_origin_link',
                                        values={'self': move, 'origin': move.line_ids.mapped('sale_line_ids.order_id')},
                                        subtype_id=self.env.ref('mail.mt_note').id
                                        )
        return moves

    def action_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    @api.depends('partner_shipping_id')
    def _compute_out_of_zone(self):
        for rec in self:
            if rec.partner_shipping_id:
                rec.out_of_zone = rec.partner_shipping_id.out_of_zone
            else:
                rec.out_of_zone = False

    def _compute_balance_left(self):
        for rec in self:
            rec.balance_left = rec.use_credit_balance - rec.printed_balance

    def get_formatted_balance(self):
        for rec in self:
            balance_amount = rec.show_balance if rec.show_balance > 0 else rec.balance_left
            balance = balance_amount if not rec.is_use_custom_balance else rec.custom_balance
            return formatLang(self.env, balance, currency_obj=self.company_id.currency_id)

    @api.depends('use_credit_balance', 'reconciled_balance')
    def _compute_balance_credit(self):
        for rec in self:
            rec.balance_credit_balance = rec.use_credit_balance - rec.reconciled_balance

    @api.depends('order_line')
    def _compute_transit_discount(self):
        for rec in self:
            order_line = reduce(
                lambda acc, obj: acc + [obj] if obj.product_id not in map(lambda x: x.product_id, acc) else acc,
                rec.order_line,
                [])
            order_line = [x.id for x in order_line]
            rec.transit_discount = sum(rec.order_line.filtered(lambda x: x.id in order_line).mapped('transit_discount'))

    @api.depends('order_line')
    def _compute_delivered_discount(self):
        for rec in self:
            rec.delivered_discount = sum(rec.order_line.mapped('delivered_discount'))

    @api.depends('state',
                 'order_line',
                 'order_line.price_unit',
                 'order_line.product_id')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_odoo_amount = rec.amount_total
            # rec.send_total_amounts_hasura(rec)

    def send_total_amounts_hasura(self, order_id):
        discount = abs(sum(order_id.order_line.filtered(lambda x: x.price_unit < 0).mapped('price_subtotal')))
        service_charges = sum(
            order_id.order_line.filtered(lambda x: x.product_id.detailed_type == "service" and x.price_unit > 0).mapped(
                'price_subtotal'))
        original_amount = (order_id.amount_untaxed + discount) - service_charges
        query = """
                      mutation UpdateOrders($ref_id:String!,$set: orders_set_input!) {
                          update_orders(where: {ref_id:{_eq:$ref_id}},_set: $set){
                              affected_rows
                              returning{id,odoo_taxed_amt,delivery_notes}
                          }
                      }
                  """
        variables = {
            "ref_id": str(order_id.id),
            "set": {
                'total_amount': order_id.amount_untaxed,
                'discount': discount,
                'odoo_taxed_amt': order_id.amount_tax,
                'odoo_total_after_tax': order_id.amount_total,
                'original_amount': original_amount
            }
        }
        self.run_query(query, variables)

    def get_transit_discount(self):
        for rec in self:
            return formatLang(self.env, rec.transit_discount, currency_obj=self.company_id.currency_id)

    def get_delivered_discount(self):
        for rec in self:
            return formatLang(self.env, rec.delivered_discount, currency_obj=self.company_id.currency_id)

    @api.depends('order_line.tax_id', 'order_line.price_unit', 'amount_total', 'amount_untaxed',
                 'order_line.in_transit_qty')
    def _compute_proforma_tax_totals_json(self):
        def compute_taxes(order_line):
            price = order_line.price_unit * (1 - (order_line.discount or 0.0) / 100.0)
            order = order_line.order_id
            return order_line.tax_id._origin.compute_all(price, order.currency_id, order_line.in_transit_qty,
                                                         product=order_line.product_id,
                                                         partner=order.partner_shipping_id)

        account_move = self.env['account.move']
        for order in self:
            amount_total = sum(order.order_line.filtered(lambda x: x.in_transit_qty > 0).mapped('in_transit_total'))
            amount_subtotal = sum(
                order.order_line.filtered(lambda x: x.in_transit_qty > 0).mapped('in_transit_subtotal'))
            tax_lines_data = account_move._prepare_tax_lines_data_for_totals_from_object(order.order_line,
                                                                                         compute_taxes)
            tax_totals = account_move._get_tax_totals(order.partner_id, tax_lines_data, amount_total, amount_subtotal,
                                                      order.currency_id)
            total_tax = 0
            for item in order.order_line.filtered(
                    lambda x: x.product_id.detailed_type != 'service' and x.product_uom_qty > 0):
                in_transit_tax = (item.price_tax / item.product_uom_qty) * item.in_transit_qty
                total_tax += in_transit_tax + ((item.tax_line / item.product_uom_qty) * item.in_transit_qty)
            if order.transit_discount < 0:
                tax_totals['amount_total'] = ((amount_subtotal + total_tax) + order.transit_discount)
            if order.delivery_charge > 0:
                tax_totals['amount_total'] += order.delivery_charge
            if order.service_charge > 0:
                tax_totals['amount_total'] += order.service_charge
            total_due = tax_totals['amount_total']
            if order.balance_left > 0 or order.show_balance > 0 or order.custom_balance > 0:
                balance_amount = order.show_balance if order.show_balance > 0 else order.balance_left
                balance = balance_amount if not order.is_use_custom_balance else order.custom_balance
                tax_totals['amount_total'] -= balance
            tax_totals['total_amount_with_balance'] = total_due
            tax_totals['formatted_total_due'] = formatLang(self.env, total_due, currency_obj=self.currency_id)
            tax_totals['formatted_amount_total'] = formatLang(self.env, tax_totals['amount_total'],
                                                              currency_obj=self.company_id.currency_id)
            tax_totals['total_tax'] = formatLang(self.env, round(total_tax, 2),
                                                 currency_obj=self.company_id.currency_id)
            order.proforma_tax_totals_json = json.dumps(tax_totals)

    @api.depends('order_line.tax_id', 'order_line.price_unit', 'amount_total', 'amount_untaxed',
                 'order_line.qty_delivered')
    def _compute_proforma_delivered_tax_totals_json(self):
        def compute_taxes(order_line):
            price = order_line.price_unit * (1 - (order_line.discount or 0.0) / 100.0)
            order = order_line.order_id
            return order_line.tax_id._origin.compute_all(price, order.currency_id, order_line.qty_delivered,
                                                         product=order_line.product_id,
                                                         partner=order.partner_shipping_id)

        account_move = self.env['account.move']
        for order in self:
            amount_total = sum(order.order_line.filtered(lambda x: x.qty_delivered > 0).mapped('delivered_total'))
            amount_subtotal = sum(order.order_line.filtered(lambda x: x.qty_delivered > 0).mapped('delivered_subtotal'))
            tax_lines_data = account_move._prepare_tax_lines_data_for_totals_from_object(order.order_line,
                                                                                         compute_taxes)
            tax_totals = account_move._get_tax_totals(order.partner_id, tax_lines_data, amount_total, amount_subtotal,
                                                      order.currency_id)
            total_tax = 0
            for item in order.order_line.filtered(lambda x: x.product_id.detailed_type != 'service'):
                delivered_tax = (item.price_tax / item.product_uom_qty) * item.qty_delivered
                total_tax += delivered_tax + ((item.tax_line / item.product_uom_qty) * item.qty_delivered)
            if order.delivery_charge > 0:
                tax_totals['amount_total'] += order.delivery_charge
            if order.use_credit_balance > 0:
                balance = order.use_credit_balance if not order.is_use_custom_balance else order.custom_balance
                tax_totals['amount_total'] -= balance
            if order.delivered_discount < 0:
                tax_totals['amount_total'] = ((
                                                      amount_subtotal + total_tax) + order.delivered_discount) + order.delivery_charge
            tax_totals['formatted_amount_total'] = formatLang(self.env, tax_totals['amount_total'],
                                                              currency_obj=self.company_id.currency_id)
            tax_totals['total_tax'] = formatLang(self.env, round(total_tax, 2),
                                                 currency_obj=self.company_id.currency_id)
            order.proforma_delivered_tax_totals_json = json.dumps(tax_totals)

    def so_hasura_total_amount_verification(self):
        graphql = self.env['ir.config_parameter'].sudo().get_param('hasura')
        admin_secret = self.env['ir.config_parameter'].sudo().get_param('adminsecret')
        if not graphql or not admin_secret:
            raise UserError(_("Hasura not configured. Please contact administrator"))
        client = GraphQLClient(graphql)
        client.inject_token(admin_secret, 'x-hasura-admin-secret')
        query = """  
                  query SaleOrderAmount($ref_id: String!) {
                    orders(where:{ref_id:{_eq:$ref_id} }){
                      total_amount
                    }
                  }

                          """
        variables = {
            "ref_id": str(self._origin.id)
        }
        try:
            data = client.execute(query, variables)
        except URLError:
            raise UserError(_("Something is wrong with the backend. Please try again after sometime."))
        orders = json.loads(data)
        branch_data = orders.get('data').get('orders')
        if branch_data:
            total_amount = branch_data[0].get("total_amount")
            amount_untaxed = self.amount_untaxed
            if total_amount:
                if not float_compare(total_amount, amount_untaxed, precision_rounding=2) == 0:
                    message = f"Warning: Untaxed total amount in website is equal to {total_amount} and this is not Equal to Amount {amount_untaxed} in Odoo"
                    self.message_post(body=message)

    def action_check_address(self, field, address_type):
        if field:
            if address_type == 'delivery':
                if not field.type == address_type:
                    raise ValidationError(_("Delivery address has to be a branch!"))
            else:
                if not (field.type == address_type and field.company_type == 'company'):
                    raise ValidationError(_("Partner has to be an entity"))

    def check_order_in_hasura(self):
        query = """
                      query getOrderId( $ref_id: String! ){
                          orders(where:{ ref_id:{_eq:$ref_id } }){
                              id
                          }
                      }
                  """
        variables = {
            "ref_id": str(self.id)
        }
        try:
            data = self.run_query(query, variables, return_data=True, delivery=False)
        except URLError:
            raise UserError(_("Something is wrong with the backend. Please try again after sometime."))
        order_id = json.loads(data)
        order_data = order_id.get('data').get('orders')
        if not order_data:
            self.sync_data()

    def action_confirm(self):
        self.action_check_address(self.partner_invoice_id, 'invoice')
        self.action_check_address(self.partner_shipping_id, 'delivery')
        if not self.env.context.get('is_api'):
            message = self.validate_customer_data()
            if message:
                raise UserError(_(message))
        res = super(SaleOrder, self).action_confirm()
        if not self.env.context.get('is_api') and not self.is_backend_order:
            self.create_sale_order_new_backend()
        elif not self.env.context.get('is_api') and self.is_backend_order:
            self.update_order_after_confirmed()
        for rec in self.picking_ids.filtered(
            lambda x: x.picking_type_id.is_out_delivery and x.state not in ['cancel', 'done']):
            rec.sync_to_delivery()
        return res
    
    def action_draft(self):
        res = super(SaleOrder, self).action_draft()
        for record in self:
            record.delete_sale_order()
        return res

    def action_cancel(self):
        self.update_cancel_order()
        res = super(SaleOrder, self).action_cancel()
        activty_type = self.env.ref('mail.mail_activity_data_todo')
        for rec in self:
            if rec.credit_note_ids:
                rec.action_cancel_credit()
            po_ids = rec._get_purchase_orders()
            if po_ids:
                for po_id in po_ids:
                    if po_id.state in ['draft', 'sent']:
                        for sale_line in rec.order_line:
                            for purchase_line in po_id.order_line:
                                if sale_line.product_id.id == purchase_line.product_id.id:
                                    purchase_line.product_qty = purchase_line.product_qty - sale_line.product_qty
                        purchase_order_qty = sum(po_id.order_line.mapped('product_qty'))
                        if purchase_order_qty <= 0:
                            po_id.button_cancel()
                    else:
                        self.env['mail.activity'].sudo().create({
                            'summary': 'Sale Order Canceled',
                            'activity_type_id': activty_type.id,
                            'res_model_id': self.env['ir.model']._get_id('purchase.order'),
                            'res_id': po_id,
                            'user_id': po_id.user_id.id,
                        })
            if self.state == 'cancel' and self.picking_ids:
                return_picking_ids = self.picking_ids.mapped('return_picking_id').mapped('id')
                for picking_id in self.picking_ids.filtered(
                        lambda x: not x.return_picking_id and x.id not in return_picking_ids and x.state == 'done'):
                    stock_return_wizard = self.env['stock.return.picking'].with_context(
                        active_ids=[picking_id.id], active_id=picking_id.id, active_model=picking_id._name).create(
                        {'picking_id': picking_id.id})
                    stock_return_wizard._onchange_picking_id()
                    stock_return_wizard.create_returns()
        return res

    def sync_data(self):
        for rec in self:
            rec.create_sale_order_new_backend()

    # TODO: relocate this method to a better place
    def add_entity(self, client, entity_id, product_id):
        e_product_query = """
                          mutation addEntityProduct($e_ref_id: String!, $p_ref_id: String!) {
                              addEntityProduct(object: {add_entity_product_data: {entity_id: $e_ref_id, product_id: $p_ref_id}}) {
                                  entity_product_id
                              }
                          }
                      """
        e_product_variable = {
            'e_ref_id': str(entity_id),
            'p_ref_id': str(product_id)
        }
        entity_product_data = client.execute(e_product_query, e_product_variable)
        return entity_product_data

    def sync_order_lines(self):
        graphql = self.env['ir.config_parameter'].sudo().get_param('hasura')
        admin_secret = self.env['ir.config_parameter'].sudo().get_param('adminsecret')
        if not graphql or not admin_secret:
            raise UserError(_("Hasura not configured. Please contact administrator"))
        client = GraphQLClient(graphql)
        client.inject_token(admin_secret, 'x-hasura-admin-secret')
        updates = []
        lines = self.env['sale.order.line'].search([])
        for rec in lines.filtered(lambda x: x.product_id.detailed_type == 'product'):
            shipment_ref_id = rec.x_studio_stock_moves.filtered(lambda x: x.picking_type_id.is_out_delivery and x.state != 'cancel')
            if shipment_ref_id:
                update_object = {
                    "where": {
                        "ref_id": {
                            "_eq": str(rec.id)
                        }
                    },
                    "_set": {
                        "shipment_ref_id": shipment_ref_id[0]
                    }
                }
                updates.append(update_object)
        query = """
              mutation saleOrderLinesUpdates($updates: [order_line_updates!]!) {
              update_order_line_many(updates: $updates) {
                  affected_rows
                  returning {
                  id
                  }
              }
              }
          """
        variables = {
            "updates": updates
        }
        self.run_query(query, variables)

    def sync_orders(self):
        graphql = self.env['ir.config_parameter'].sudo().get_param('hasura')
        admin_secret = self.env['ir.config_parameter'].sudo().get_param('adminsecret')
        if not graphql or not admin_secret:
            raise UserError(_("Hasura not configured. Please contact administrator"))
        client = GraphQLClient(graphql)
        client.inject_token(admin_secret, 'x-hasura-admin-secret')
        updates = []
        lines = self.env['sale.order'].search([])
        for rec in lines:
            discount = abs(sum(rec.order_line.filtered(lambda x: x.price_unit < 0).mapped('price_total')))
            update_object = {
                "where": {
                    "ref_id": {
                        "_eq": str(rec.id)
                    }
                },
                "_set": {
                    'total_amount': rec.amount_untaxed,
                    'discount': discount,
                    'odoo_taxed_amt': rec.amount_tax,
                    'odoo_total_after_tax': rec.amount_total,
                }
            }
            updates.append(update_object)
        query = """
              mutation saleOrderLinesUpdates($updates: [orders_updates!]!) {
              update_orders_many(updates: $updates) {
                  affected_rows
                  returning {
                  id
                  }
              }
              }
          """
        variables = {
            "updates": updates
        }
        self.run_query(query, variables)

    def recompute_coupon_lines(self):
        for order in self.filtered(lambda x: x.state != 'cancel'):
            order._remove_invalid_reward_lines()
            programs = order._get_applicable_no_code_promo_program()
            if programs:
                if order.state != 'cancel':
                    order._create_new_no_code_promo_reward_lines_multy_so(programs.ids)
                order._update_existing_reward_lines()
                self.add_promo(order, [str(rec.id) for rec in programs])

    def add_promo(self, order, program):
        graphql = self.env['ir.config_parameter'].sudo().get_param('hasura')
        admin_secret = self.env['ir.config_parameter'].sudo().get_param('adminsecret')
        if not graphql or not admin_secret:
            raise UserError(_("Hasura not configured. Please contact administrator"))
        client = GraphQLClient(graphql)
        client.inject_token(admin_secret, 'x-hasura-admin-secret')
        # query = """
        #       mutation odoo_addPromotionsToOrder($object: odoo_updateOrderWithPromoCodeInput!) {
        #           odoo_updateOrderWithPromoCode(object: $object) {
        #               message
        #               ok
        #           }
        #       }
        #   """
        # object = {
        #     'order_ref_id': str(order.id),
        #     'promo_ref_ids': program
        # }
        # variables = {
        #     "object": object
        # }
        # self.run_query(query, variables)

    def _get_reward_values_fixed_amount(self, program):
        discount_amount = self._get_reward_values_discount_fixed_amount(program)

        # In case there is a tax set on the promotion product, we give priority to it.
        # This allow manual overwrite of taxes for promotion.
        if program.discount_line_product_id.taxes_id:
            line_taxes = self.fiscal_position_id.map_tax(
                program.discount_line_product_id.taxes_id) if self.fiscal_position_id else program.discount_line_product_id.taxes_id
            lines = self._get_base_order_lines(program)
            discount_amount = min(
                sum(lines.mapped(lambda l: l.price_reduce * l.product_uom_qty)), discount_amount
            )
            return [{
                'name': _("Discount: %s", program.name),
                'product_id': program.discount_line_product_id.id,
                'price_unit': -discount_amount,
                'product_uom_qty': 1.0,
                'product_uom': program.discount_line_product_id.uom_id.id,
                'is_reward_line': True,
                'tax_id': [(4, tax.id, False) for tax in line_taxes],
                'is_coupon': True,
            }]

        lines = self._get_paid_order_lines()
        # Remove Free Lines
        lines = lines.filtered('price_reduce')
        reward_lines = {}

        tax_groups = set([line.tax_id for line in lines])
        max_discount_per_tax_groups = {tax_ids: self._get_max_reward_values_per_tax(program, tax_ids) for tax_ids in
                                       tax_groups}

        for tax_ids in sorted(tax_groups, key=lambda tax_ids: max_discount_per_tax_groups[tax_ids], reverse=True):

            if discount_amount <= 0:
                return reward_lines.values()

            curr_lines = lines.filtered(lambda l: l.tax_id == tax_ids)
            lines_price = sum(curr_lines.mapped(lambda l: l.price_reduce * l.product_uom_qty))
            lines_total = sum(lines.mapped('price_subtotal'))

            discount_line_amount_price = min(max_discount_per_tax_groups[tax_ids],
                                             (discount_amount * lines_price / lines_total))

            if not discount_line_amount_price:
                continue

            reward_lines[tax_ids] = {
                'name': _(
                    "Discount: %(program)s - On product with following taxes: %(taxes)s",
                    program=program.name,
                    taxes=", ".join(tax_ids.mapped('name')),
                ),
                'product_id': program.discount_line_product_id.id,
                'price_unit': -discount_line_amount_price,
                'product_uom_qty': 1.0,
                'product_uom': program.discount_line_product_id.uom_id.id,
                'is_reward_line': True,
                'tax_id': [(4, tax.id, False) for tax in tax_ids],
                'is_coupon': True,
            }
        return reward_lines.values()

    def update_language(self):
        move_ids = self.env['account.move'].search([('move_type', '=', 'out_invoice')])
        for move in move_ids:
            move.lang = 'ar_001'
        order_ids = self.env['sale.order'].search([])
        for order in order_ids:
            order.lang = 'ar_001'

    def action_mark_as_delivered(self):
        return {
            'name': _('Mark as done'),
            'type': 'ir.actions.act_window',
            'target': 'new',
            'view_mode': 'form',
            'res_model': 'mark.delivered.wizard',
        }

    def sync_balance_hasura(self, client):
        credit = self.partner_id.credit
        if credit < 0:
            credit = abs(round(credit, 2))
        else:
            credit = 0
        query = """
              mutation UpdateEntity($ref_id:String!,$set: entity_set_input!) {
                  update_entity(where: {ref_id:{_eq:$ref_id}},_set: $set){
                      affected_rows
                      returning{id}
                  }
              }
          """
        set = {
            'credit_balance': credit
        }
        variables = {
            "ref_id": str(self.partner_id.id),
            "set": set
        }
        try:
            r = client.execute(query, variables)
            a = 0
        except URLError:
            raise UserError(_("Something is wrong with the backend. Please try again after sometime."))
        _logger.info(r)
        if "error" in r:
            errors = json.loads(r).get('errors')
            if errors:
                message = errors[0].get('message')
                extension = errors[0].get('extensions')
                path = False
                if extension:
                    path = extension.get('path')
                error_message = "Issue sending data to the backend. Please contact the Administrator.\n"
                if message and path:
                    error_message = f"{error_message}Error message: {message}\nPath: {path}"
                elif message:
                    error_message = f"{error_message}\nError message : {message}"
                elif path:
                    error_message = f"{error_message}\nPath: {path}"
                _logger.exception(error_message)
                raise UserError(_(error_message))

    def sync_is_used_balance(self):
        order_ids = self.search([('use_credit_balance', '>', 0)])
        for order in order_ids:
            order.is_use_credit_balance = True

    def update_wallet_balance(self):
        lines = self.env['res.partner'].search([('type', '=', 'invoice')])
        updates = []
        for rec in lines:
            update_object = {
                "where": {
                    "ref_id": {
                        "_eq": str(rec.id)
                    }
                },
                "_set": {
                    'credit_balance': rec.wallet_balance
                }
            }
            updates.append(update_object)
        query = """
                      mutation entityUpdates($updates: [entity_updates!]!) {
                      update_entity_many(updates: $updates) {
                          affected_rows
                          returning {
                          id
                          }
                      }
                      }
                  """
        variables = {
            "updates": updates
        }
        self.run_query(query, variables)

    # get the number of sale orders for the current customer
    @api.depends('partner_id')
    def _compute_past_number_of_orders(self):
        for order in self:
            if order.partner_id:
                order.past_number_of_orders = self.env['sale.order'].search_count(
                    [('partner_id', '=', order.partner_id.id), ('id', '!=', order.id or order._origin.id),
                     ('state', '!=', 'cancel')])
            else:
                order.past_number_of_orders = 0

    @api.onchange('credit_note_ids')
    def _onchange_credit_note_ids(self):
        for rec in self:
            credit_note_ids = []
            amount = 0
            for item in rec.credit_note_ids:
                if item.credit_note_id.id not in credit_note_ids:
                    amount += item.amount
            rec.use_credit_balance = amount

    def _get_applicable_credit_notes(self):
        """Fetches credit notes eligible for cashback application."""
        cashback_journal = self.env['account.journal'].search([('is_cashback', '=', True)], limit=1)
        if not cashback_journal:
            raise UserError(_("No journal found for cashback, please contact the administrator."))

        credit_notes = self.env['account.move'].search([
            ('partner_id', '=', self.partner_id.id),
            ('journal_id', '=', cashback_journal.id),
            ('state', '=', 'posted'),
            ('amount_residual', '>', 0),
            ('id', 'not in', self.credit_note_ids.mapped('credit_note_id').ids),  # Exclude already used credit notes
        ])
        credit_notes = credit_notes.filtered(lambda x: x.balance_credit_amount > 0)
        return credit_notes.sorted(key=lambda x: x.amount_residual, reverse=True)

    def action_add_credit_notes(self):
        """Applies available credit notes to the current record."""
        """Applies available credit notes to the current record."""
        if self.state != 'sale':
            raise UserError(_("Please add the credit notes after order confirmation"))
        remaining_amount_to_apply = self.amount_total - sum(self.credit_note_ids.mapped('amount'))
        if remaining_amount_to_apply > 0:
            credit_note_lines = []
            query = """
                    mutation UseCreditBalance($object:RefIdInputDTO!){
                            useCreditBalanceOnOrder(input:$object){
                            credit_notes{
                                ref_id
                                amount_used_by_order
                                }
                    }
                    }
            """
            variables = {"object":{"ref_id": self.id}}
            return_data = self.run_query(query, variables,True,True)
            if return_data:
                return_data = json.loads(return_data)
                applicable_credit_notes = return_data.get('data').get('useCreditBalanceOnOrder').get('credit_notes')
                if applicable_credit_notes:
                    for credit_note in applicable_credit_notes:
                        account_record = self.env['account.move'].search([('id', '=', credit_note['ref_id'])])
                        amount_to_apply = credit_note['amount_used_by_order']
                        credit_note_lines.append((0, 0, {
                            'credit_note_id': account_record.id,
                            'amount': amount_to_apply
                        }))
                        account_record.sudo().used_amount = account_record.used_amount + amount_to_apply
                    if credit_note_lines:
                        self.credit_note_ids = credit_note_lines

    def _get_order_from_hasura(self):
        query = """
            query getOrderId( $ref_id: String! ){
                orders(where:{ ref_id:{_eq:$ref_id } }){
                    id
                }
            }
        """
        variables = {"ref_id": str(self.id)}
        data = self.run_query(query, variables, True)
        order_data = json.loads(data)
        order = order_data.get("data").get("orders")
        if not order:
            raise UserError(_(f"Order not found {self.id}"))
        return order[0].get("id")

    def _get_credit_notes_from_hasura(self, credit_note_id):
        query = """
            query getCreditNoteId( $ref_id: String! ){
                credit_notes(where:{ ref_id:{_eq:$ref_id } }){
                    id
                    amount_used
                }
            }
        """
        variables = {"ref_id": str(credit_note_id)}
        data = self.run_query(query, variables, True)
        credit_note_data = json.loads(data)
        credit_note = credit_note_data.get("data").get("credit_notes")
        if not credit_note:
            raise UserError(_(f"Credit note not found in hasura {credit_note_id}"))
        return credit_note[0].get("id"), credit_note[0].get("amount_used")

    def _update_credit_note_on_hasura(self, credit_note_id, amount):
        query = """
            mutation UpdateCreditNotes($ref_id:String!,$set: credit_notes_set_input!) {
                update_credit_notes(where: {ref_id:{_eq:$ref_id}},_set: $set){
                    affected_rows
                    returning{id}
                }
            }
        """
        variables = {
            "ref_id": str(credit_note_id),
            "set": {
                'amount_used': amount
            }
        }
        self.run_query(query, variables)

    def _delete_credit_note_on_hasura(self, credit_note_id, order_id):
        query = """ 
                mutation DeleteCreditNotes($condition: credit_notes_and_orders_bool_exp!) {
                    delete_credit_notes_and_orders(where: $condition) {
                        affected_rows
                        returning {
                            id
                        }
                    }
                }
            """
        variables = {
            "condition": {
                "_and": [
                    {"order_id": {"_eq": order_id}},
                    {"credit_note_id": {"_eq": credit_note_id}}
                ]
            }
        }
        self.run_query(query, variables)

    def update_credit_on_hasura(self, credit_note_id, amount):
        order = self._get_order_from_hasura()
        credit_note, amount_used = self._get_credit_notes_from_hasura(credit_note_id)
        amount += amount_used
        self._update_credit_note_on_hasura(credit_note_id, amount)
        query = """
            mutation CreateCreditNotes($object: credit_notes_and_orders_insert_input!) {
                insert_credit_notes_and_orders_one(object: $object){
                    id
                }
            }
        """
        variables = {
            "object": {
                'amount': amount,
                "credit_note_id": credit_note,
                "order_id": order
            }
        }
        self.run_query(query, variables)

    def get_timeout_hours(self):
        return self.company_id.timeout_hours

    @api.onchange('payment_method')
    def _onchange_payment_method(self):
        if self.payment_method not in ['pos', 'online']:
            for line in self.order_line:
                if line.product_id.is_online_charge:
                    line.unlink()

    def action_cancel_credit(self):
        for credit_note_line in self.credit_note_ids:
            credit_note_id = credit_note_line.credit_note_id.id
            amount_to_subtract = credit_note_line.amount
            credit_note = self.env['account.move'].browse(credit_note_id)
            used_amount = max(0, credit_note.used_amount - amount_to_subtract)
            credit_note.sudo().used_amount = used_amount
            query = self.prepare_query("updateCreditNote","UpsertCreditNoteDTO!","input","ok")
            variables = {
                "object": {
                    'entity_ref_id': credit_note.partner_id.id,
                    'credit_note':{
                        "used_amount": used_amount,
                        "ref_id": credit_note.id,
                        "original_total_amount": credit_note.amount_total,
                        "reversed": False
                    }
                }
            }
            self.run_query(query, variables, delivery=True)
        self.credit_note_ids = False

    def subtract_amount_from_hasura(self, credit_note_id, amount):
        credit_note_hasura_id, amount_used = self._get_credit_notes_from_hasura(credit_note_id)
        new_amount_used = max(0, amount_used - amount)
        order_id = self._get_order_from_hasura()
        self._update_credit_note_on_hasura(credit_note_id, new_amount_used)
        self._delete_credit_note_on_hasura(credit_note_hasura_id, order_id)

    def _compute_shipment_count(self):
        for order in self:
            order.shipment_count = self.env['stock.picking'].search_count(
                [('sale_id', '=', order.id), ('picking_type_code', '=', 'outgoing'),
                 ('backorder_id', '=', False)])

    @api.depends('order_line','delivery_status','picking_ids.state')
    def _compute_remaining_order_line_ids(self):
        for order in self:
            vals = []
            for line in order.order_line.filtered(lambda x: x.product_id.detailed_type == 'product'):
                remaining_qty = line.product_uom_qty - (line.qty_delivered + line.in_transit_qty)
                if remaining_qty:
                    if line.product_id.id in order.remaining_order_line_ids.mapped('product_id').ids:
                        remaining_id = order.remaining_order_line_ids.filtered(lambda x: x.product_id.id == line.product_id.id)
                        remaining_id.remaining_qty = remaining_qty
                    else:
                        vals.append((0, 0, {
                            'product_id': line.product_id.id,
                            'remaining_qty': remaining_qty,
                            'order_id': order.id,
                        }))
                else:
                    if line.product_id.id in order.remaining_order_line_ids.mapped('product_id').ids:
                        remaining_id = order.remaining_order_line_ids.filtered(lambda x: x.product_id.id == line.product_id.id)
                        remaining_id.unlink()
            order.remaining_order_line_ids = vals if vals else order.remaining_order_line_ids
    
    def action_cancel_wizard(self):
        return {
            'name': _('Cancel Sales Order'),
            'view_mode': 'form',
            'res_model': 'cancel.order.wizard',
            'view_id': self.env.ref('sale_addons.cancel_order_wizard_view').id,
            'type': 'ir.actions.act_window',
            'context': {'default_order_id': self.id},
            'target': 'new'
        }

    def action_check_deadline_status(self):
        today = datetime.today()
        today_min = datetime.combine(today, today.time().min)
        today_max = datetime.combine(today, today.time().max)
        picking_id = self.env['stock.picking'].search([("date_deadline", ">=", today_min),("date_deadline", "<=", today_max)])
        if picking_id:
            for move_id in picking_id.move_ids_without_package:
                sale_line_id = self.env['sale.order.line'].browse(move_id.sale_line_id.id)
                if sale_line_id.reserved_qty >0 and sale_line_id.reserved_qty < sale_line_id.product_uom_qty:
                    query = """
                                mutation UpdateOrderLine($ref_id:String!,$set: order_line_set_input!) {
                                    update_order_line(where: {ref_id:{_eq:$ref_id}},_set: $set){
                                        affected_rows
                                        returning{id}
                                    }
                                }
                            """
                    variables = {
                        "ref_id": str(sale_line_id.id),
                        "set": {
                            "status": 'READY'
                        }
                    }
                    self.run_query(query, variables)

    @api.depends('order_line','order_line.reserved_qty','order_line.move_ids','picking_ids.state')
    def _compute_reserved_line_status(self):
        for rec in self:
            for line in rec.order_line.filtered(lambda x: x.product_id.type == 'product'):
                if line.in_transit_qty == 0 and line.qty_delivered < line.product_uom_qty:
                    if line.reserved_qty > 0 and line.reserved_qty >= line.product_uom_qty:
                        line.status = 'READY'
                    else:
                        line.status = ''
                    # if line.status in ['READY']:
                        # update_shipment 
                        # query = """
                        #                     mutation UpdateOrderLine($ref_id:String!,$set: order_line_set_input!) {
                        #                         update_order_line(where: {ref_id:{_eq:$ref_id}},_set: $set){
                        #                             affected_rows
                        #                             returning{id}
                        #                         }
                        #                     }
                        #                 """
                        # variables = {
                        #     "ref_id": str(line.id),
                        #     "set": {
                        #         "status": line.status
                        #     }
                        # }
                        # self.run_query(query, variables)
            rec.line_status = ''

    def action_resync_invoices(self):
        six_months_ago = datetime.today() - timedelta(days=180)
        sale_orders = self.search([('date_order', '>=', six_months_ago)])
        for order in sale_orders:
            query = """
                        mutation ResyncOrderInvoices($input: [OdooResyncOrdersInvoicesInput!]!) {
                            odooResyncOrdersInvoices(input: $input) {
                                ok
                                status
                            }
                        }
            """
            invoice_ids = order.invoice_ids.ids
            invoices = self.env['account.move'].search([('id', 'in', invoice_ids)])
            invoice_data = []
            for invoice in invoices:
                invoice_data.append({
                    "ref_id": invoice.id,
                    "invoice_number": invoice.name,
                    "paid_amount": invoice.amount_total if invoice.payment_state == 'paid' else 0,
                    "total_amount": invoice.amount_total,
                    "finalised_date": invoice.invoice_date.strftime('%Y-%m-%d') if invoice.invoice_date else None,
                    "due_date": invoice.invoice_date_due.strftime('%Y-%m-%d') if invoice.invoice_date_due else None,
                    "status": "PAID" if invoice.payment_state == 'paid' else "NOT_PAID",
                    "invoice_url": order._get_pdf_url(invoice.id)
                })
            order_data = {"order_ref_id": order.id, "invoices":invoice_data}
            variables = {"input": [order_data]}
            order.run_query(query, variables)

    def _get_pdf_url(self,invoice_id):
        report = self.env.ref('sp_reports.sp_reports_report_action')._render_qweb_pdf(invoice_id)
        report_file = base64.b64encode(report[0])
        key = self.env['ir.config_parameter'].sudo().get_param('aws_access_key_id')
        secret = self.env['ir.config_parameter'].sudo().get_param('aws_secret_access_key')
        if not key or not secret:
            raise UserError(_("Aws credentials not provided, please contact administrator"))
        image_url = s3.upload_base64_file(str(report_file)[1:], key, secret, True)
        return image_url

