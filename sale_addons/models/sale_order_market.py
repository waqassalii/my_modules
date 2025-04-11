# -*- coding: utf-8 -*-

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
import bson

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'hasura.mixin']
    
    def write(self, values):
        res = super(SaleOrder, self).write(values)
        for rec in self:
            if rec.state == 'sale':
                rec.update_order_lines(values)
        return res

    def create_so(self, body):
        branch_id = body.get('branch_id')
        partner_id = branch_id.parent_id
        payment_term_id = body.get('payment_term_id')
        if partner_id:
            vals = {
                'partner_id': partner_id.id if partner_id else False,
                'partner_invoice_id': partner_id.id,
                'partner_shipping_id': branch_id.id,
                'payment_term_id': payment_term_id.id if payment_term_id else False,
                'delivery_note': body.get('order_note'),
                'payment_method': body.get('payment_method').get('type').lower(),
                'mapped_to_foodics': body.get('mapped_to_foodics'),
                'is_backend_order': True
            }
            if partner_id.user_id:
                vals['user_id'] = partner_id.user_id.id
            sale_orders = self.env['sale.order'].search([('partner_id', '=', partner_id.id),('state', '!=', 'cancel')], limit=1)
            general_tags_ids = []
            if not sale_orders:
                general_tags_ids.append(
                    self.env['sale.order.tag'].search([('name', '=', 'First Order')], limit=1).id)
            if body.get('mapped_to_foodics'):
                general_tags_ids.append(self.env.ref('sale_addons.foodics_sale_order_tag').id)
            if general_tags_ids:
                vals['so_tag_ids'] = [(6, 0, general_tags_ids)]
            merge_shipment = body.get('merge_shipment')
            if merge_shipment:
                vals['merge_shipment'] = body.get('merge_shipment')
            sale_order_id = self.env['sale.order'].create(vals)
            if sale_order_id:
                sale_order_id._onchange_credit_note_ids()
                return sale_order_id
            else:
                return False
            
    def add_credit_notes(self, credit_note_ids):
        credit_notes = credit_note_ids
        if credit_notes:
            credit_note_list = []
            amount = 0
            for credit_id in credit_notes:
                credit_note_id = self.env['account.move'].search(
                    [('id', '=', int(credit_id['ref_id']))])
                if credit_note_id:
                    credit_note_id.used_amount = credit_note_id.used_amount + credit_id['amount_used_by_order']
                    credit_note_list.append((0, 0, {
                        'credit_note_id': credit_note_id.id,
                        'amount': credit_id['amount_used_by_order']
                    }))
                    amount += credit_id['amount_used_by_order']
            self.write({
                'credit_note_ids': credit_note_list,
                'use_credit_balance': amount,
                'is_use_credit_balance': True
            })

    def add_payment_method(self, payment_method, amount):
        self.so_payment_method_ids = [
                    (0, 0, {
                    'payment_method': payment_method.lower(),
                    'amount': amount})]

    def add_online_charge(self, total_amount, credit=False,payment_term=False):
        search_var = 'is_online_charge' if not credit else 'is_credit_charge'
        product_id = self.env['product.product'].search([(search_var, '=', True)],
                                                                                limit=1)
        if product_id:
            charge = product_id.online_charge if not payment_term else (payment_term.fee_percentage/100)
            charges = total_amount * (charge)
            self.order_line = [(0, 0, {
                            'product_id': product_id.id,
                            'price_unit': charges,
                            'name': product_id.name,
                            'product_uom_qty': 1,
                            'is_pos_charge': True
                        })]
    
    @api.model
    def create(self, vals):
        return super(SaleOrder, self).create(vals)
                
    def create_order_lines(self, shipments):
        vals = []
        for item in shipments:
            shipment_id = item.get('_id')
            expected_delivery_date = item.get('shipment_original_delivery_date')
            if expected_delivery_date:
                expected_delivery_date_str = datetime.strptime(expected_delivery_date, '%Y-%m-%dT%H:%M:%S.%fZ')
                expected_delivery_date = expected_delivery_date_str.strftime('%Y-%m-%d')
            for rec in item.get('shipment_lines'):
                shimpment_line_id = rec.get('_id')
                product_package = rec.get('product').get('product_packcage')
                product_id = rec.get('product_id')
                quantity = rec.get('quantity_ordered')
                price = product_id.lst_price
                charge = 0
                if self.payment_term_id.fee_percentage > 0:
                    charge = self.payment_term_id.fee_percentage
                    price = price + (price * (charge/100))
                product_data = {
                    'product_id': product_id.id,
                    'shipment_id': shipment_id,
                    'shipment_line_id': shimpment_line_id,
                    'name': product_id.name,
                    'product_uom_qty': quantity,
                    'manual_qty': quantity,
                    'expected_delivery_date': expected_delivery_date if expected_delivery_date else False,
                    'price_unit': price,
                    'order_id': self.id,
                    'payment_term_charge': charge
                }
                if product_package:
                    product_package = self.env['product.packaging'].search([('id', '=', product_package.get('ref_id'))], limit=1)
                    product_data['product_packaging_id'] = product_package.id
                    product_data['product_packaging_qty'] = quantity
                    product_data['product_uom_qty'] = product_package.qty * quantity 
                vals.append((0,0,product_data))
        if vals:
            self.order_line = vals           
    
    def add_delivery_charge_so(self, shipping_method):
        carrier_id = self.env['delivery.carrier'].sudo().search([('id', '=', shipping_method)])
        today_date = self.date_order.date()
        start_of_day = datetime.combine(today_date, time.min)
        end_of_day = datetime.combine(today_date, time.max)
        partner_id = self.partner_shipping_id.id
        past_order_number = self.env['sale.order'].sudo().search_count(
            [('partner_shipping_id', '=', partner_id),
                ('state', '!=', 'cancel'),
                ('date_order', '>=', start_of_day),
                ('date_order', '<=', end_of_day), ])
        total_orders = self.env['sale.order'].sudo().search_count(
                [('partner_id', '=', self.partner_id.id), ('id', '!=', self.id or self._origin.id),
                    ('state', '!=', 'cancel')])
        shipping_method = self.env['delivery.carrier'].search([('region_id', '=', self.partner_shipping_id.region_master_id.id)], limit=1)
        if total_orders >= 2 or not total_orders < shipping_method.number_of_first_free_orders:
            if past_order_number <= 1:
                if carrier_id:
                    delivery_wizard = Form(self.env['choose.delivery.carrier'].with_context({
                        'default_order_id': self.id,
                        'default_carrier_id': carrier_id.id,
                    }))
                    choose_delivery_carrier = delivery_wizard.save()
                    choose_delivery_carrier.with_context({'api': True}).button_confirm()
    
    def apply_promotion_on_the_order(self, promo_ids):
        self.sudo().recompute_multi_coupon_lines(promo_ids)
        
    def validate_customer_data(self):
        message = False
        if not self.payment_method:
           message = "Please select the payment method"
        if self.partner_id.is_blocked and not self.env.user.has_group('base.group_erp_manager'):
           message = "This is a blocked user, please contact the administrator to process the order"
        if not self.partner_shipping_id.active_time_windows:
           message = "Delivery window is not set for the branch"
        if not self.partner_shipping_id.street:
            message = "Branch details are missing (Street)"
        if not self.partner_shipping_id.city:
            message = "Branch details are missing (City)"
        if not self.partner_shipping_id.country_id: 
            message = "Branch details are missing (Country)"
        if not self.partner_shipping_id.area_master_id: 
           message = "Branch details are missing (Area)"
        if not self.partner_shipping_id.phone:
           message = "Branch details are missing (Phone)"
        if not self.partner_shipping_id.x_studio_location:
           message = "Branch location missing"
        if not self.partner_shipping_id.region_master_id:
           message = "Branch is missing region"
        if self.partner_id.partner_type_id.is_cancel_order:
           self.action_cancel()
           message = "We are not serving this sector for the time being. ({self.partner_id.partner_type_id.name})"
        return message
    
    def prepare_product_for_lines(self, rec, sale_line=False,no_image=False):
        product = {
                'product_ref_id': rec.product_id.id,
                'unit': rec.product_id.uom_id.name,
                'tax_percentage': rec.product_id.taxes_id[0].amount if rec.product_id.taxes_id else 0,
                'image_url': rec.product_id.get_product_image_url() if not no_image else "",
                'name': rec.product_id.name,
                'arabic_name': rec.product_id.arabic_name,
                'original_price': rec.product_id.lst_price
                }
        package_id = rec.sale_line_id.product_packaging_id if not sale_line else rec.product_packaging_id
        uom = self.env['uom.uom'].sudo().search([('name', '=', 'Unit')], limit=1)
        if package_id:
            product['product_packcage'] = {
                'ref_id': package_id.id,
                'package_name': package_id.name,
                'discount': package_id.discount,
                'product_conversion_count': package_id.qty,
                'conversion_units': package_id.product_uom_id.name if package_id.product_uom_id else uom.name,
                'arabic_name': package_id.arabic_name if package_id.arabic_name else None
            }
        return product
    
    def prepare_shipment_lines_for_sale(self, move):
        shipment_lines = []
        for rec in move.move_ids_without_package.filtered(lambda x: not x.product_pack_id):
            original_price = rec.sale_line_id.price_unit * rec.product_uom_qty
            original_price_after_terms = rec.sale_line_id.price_unit + rec.sale_line_id.payment_term_charge
            discount =  rec.sale_line_id.total_order_discount + rec.sale_line_id.product_specific_discount
            price_after_discount =  original_price_after_terms + discount
            added_tax = rec.sale_line_id.price_tax + rec.sale_line_id.tax_line 
            final_price = price_after_discount + added_tax
            discount_percentage = 0 
            if discount > 0:
                discount_percentage = (discount/original_price) * 100
            quantity_ordered = rec.product_uom_qty
            if rec.product_packaging_id:
                quantity_ordered = rec.sale_line_id.product_packaging_qty
            shipment_line = {
                'ref_id': rec.id,
                'quantity_ordered': int(quantity_ordered),
                'original_price': round(original_price,2),
                'original_price_after_terms_fee': round(original_price_after_terms,2),
                'price_after_terms_after_discount': round(price_after_discount,2),
                'price_after_terms_after_discount_after_tax': round(final_price,2),
                'added_tax_amount_after_terms': round(added_tax,2),
                'product': self.prepare_product_for_lines(rec),
                'discount_percentage': round(discount_percentage,2)
            }
            if rec.sale_line_id.promo_ids:
                shipment_line['promotions_applied_data'] = json.loads(rec.sale_line_id.promo_applied_json)
            shipment_lines.append(shipment_line)
        pack_ids = move.move_ids_without_package.filtered(lambda x: x.product_pack_id)
        if pack_ids:
            order_lines = pack_ids.mapped('sale_line_id')
            for item in order_lines:
                obj_id = bson.ObjectId()
                item.shipment_line_id = str(obj_id)
                original_price = sum([rec.product_id.lst_price for rec in item.product_id.pack_ids]) * item.product_uom_qty
                org_after_terms = original_price + item.payment_term_charge
                discount = original_price - item.price_unit
                discount_percentage = 0
                if discount > 0:
                    discount_percentage = (discount/original_price) * 100
                shipment_line = {
                    '_id': str(obj_id),
                    'quantity_ordered': item.product_uom_qty,
                    'original_price': round(sum([rec.product_id.lst_price for rec in item.product_id.pack_ids]),2),
                    'original_price_after_terms_fee': round(org_after_terms,2),
                    'price_after_terms_after_discount': round(item.price_subtotal,2),
                    'price_after_terms_after_discount_after_tax': round(item.price_total,2),
                    'added_tax_amount_after_terms': round(item.price_tax,2),
                    'product': self.prepare_product_for_lines(item, True),
                    'discount_percentage': round(discount_percentage,2)
                }
                shipment_lines.append(shipment_line)
        return shipment_lines
    
    def prepare_shipments_for_sale(self):
        shipments = []
        pickings = self.picking_ids.filtered(lambda x: (x.picking_type_id.is_out_delivery or x.picking_type_id.is_dropship) and x.state != 'cancel')
        for rec in pickings:
            shipment_vals = {
                'ref_id': rec.id,
                'shipment_number': rec.shipment_number,
                'shipment_original_delivery_date': rec.date_deadline.strftime("%Y-%m-%d"),
                'status': 'PENDING',
                'shipment_lines': self.prepare_shipment_lines_for_sale(rec)
            }
            shipments.append(shipment_vals)
        return shipments
    
    def unlink(self):
        for record in self:
            record.delete_sale_order()
        return super(SaleOrder, self).unlink()
    
    def delete_sale_order(self):
        value = {
            'ref_id': self.id,
        }
        query = self.prepare_query("deleteOrderFromOdoo","DeleteOrderInputDTO!","input","ok")
        variable = {
            "object": value
        } 
        self.run_query(query, variable, delivery=True)
        self.is_backend_order = False
    
    def update_cancel_order(self):
        if self.state != 'draft':
            value = {
                'ref_id': self.id,
                'status': 'CANCELLED'
            }
            query = self.prepare_query("updateOrderFromOdoo","OrderInputDTO!","input","ok")
            variable = {
                "object": value
            } 
            self.run_query(query, variable, delivery=True)
    
    def update_order_after_confirmed(self):
        value = {
            'ref_id': self.id,
            'status': 'CONFIRMED',
            'shipments': self.prepare_update_shipments_for_sale()
        }
        query = self.prepare_query("updateOrderFromOdoo","OrderInputDTO!","input","ok")
        variable = {
            "object": value
        } 
        self.run_query(query, variable, delivery=True)
    
    def prepare_update_shipments_for_sale(self):
        shipments = []
        pickings = self.picking_ids.filtered(lambda x: (x.picking_type_id.is_out_delivery or x.picking_type_id.is_dropship) and x.state != 'cancel')
        for rec in pickings:
            shipment_id = rec.move_ids_without_package.sale_line_id.mapped('shipment_id')
            if shipment_id:
                shipment_id = shipment_id[0]
                shipment_vals = {
                    '_id': shipment_id,
                    'ref_id': rec.id,
                    'shipment_number': rec.shipment_number,
                    'shipment_lines': self.prepare_update_shipment_lines_for_sale(rec)
                }
                shipments.append(shipment_vals)
        return shipments

    def prepare_update_shipment_lines_for_sale(self, move):
        shipment_lines = []
        for rec in move.move_ids_without_package.filtered(lambda x: not x.product_pack_id):
            shipment_line = {
                '_id': rec.sale_line_id.shipment_line_id,
                'ref_id': rec.id,
            }
            shipment_lines.append(shipment_line)
        return shipment_lines
    
    def get_promo_code_data(self, promo_discount=0):
        if self.sudo().code_promo_program_id:
            promo = self.sudo().code_promo_program_id
            return {
                'promo_code': promo.promo_code,
                'promo_discount_percentage': promo.discount_percentage,
                'promo_code_discount': promo_discount,
                'promo_ref_id': promo.id
            }      
        else:
            return False   
    
    def get_expected_dates(self):
        picking_ids = self.picking_ids.filtered(lambda x: x.picking_type_id.is_out_delivery).sorted(key=lambda x: x.date_deadline)
        if picking_ids:
            first_delivery = picking_ids[0].date_deadline.strftime("%Y-%m-%d")
            final_delivery = picking_ids[-1].date_deadline.strftime("%Y-%m-%d")
            return first_delivery,final_delivery
        else:
            first_delivery,final_delivery = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            return first_delivery,final_delivery

    def create_sale_order_new_backend(self):
        query = """
             mutation createOrderFromOdoo($object: CreateOrderInputDTO!) {
                  createOrderFromOdoo(input: $object) {
                    message
                    ok
                  }
                }
        """
        product_lines = self.order_line.filtered(lambda x: x.product_id.detailed_type == 'product' and x.price_unit > 0)
        products_discount = abs(sum(product_lines.mapped('product_specific_discount'))) + abs(sum(product_lines.mapped('total_order_discount'))) 
        promo_discount = abs(sum(product_lines.mapped('coupon_per_line')))
        promo_data = False
        if promo_discount > 0:
            promo_data = self.get_promo_code_data(promo_discount)
        first_delivery,final_delivery = self.get_expected_dates()
        sale_val = {
            "ref_id": self.id,
            "sale_order_number": self.name,
            "registered_in_erp": True,
            "status": "CONFIRMED",
            "order_total_tax_after_terms": self.amount_tax,
            "order_sub_total_after_terms": self.amount_untaxed,
            "order_products_after_terms_total_discount": products_discount if products_discount else 0,
            "order_note": self.note,
            "order_total_after_terms_after_discount_after_promo_after_tax_after_delivery_fees": self.amount_total,
            "order_entity": {
                "ref_id": self.partner_id.id,
                "branch_ref_id": self.partner_shipping_id.id
            },
            "shipments": self.prepare_shipments_for_sale(),
            "expected_final_delivery_date": final_delivery,
            "expected_initial_delivery_date": first_delivery,
            "delivery_fees": self.delivery_charge
        }
        shipping_method = self.env['delivery.carrier'].search([('region_id', '=', self.partner_shipping_id.region_master_id.id)], limit=1)
        if shipping_method:
            sale_val['shipping_method_ref_id'] = shipping_method.id
        if promo_data:
            sale_val['promo'] = promo_data
        variables = {"object": sale_val}
        self.run_query(query, variables, False, True)
    
    def action_add_payment_term(self):
        if self.state != 'sale':
            raise UserError(_("Please add the payment term charge after order confirmation"))
        payment_term_id = self.partner_id.property_payment_term_id
        if not payment_term_id:
            raise UserError(_("No payment term assigned to the user"))
        self.payment_term_id = payment_term_id.id
        if payment_term_id.fee_percentage > 0:
            fee = payment_term_id.fee_percentage
            for rec in self.order_line.filtered(lambda x: x.product_id.detailed_type == 'product' and x.price_unit > 0):
                charge = rec.product_id.lst_price * (fee/100)
                price_unit = rec.product_id.lst_price + charge 
                rec.write({'price_unit': price_unit, 'payment_term_charge': charge})
        self.update_order_lines({},True)
        self.use_payment_terms_sales()
        
    def action_resync_order_shipment(self):
        query = """
            mutation OdooAutoResyncOrderShipment($input: OdooForcedResyncInputDTO!) {
                odooAutoResyncOrderShipment(input: $input) {
                    ok    
                }
            }
        """
        variables = {"input":{"order_ref_id": self.id}}
        self.run_query(query,variables,delivery=True)
    
    def use_payment_terms_sales(self):
        query = """
            mutation UsePaymentTerm($object:RefIdInputDTO!){
                    usePaymentTerm(input:$object){
                        message
                }
            }
        """
        variables = {"object":{"ref_id": self.id}}
        self.run_query(query,variables,delivery=True)
    
    def update_order_lines(self,values,force=False):
        shipments = []
        if (values.get('order_line') and self.picking_ids) or force:
            for rec in self.picking_ids.filtered(lambda x: x.state != 'done' and x.picking_type_id.is_out_delivery and x.state != 'cancel'):
                shipment_lines = []
                value = {
                    'ref_id': rec.id,
                    'shipment_lines': shipment_lines
                    }
                for item in rec.move_ids_without_package:
                    original_amount = item.sale_line_id.price_unit * item.product_uom_qty
                    total_tax = item.sale_line_id.price_tax + item.sale_line_id.tax_line
                    discount =  item.sale_line_id.total_order_discount + item.sale_line_id.product_specific_discount
                    price_after_discount = original_amount + discount
                    final_price = price_after_discount + total_tax
                    line_vals = {
                        'ref_id': item.id,
                        'product': self.prepare_product_for_lines(item),
                        'adjusted_original_price': round(original_amount,2),
                        'adjusted_price_after_discount': round(price_after_discount,2),
                        'adjusted_price_after_discount_after_tax': round(final_price,2)
                    }
                    if item.sale_line_id.promo_ids:            
                        line_vals['promotions_applied_data'] = json.loads(item.sale_line_id.sudo().promo_applied_json)
                    shipment_lines.append(line_vals)
                shipments.append(value)
            product_lines = self.order_line.filtered(lambda x: x.product_id.detailed_type == 'product' and x.price_unit > 0)
            products_discount = abs(sum(product_lines.mapped('product_specific_discount'))) + abs(sum(product_lines.mapped('total_order_discount'))) 
            promo_discount = abs(sum(product_lines.mapped('coupon_per_line')))
            promo_data = False
            if promo_discount > 0:
                promo_data = self.get_promo_code_data(promo_discount)
            first_delivery,final_delivery = self.get_expected_dates()
            value = {
                "ref_id": self.id,
                "order_total_tax_after_terms": self.amount_tax,
                "order_sub_total_after_terms": self.amount_untaxed,
                "order_products_after_terms_total_discount": products_discount if products_discount else 0,
                "order_total_after_terms_after_discount_after_promo_after_tax_after_delivery_fees": self.amount_total,
                "expected_final_delivery_date": final_delivery,
                "expected_initial_delivery_date": first_delivery
            }
            if promo_data:
                value['promo'] = promo_data
            if shipments:
                value['shipments'] = shipments
            query = self.prepare_query("updateOrderFromOdoo","OrderInputDTO!","input","ok")
            variable = {
                "object": value
            } 
            self.run_query(query, variable, delivery=True)

    def process_order(self, body):
        if body.get('promo_ids'):
            promo_ids = body.get('promo_ids')
            normal_promo = promo_ids.filtered(lambda x: x.reward_type != 'free_shipping')
            if normal_promo:
                try:
                    self.apply_promotion_on_the_order(promo_ids.ids)  
                except:
                    return {"status": "Promo applier faile", "code": "404"}
        shipping_method = body.get('shipping_method_ref_id')
        if shipping_method:
            self.add_delivery_charge_so(shipping_method)       
        credit_note_ids = body.get('order_credit_balance')
        if credit_note_ids:
            credit_note_ids = credit_note_ids.get('credit_notes')
        if credit_note_ids:
            self.add_credit_notes(credit_note_ids)
        payment_method = body.get('payment_method')
        if payment_method:
            final_amount = self.amount_total - self.use_credit_balance
            self.add_payment_method(payment_method.get('type'),final_amount)
            if payment_method.get('type').lower() in ['pos','online', 'link']:
                self.add_online_charge(final_amount)
        message = self.validate_customer_data()
        if not message: 
            self.action_confirm()

    # @api.model
    def update_sale_order_and_shipments(self):
        ref_id_list = [
            '1259', '27379', '36124', '28866', '15469', '22004', '31557', '25095',
            '20304', '9082', '23929', '33646', '22571', '9070', '9898', '84826',
            '26639', '9172', '37586', '25708', '23564', '9323', '9079', '43988',
            '10409', '4292', '52398', '30180', '30975', '30734', '31482', '27614',
            '7986', '33851', '59317', '19119', '42780', '40662', '2079', '31602',
            '10287', '30287', '44147', '28499', '36527', '34412', '26206', '42845',
            '4532', '4474', '21614', '25673', '11806', '28652', '22204', '24428',
            '19723', '40713', '24807', '46651', '81519', '24143', '8029', '19274',
            '45641', '25338', '17024', '20675', '27190', '36587', '35932', '32187',
            '32371', '17969', '30049', '27635', '22874', '17736', '19552', '30629',
            '12144', '30561', '32458', '26656', '40360', '13560', '44101', '18580',
            '13620', '34778', '43622', '25847', '19661', '42381', '29204', '39985',
            '44999', '24958', '19980', '17664', '11369', '29067', '29420', '33400',
            '32090', '22264', '29587', '43183', '44557', '40926', '11802', '25692',
            '18675', '22908', '38922', '10638', '40417', '17596', '26669', '26083',
            '30277', '20291', '28484', '34977', '22043', '24596', '34968', '28058',
            '28787', '26539', '16037', '17944', '20792', '27655', '31729', '28591',
            '20164', '23968', '23922', '19749', '18570', '22378', '44766', '28950',
            '36561', '25176', '26532', '38252', '26545', '40778', '39842', '42318',
            '20807', '21553', '16355', '33497', '28290', '40471', '23364', '28681',
            '17049', '33007', '17995', '34018', '38442', '18463', '33677', '19699',
            '31575', '29628', '19195', '35951', '38201', '44705', '18182', '35396',
            '44968', '26433', '22896', '29924', '31260', '22645', '26509', '18446',
            '29500', '19399', '18386', '22177', '35594', '40617', '19538', '21442',
            '30493', '31052', '18648', '37912', '26009', '22033', '17976', '34005',
            '31028', '20862', '42461', '29831', '31276', '22121', '32848', '41924',
            '41583', '27819', '27933', '30384', '24494', '22083', '21698', '26367',
            '40115', '39100', '22010', '40747', '41608', '30072', '27811', '30898',
            '31512', '18526', '36521', '25476', '21217', '34482', '36223', '39942',
            '31947', '25534', '19887', '19908', '37442', '41016', '32543', '27363',
            '33794', '38621', '35855', '42110', '19151', '22496', '29318', '27841',
            '30879', '36658', '24256', '32294', '41271', '23845', '27687', '21909',
            '27768', '24980', '23115', '38229', '35295', '30858', '42136', '18972',
            '40272', '25990', '35504', '39795', '34247', '40439', '29736', '25784',
            '29078', '31595', '38297', '23347', '25915', '25773', '40460', '26814',
            '41539', '44036', '29698', '26331', '37148', '37192', '31804', '28697'
        ]
        self.update_sale_orders_and_shipments_status(ref_id_list)

    # @api.model
    def update_sale_orders_and_shipments_status(self, ref_id_list):
            sale_orders = self.env['sale.order'].search([('id', 'in', ref_id_list)])
            for sale_order in sale_orders:
                delivery_status = sale_order.delivery_status.upper()
                if sale_order.picking_ids:
                    for picking in sale_order.picking_ids:
                        if picking.is_out_delivery:
                            order_status = {
                                "DELIVERED": "DELIVERED",
                                "READY": "READY"
                            }
                            if not delivery_status == "DELIVERED":
                                delivery_status = "READY"
                            status = order_status.get(delivery_status)
                            value = {
                                'ref_id': sale_order.id,
                                'shipments': {
                                    'ref_id': picking.id,
                                    'status': status
                                }
                            }
                            query = self.prepare_query("updateOrderFromOdoo", "OrderInputDTO!", "input", "ok")
                            variable = {"object": value}
                            self.run_query(query, variable, delivery=True)

            return {'status': 'success', 'message': 'Sale Orders and Pickings updated successfully'}
