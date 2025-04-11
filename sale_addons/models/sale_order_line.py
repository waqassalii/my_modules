from urllib.error import URLError

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
from graphqlclient import GraphQLClient
import logging
import json

_logger = logging.getLogger(__name__)


class SaleOrderLine(models.Model):
    _name = 'sale.order.line'
    _inherit = ['sale.order.line', 'hasura.mixin']

    bundle_discount = fields.Float(string="Bundle Discount", compute='_compute_bundle_discount')
    expected_delivery_date = fields.Date('Expected Delivery Date')
    is_package_discount = fields.Boolean('Package Discount')
    is_api = fields.Boolean(string="Api", copy=False)
    is_in_transit = fields.Boolean(string="In Transit", compute='_compute_in_transit')
    in_transit_qty = fields.Float(string="In Transit Quantity", compute='_compute_transit_qty')
    in_transit_tax = fields.Float(string="In Transit Tax", compute='_compute_transit_amount')
    in_transit_total = fields.Float(string="In Transit Total", compute='_compute_transit_amount')
    in_transit_subtotal = fields.Float(string="In Transit Subtotal", compute='_compute_transit_amount')
    delivered_tax = fields.Float(string="Delivered Tax", compute='_compute_delivered_amount')
    delivered_total = fields.Float(string="Delivered Total", compute='_compute_delivered_amount')
    delivered_subtotal = fields.Float(string="Delivered Subtotal", compute='_compute_delivered_amount')
    status = fields.Char(string="Status", default='In Preparation', readonly=True)
    discount_per_line = fields.Float(string="Discount Per Line", compute='_compute_discount_line')
    tax_line = fields.Float(string="Tax Per Line", compute='_compute_discount_line')
    transit_discount = fields.Float(string="Transit Discount", compute='_compute_transit_discount')
    delivered_discount = fields.Float(string="Transit Discount", compute='_compute_delivered_discount')
    discount_product_id = fields.Many2one('product.product', string="Discount Product")
    discount_product_ids = fields.Many2many('product.product', string="Discount Products")
    reserved_qty = fields.Float("Reserved Quantity", compute='_compute_reserved_qty')
    margin = fields.Float("Margin", compute='_compute_margin',
                          digits='Product Price', store=True, groups="sale_addons.group_margin_access")
    margin_percent = fields.Float(
        "Margin (%)", compute='_compute_margin', store=True, groups="sale_addons.group_margin_access",
        group_operator="avg")
    purchase_price = fields.Float(
        string='Cost', compute="_compute_purchase_price",
        digits='Product Price', store=True, readonly=False,
        groups="sale_addons.group_margin_access")
    # discount_line_specific = fields.Float(string="Discount Specific", compute='_compute_discount_line_specific')
    # discount_line_normal = fields.Float(string="Discount Normal", compute='_compute_discount_line_normal')
    is_pos_charge = fields.Boolean(string="Is pos charge", copy=False)
    product_specific_discount = fields.Float(string="Product Specific Discount",
                                             compute='_compute_product_specific_discount',store=True)
    total_order_discount = fields.Float(string='Total Order Discount', compute='_compute_total_order_discount',store=True)
    is_coupon = fields.Boolean(string="Is Coupon")
    coupon_per_line = fields.Float(string="Coupon Per Line")
    shipment_id = fields.Char(string="Shipment ID")
    shipment_line_id = fields.Char(string="Shipment line id")
    promo_ids = fields.Many2many('coupon.program', string="Promo ids", compute='_compute_promo_ids')
    payment_term_charge = fields.Float(string="Payment term charge")
    promo_applied_json = fields.Char(string="Promo applied data", compute="_compute_total_order_discount")
    margin_per_unit = fields.Float(compute='_compute_margin_per_unit', string='Margin Per Unit')
    total_margin = fields.Float(compute='_compute_margin_per_unit', string='Total Margin')
    opex_cost_line = fields.Float(string='OPEX Cost (Line)', compute='_compute_opex_cost_line')

    @api.depends('price_unit', 'discount', 'product_uom_qty', 'product_id')
    def _compute_margin_per_unit(self):
        for line in self:
            if line.product_uom_qty > 0 and line.price_subtotal > 0:
                discount =  line.total_order_discount + line.product_specific_discount
                sale_price = line.price_unit - (abs(discount)/line.product_uom_qty)
                cost = line.product_id.standard_price
                margin = sale_price - cost
                line.margin_per_unit = round(margin,2) or 0
                line.total_margin = (margin * line.product_uom_qty) or 0
            else:
                line.margin_per_unit = 0 
                line.total_margin = 0

    @api.depends('product_id', 'product_uom_qty')
    def _compute_opex_cost_line(self):
        for line in self:
            opex_cost_line = line.product_id.opex_cost * line.product_uom_qty if line.product_id else 0
            line.opex_cost_line = max(opex_cost_line,0)

    def _compute_promo_ids(self):
        for rec in self:
            rec.promo_ids = rec.order_id.no_code_promo_program_ids\
                .filtered(lambda x: x.discount_apply_on == 'on_order' 
                    or rec.product_id in x.discount_specific_product_ids)

    def _compute_bundle_discount(self):
        for rec in self:
            if rec.product_id.is_pack:
                pack_price = 0
                for item in rec.product_id.pack_ids:
                    price_unit = item.product_id.list_price
                    tax = item.product_id.taxes_id[0].amount if item.product_id.taxes_id else False
                    if tax and tax > 0:
                        price_unit += (tax / 100) * price_unit
                    pack_price += (price_unit * item.qty_uom)
                rec.bundle_discount = rec.price_unit - pack_price
            else:
                rec.bundle_discount = 0

    def _prepare_bundle_invoice_line(self, sequence):
        """
        Prepare the dict of values to create the new invoice line for a sales order line.

        :param qty: float quantity to invoice
        :param optional_values: any parameter that should be added to the returned invoice line
        """
        lines = []
        for rec in self.product_id.pack_ids:
            res = {
                'display_type': self.display_type,
                'sequence': sequence,
                'name': rec.product_id.name,
                'product_id': rec.product_id.id,
                'product_uom_id': rec.product_id.uom_id,
                'quantity': self.qty_to_invoice * rec.qty_uom,
                'discount': self.discount,
                'price_unit': rec.product_id.list_price,
                'tax_ids': [(6, 0, rec.product_id.taxes_id.ids)],
                'sale_line_ids': [(4, self.id)],
            }
            if self.order_id.analytic_account_id and not self.display_type:
                res['analytic_account_id'] = self.order_id.analytic_account_id.id
            if self.analytic_tag_ids and not self.display_type:
                res['analytic_tag_ids'] = [(6, 0, self.analytic_tag_ids.ids)]
            if self.display_type:
                res['account_id'] = False
            sequence += 1
            lines.append(res)
        return lines

    @api.depends('invoice_lines.move_id.state', 'invoice_lines.quantity', 'untaxed_amount_to_invoice')
    def _compute_qty_invoiced(self):
        for line in self:
            qty_invoiced = 0.0
            for invoice_line in line._get_invoice_lines():
                if invoice_line.move_id.state != 'cancel' or invoice_line.move_id.payment_state == 'invoicing_legacy':
                    qty = invoice_line.product_uom_id._compute_quantity(invoice_line.quantity, line.product_uom,
                                                                        raise_if_failure=False)
                    if line.product_id.is_pack:
                        if line.product_id.pack_ids and not line.order_id.is_old_bundle_order:
                            # Divide by the sum of the quantity of picking list of the product
                            qty /= sum(line.product_id.pack_ids.mapped('qty_uom'))
                    if invoice_line.move_id.move_type == 'out_invoice':
                        qty_invoiced += qty
                    elif invoice_line.move_id.move_type == 'out_refund':
                        qty_invoiced -= qty
            line.qty_invoiced = qty_invoiced

    @api.constrains('product_id')
    def _check_min_order_qty(self):
        for rec in self:
            if rec.product_id.min_order_quantity > 0 and (rec.product_uom_qty < rec.product_id.min_order_quantity):
                raise UserError(f'You ordered less than minimum order qty of product: {rec.product_id.name}!')

    def _get_start_date(self, interval):
        today = datetime.today()
        interval_mapping = {
            'DAILY': today,
            'WEEKLY': today - timedelta(days=7),
            'BI_WEEKLY': today - timedelta(days=15),
            'MONTHLY': today - timedelta(days=30),
        }
        return datetime.combine(interval_mapping.get(interval, today), today.time().min)

    def _check_max_order_interval(self, product_id, max_order_interval, partner_id, product_qty):
        start_date = self._get_start_date(max_order_interval)
        sale_order_line = self.env['sale.order.line'].search([
            ('order_id.partner_id', '=', partner_id),
            ('product_id', '=', product_id),
            ('state', '!=', 'cancel'),
            ('order_id.date_order', '>=', start_date),
        ])
        if sale_order_line:
            product_uom_qty = sum(sale_order_line.mapped('product_uom_qty'))
            if product_uom_qty > product_qty:
                return True

    @api.constrains('order_id.partner_id', 'product_id')
    def _check_max_order_qty_and_interval(self):
        for rec in self:
            if rec.product_id.max_order_quantity > 0 and rec.product_id.max_order_interval:
                max_interval = self._check_max_order_interval(rec.product_id.id, rec.product_id.max_order_interval,
                                                            rec.order_id.partner_id.id,
                                                            rec.product_id.max_order_quantity)
                if max_interval:
                    raise UserError(f'Maximum order Qty reached within interval of Products: {self.product_id.name}!')

    def _compute_reserved_qty(self):
        for rec in self:
            moves = rec.x_studio_stock_moves.filtered(lambda x: x.picking_id.picking_type_id.barcode == 'WH-INTERNAL')
            rec.reserved_qty = sum(moves.mapped('reserved_availability'))

    @api.depends('order_id.order_line', 'price_unit', 'product_uom_qty')
    def _compute_transit_discount(self):
        for rec in self:
            if rec.discount_per_line < 0 and rec.product_uom_qty > 0:
                rec.transit_discount = (rec.discount_per_line / rec.product_uom_qty) * rec.in_transit_qty
            else:
                rec.transit_discount = 0

    @api.depends('order_id.order_line', 'price_unit', 'product_uom_qty')
    def _compute_delivered_discount(self):
        for rec in self:
            if rec.discount_per_line < 0:
                quantity = rec.qty_delivered if rec.order_id.is_confirmed else rec.product_uom_qty
                if quantity > 0:
                    rec.delivered_discount = (rec.discount_per_line / quantity) * rec.qty_delivered
                else:
                    rec.delivered_discount = 0
            else:
                rec.delivered_discount = 0

    @api.depends('order_id.order_line', 'price_unit', 'product_uom_qty')
    def _compute_discount_line(self):
        for rec in self:
            if rec.price_subtotal > 0 and rec.order_id.amount_untaxed > 0:
                discount_line = rec.order_id.order_line.filtered(lambda
                                                                     x: x.product_id.detailed_type == "service" and x.price_unit < 0 and not x.discount_product_id)
                delivery_charge = sum(rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit > 0).mapped('price_subtotal'))
                discount = sum(discount_line.mapped('price_subtotal'))
                discount_tax = sum(discount_line.mapped('price_tax'))
                discount_specific_line = rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit < 0 and x.discount_product_id)
                discount_specific_amount = sum(discount_specific_line.mapped('price_subtotal'))
                discount_specific_tax = sum(discount_specific_line.mapped('price_tax'))
                subtotal = ((rec.order_id.amount_untaxed - discount_specific_amount) - discount) - delivery_charge
                discount_line = ((rec.price_subtotal / subtotal) * discount) if subtotal > 0 else 0
                if rec.order_id.amount_tax > 0:
                    tax_line = (rec.price_tax / (
                            (rec.order_id.amount_tax - discount_specific_tax) - discount_tax)) * discount_tax
                else:
                    tax_line = 0
                rec.discount_per_line = discount_line if discount_line < 0 else 0
                rec.tax_line = tax_line if tax_line < 0 else 0
                discount_specific_line = rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit < 0 and x.discount_product_id)
                discount_product_specific = discount_specific_line.filtered(
                    lambda x: rec.product_id in x.discount_product_ids)
                if discount_product_specific:
                    for item in discount_product_specific:
                        product_qty = len(item.discount_product_ids)
                        discount_specific_amount = sum(item.mapped('price_subtotal')) / product_qty
                        discount_specific_tax = sum(item.mapped('price_tax')) / product_qty
                        if rec.discount_per_line < 0:
                            rec.discount_per_line += discount_specific_amount
                        else:
                            rec.discount_per_line = discount_specific_amount
                        if rec.tax_line < 0:
                            rec.tax_line += discount_specific_tax
                        else:
                            rec.tax_line = discount_specific_tax
            else:
                rec.tax_line = 0
                rec.discount_per_line = 0

    @api.depends('order_id.order_line', 'price_unit', 'product_uom_qty')
    def _compute_product_specific_discount(self):
        for rec in self:
            if rec.price_subtotal > 0 and rec.order_id.amount_untaxed > 0:
                discount_specific_line = rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit < 0 and x.discount_product_id)
                discount_product_specific = discount_specific_line.filtered(
                    lambda x: rec.product_id in x.discount_product_ids)
                if discount_product_specific:
                    for item in discount_product_specific:
                        product_qty = len(item.discount_product_ids)
                        discount_specific_amount = sum(item.mapped('price_subtotal')) / product_qty
                        if rec.discount_per_line < 0:
                            rec.product_specific_discount = discount_specific_amount
                else:
                    rec.product_specific_discount = 0
            else:
                rec.product_specific_discount = 0
    
    def _compute_promo_ids(self):
        for rec in self:
            rec.promo_ids = rec.order_id.sudo().no_code_promo_program_ids\
                .filtered(lambda x: x.discount_apply_on == 'on_order' 
                    or rec.product_id in x.discount_specific_product_ids)

    @api.depends('order_id.order_line', 'price_unit', 'product_uom_qty')
    def _compute_total_order_discount(self):
        for rec in self:
            applied_data = []
            if rec.price_subtotal > 0 and rec.order_id.amount_untaxed > 0:
                discount_lines = rec.order_id.order_line.filtered(
                    lambda
                        x: x.product_id.detailed_type == "service" and x.price_unit < 0 and not x.discount_product_id and not x.is_coupon)
                delivery_charge = sum(rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit > 0).mapped('price_subtotal'))
                discount = sum(discount_lines.mapped('price_subtotal'))
                discount_specific_line = rec.order_id.order_line.filtered(
                    lambda
                        x: x.product_id.detailed_type == "service" and x.price_unit < 0 and x.discount_product_id and not x.is_coupon)
                coupon_discount_amount = sum(rec.order_id.order_line.filtered(
                    lambda x: x.product_id.detailed_type == "service" and x.price_unit < 0 and x.is_coupon).mapped(
                    'price_subtotal'))
                discount_specific_amount = sum(discount_specific_line.mapped('price_subtotal'))
                discount_specific_tax = sum(discount_specific_line.mapped('price_tax'))
                subtotal = ((
                            rec.order_id.amount_untaxed - discount_specific_amount - coupon_discount_amount)
                            - discount) - delivery_charge
                discount_line = ((rec.price_subtotal / subtotal) * discount) if subtotal > 0 else 0
                coupon_line = ((rec.price_subtotal / subtotal) * coupon_discount_amount) if subtotal > 0 else 0
                rec.total_order_discount = discount_line if discount_line < 0 else 0
                rec.coupon_per_line = coupon_line if coupon_line < 0 else 0
                for item in (discount_lines + discount_specific_line.filtered(lambda x: x.discount_product_id == rec.product_id)):
                    coupon_program = self.env['coupon.program'].search([('discount_line_product_id', '=', item.product_id.id)])
                    if coupon_program:
                        if coupon_program.discount_apply_on == 'specific_products':
                            discount = item.price_subtotal
                        else:
                            discount = ((rec.price_subtotal / subtotal) * item.price_subtotal) if subtotal > 0 else 0
                        applied_data.append(
                            {
                                'promo_ref_id': coupon_program.id,
                                'discount_used': abs(round(discount,2))
                            }
                        ) 
                rec.promo_applied_json = json.dumps(applied_data)
            else:
                rec.promo_applied_json = json.dumps(applied_data)
                rec.total_order_discount = 0
                rec.coupon_per_line = 0

    @api.constrains('product_uom_qty')
    def _check_product_uom_qty(self):
        for rec in self:
            if not rec.product_id.allowed_beyond_stock and rec.product_id.detailed_type == 'product':
                if rec.product_uom_qty > rec.product_id.qty_available_not_res:
                    raise UserError(
                        _(f"{rec.product_id.name} is out of stock and not allowed beyond stock. Available stock - {rec.product_id.qty_available_not_res}"))
            if rec.product_id.detailed_type == 'product' and rec.product_id.max_order_quantity > 0 and rec.product_uom_qty > rec.product_id.max_order_quantity:
                raise UserError(
                    _(f"Max order quantity ({rec.product_id.max_order_quantity}) of {rec.product_id.name} exceeded"))

    def _compute_transit_qty(self):
        for rec in self:
            moves = rec.x_studio_stock_moves.filtered(lambda x: x.picking_id.picking_type_id.is_truck_loading)
            return_moves = rec.x_studio_stock_moves.filtered(lambda x: x.picking_id.picking_type_id.is_truck_return)
            # changing this because is_truck_ return was always false
            # return_moves = rec.x_studio_stock_moves.filtered(lambda x: x.picking_id.origin and 'Return of' in x.picking_id.origin and x.picking_id.state == 'done')
            pick_qty = sum(moves.filtered(lambda x: x.state == 'done' and x.quantity_done > 0).mapped('quantity_done'))
            return_moves_qty = sum(
                return_moves.filtered(lambda x: x.state == 'done' and x.quantity_done > 0).mapped('quantity_done'))
            if rec.product_id.is_pack:
                total_pack_products = sum([x.qty_uom for x in rec.product_id.pack_ids])
                if total_pack_products > 0:
                    if pick_qty > 0:
                        pick_qty = pick_qty / total_pack_products
                    if return_moves_qty > 0:
                        return_moves_qty = return_moves_qty / total_pack_products
            if pick_qty == 0:
                in_transit_qty = 0
            else:
                in_transit_qty = (pick_qty - rec.qty_delivered) - return_moves_qty
            rec.in_transit_qty = round(in_transit_qty, 2)

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id', 'in_transit_qty', 'qty_delivered')
    def _compute_transit_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.in_transit_qty,
                                            product=line.product_id, partner=line.order_id.partner_shipping_id)
            line.update({
                'in_transit_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'in_transit_total': taxes['total_included'],
                'in_transit_subtotal': taxes['total_excluded'],
            })

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id', 'qty_delivered')
    def _compute_delivered_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            product_qty = line.qty_delivered if line.product_id.detailed_type != 'service' else line.product_uom_qty
            taxes = line.tax_id.compute_all(price, line.order_id.currency_id, product_qty, product=line.product_id,
                                            partner=line.order_id.partner_shipping_id)
            line.update({
                'delivered_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'delivered_total': taxes['total_included'],
                'delivered_subtotal': taxes['total_excluded'],
            })

    def _compute_in_transit(self):
        for rec in self:
            moves = rec.x_studio_stock_moves.filtered(lambda x: x.picking_id.picking_type_id.code == 'internal')
            is_in_transit = False
            for item in moves:
                if not is_in_transit:
                    if item.state == 'done' and item.quantity_done > 0:
                        is_in_transit = True
            rec.is_in_transit = is_in_transit

    @api.onchange('product_packaging_id')
    def _onchange_product_packaging_id(self):
        for rec in self:
            if rec.product_packaging_id:
                rec.discount = rec.product_packaging_id.discount + rec.discount

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        if not self.order_id.use_delivered_qty:
            if self._check_line_unlink():
                raise UserError(
                    _('You can not remove an order line once the sales order is confirmed.\nYou should rather set the quantity to 0.'))

    def update_status(self):
        order_line_ids = self.env['sale.order.line'].search([])
        for rec in order_line_ids:
            if rec.product_uom_qty == 0.0:
                rec.status = "Cancelled"
            elif rec.in_transit_qty > 0 and (rec.in_transit_qty + rec.qty_delivered) < rec.product_uom_qty:
                rec.status = "Partially In Transit"
            elif rec.in_transit_qty >= rec.product_uom_qty:
                rec.status = "In Transit"
            elif rec.qty_delivered > 0 and rec.qty_delivered < rec.product_uom_qty:
                rec.status = "Partially Delivered"
            elif rec.qty_delivered >= rec.product_uom_qty:
                rec.status = "Delivered"
            else:
                rec.status = "In Preparation"

    def update_discount_product_ids(self):
        lines = self.env['sale.order.line'].search([('discount_product_id', '!=', False)])
        for rec in lines:
            rec.discount_product_ids = [rec.discount_product_id.id]

    @api.depends('price_subtotal', 'product_uom_qty', 'purchase_price', 'qty_delivered')
    def _compute_margin(self):
        for line in self:
            subtotal = line.price_unit * line.qty_delivered
            line.margin = (line.price_unit - line.purchase_price) * line.qty_delivered
            line.margin_percent = subtotal and line.margin / subtotal
