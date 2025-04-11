import base64
from odoo import http, fields
from odoo.http import request
import requests
from datetime import datetime, date, time, timedelta
import logging

_logger = logging.getLogger(__name__)


class Main(http.Controller):

    def login(self, db, username, password):
        try:
            request.session.authenticate(db, username, password)
            return True
        except requests.exceptions.HTTPError as errh:
            return errh.args[0]

    @http.route('/api/v1/create_payment', methods=['POST'], auth="none", type="json")
    def create_payment(self, **kwargs):
        body = request.jsonrequest
        args = {'status': "Bad Content", 'code': "404"}
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    if not body.get('order_ref_id'):
                        args = {'status': "Order Reference not provided", 'code': "400"}
                    elif not body.get('payment_number'):
                        args = {'status': "Payment Number not provided", 'code': "400"}
                    elif not body.get('amount'):
                        args = {'status': "Amount not provided", 'code': "400"}
                    else:
                        sale_order = request.env['sale.order'].sudo().search([('id', '=', body.get('order_ref_id'))])
                        if not sale_order:
                            args = {'status': "This Order cannot be found", 'code': "404"}
                        else:
                            payment_id = request.env['account.payment'].sudo().create({
                                'date': fields.Date.today(),
                                'is_internal_transfer': False,
                                'amount': body.get('amount'),
                                'payment_type': 'inbound',
                                'partner_type': 'customer',
                                'partner_id': sale_order.partner_id.id,
                                'state': 'draft',
                            })
                            sale_order.write({
                                'payment_method': 'online',
                                'online_payment_number': body.get('payment_number'),
                                'payment_id': payment_id.id
                            })
                            payment_id.action_post()
                            args = {'status': "Success",
                                    'code': "200",
                                    'sale_order': str(sale_order.id),
                                    'payment_id': str(payment_id.id)
                                    }
                else:
                    args = {'status': "Odoo server error", 'code': "400", }
        return args

    @http.route("/api/checkqty", type="json", auth="none", methods=['POST'])
    def bulk_check_max_order_quantity(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        error_message = []
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    product_ids = body.get('product_ids')
                    for product_id in product_ids:
                        product_ref_id = int(product_id['ref_id'])
                        quantity = int(product_id['quantity'])
                        product_object = request.env['product.product'].sudo().search([('id', '=', product_ref_id)])
                        if not product_object.priority == "1" and not product_object.sale_ok:
                            message = f"{product_ref_id} is not Active"
                            error_message.append({
                                "product_ref_id": product_ref_id,
                                "code": 3000,
                                "message": message,
                            })
                        if not product_object.allowed_beyond_stock and product_object.detailed_type == 'product':
                            if product_object.qty_available_not_res <= 0:
                                message = f"{product_object.id} is not available in stock"
                                error_message.append({
                                    "product_ref_id": product_ref_id,
                                    "code": 5001,
                                    "message": message,
                                })
                            if product_object.qty_available_not_res > 0 and quantity > product_object.qty_available_not_res:
                                message = f"The Quantity: {quantity} for Product: {product_object.id} is beyond the available stock. Available stock :{product_object.qty_available_not_res}"
                                error_message.append({
                                    "product_ref_id": product_ref_id,
                                    "code": 5002,
                                    "message": message,
                                })
                        if product_object.detailed_type == 'product' and product_object.max_order_quantity > 0 and quantity > product_object.max_order_quantity:
                            message = f"Max order quantity ({product_object.max_order_quantity}) of {product_object.id} exceeded"
                            error_message.append({
                                "product_ref_id": product_ref_id,
                                "code": 4002,
                                "message": message,
                            })
                    if not error_message:
                        args = {"status": "Success", "code": 200, }
        if error_message:
            args = {
                "status": "CART_ERRORS",
                "errors": error_message,
            }
        return args

    @http.route("/api/saleorder", type="json", auth="none", methods=['POST'])
    def create_sale_order(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    _logger.info(body)
                    partner_id = False
                    branch_id = body.get('branch_id')
                    branch_id = request.env['res.partner'].search([('id', '=', branch_id)])
                    if branch_id:
                        partner_id = branch_id.parent_id
                    ordered_by = body.get('ordered_by')
                    payment_term_id = body.get('payment_term_ref_id')
                    if payment_term_id:
                        payment_term_id = request.env['account.payment.term'].search([('name', '=', payment_term_id)])
                    else:
                        payment_term_id = partner_id.property_payment_term_id
                    expected_delivery_date = body.get('expected_delivery_date')
                    if expected_delivery_date:
                        expected_delivery_date = datetime.strptime(expected_delivery_date, '%Y/%d/%m')
                    if partner_id:
                        vals = {
                            'partner_id': partner_id.id if partner_id else False,
                            'partner_invoice_id': partner_id.id,
                            'partner_shipping_id': branch_id.id,
                            'payment_term_id': payment_term_id.id if payment_term_id else False,
                            'ordered_by': ordered_by,
                            'delivery_note': body.get('delivery_note'),
                            'payment_method': body.get('payment_method'),
                            'commitment_date': expected_delivery_date,
                            'mapped_to_foodics': body.get('mapped_to_foodics'),
                            'is_api': True
                        }
                        if partner_id.user_id:
                            vals['user_id'] = partner_id.user_id.id
                        credit_notes = body.get('credit_note_ids')
                        if credit_notes:
                            credit_note_list = []
                            amount = 0
                            for credit_id in credit_notes:
                                credit_note_id = request.env['account.move'].search(
                                    [('id', '=', int(credit_id['credit_note_ref_id']))])
                                if credit_note_id:
                                    credit_note_id.used_amount = credit_note_id.used_amount + credit_id['amount']
                                    credit_note_list.append((0, 0, {
                                        'credit_note_id': credit_note_id.id,
                                        'amount': credit_id['amount']
                                    }))
                                    amount += credit_id['amount']
                            vals['credit_note_ids'] = credit_note_list
                            vals['use_credit_balance'] = amount
                            vals['is_use_credit_balance'] = True
                        payment_method_ids = body.get('payment_method_ids')
                        if payment_method_ids:
                            online_payment = sum(
                                pm['amount'] for pm in payment_method_ids if pm['payment_method'] == 'pos' or  pm['payment_method'] == 'link')
                            if online_payment > 0:
                                online_charge_product_id = request.env['product.product'].search([('is_online_charge', '=', True)],
                                                                                          limit=1)
                                if online_charge_product_id:
                                    online_charges = online_payment * (online_charge_product_id.online_charge)
                                    vals['order_line'] = [(0, 0, {
                                        'product_id': online_charge_product_id.id,
                                        'price_unit': online_charges,
                                        'name': online_charge_product_id.name,
                                        'product_uom_qty': 1,
                                        'is_pos_charge': True
                                    })]
                            vals['so_payment_method_ids'] = [
                                (0, 0, {
                                    'payment_method': pm.get('payment_method', ''),
                                    'transaction_id': pm.get('transaction_id', ''),
                                    'provider': pm.get('provider', ''),
                                    'amount': pm.get('amount', 0)
                                }) for pm in payment_method_ids
                            ]
                        if 'online_payment_fail' in body:
                            vals['online_payment_fail'] = body.get('online_payment_fail')
                        sale_orders = request.env['sale.order'].search([('partner_id', '=', partner_id.id),('state', '!=', 'cancel')], limit=1)
                        general_tags_ids = []
                        if not sale_orders:
                            general_tags_ids.append(
                                request.env['sale.order.tag'].search([('name', '=', 'First Order')], limit=1).id)
                        if body.get('mapped_to_foodics'):
                            general_tags_ids.append(request.env.ref('sale_addons.foodics_sale_order_tag').id)
                        if general_tags_ids:
                            vals['so_tag_ids'] = [(6, 0, general_tags_ids)]
                        merge_shipment = body.get('merge_shipment')
                        if merge_shipment:
                            vals['merge_shipment'] = body.get('merge_shipment')
                        sale_order_id = request.env['sale.order'].create(vals)
                        sale_order_id._onchange_credit_note_ids()
                        if sale_order_id:
                            args = {'status': "Success", 'code': "200", "id": sale_order_id.id,
                                    "sale_order_number": sale_order_id.name}
                        else:
                            args = {'status': "Odoo Server Error", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (partner_id, lines)", 'code': "404"}
        return args

    @http.route("/api/payment_method", type="json", auth="none", methods=['PUT'])
    def add_payment_method(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            _logger.info("**********API PAYMENT METHOD***")
            _logger.info(body)
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            order_ref_id = body.get('order_ref_id')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            elif not order_ref_id:
                args = {'status': "Order id not provided", 'code': "403"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    payment_method_ids = body.get('payment_method_ids')
                    order_ref_id = body.get('order_ref_id')
                    order_id = request.env['sale.order'].search([('id', '=', order_ref_id)])
                    if payment_method_ids:
                        online_payment = sum(
                            pm['amount'] for pm in payment_method_ids if pm['payment_method'] == 'pos' or  pm['payment_method'] == 'link')
                        if online_payment > 0:
                            online_charge_product_id = request.env['product.product'].search([('is_online_charge', '=', True)],
                                                                                        limit=1)
                            if online_charge_product_id:
                                online_charges = online_payment * (online_charge_product_id.online_charge)
                                order_line = [(0, 0, {
                                    'product_id': online_charge_product_id.id,
                                    'price_unit': online_charges,
                                    'name': online_charge_product_id.name,
                                    'product_uom_qty': 1,
                                    'is_pos_charge': True
                                })]
                                order_id.order_line = order_line
                        so_payment_method_ids = [
                            (0, 0, {
                                'payment_method': pm.get('payment_method', ''),
                                'transaction_id': pm.get('transaction_id', ''),
                                'provider': pm.get('provider', ''),
                                'amount': pm.get('amount', 0)
                            }) for pm in payment_method_ids
                        ]
                        if so_payment_method_ids:
                            order_id.so_payment_method_ids = so_payment_method_ids
                        args = {'status': "Success", 'code': "200"}
                else:
                    args = {'status': "Required fields not provided (product_id)", 'code': "404"}
        _logger.info(args)
        return args
    
    @http.route("/api/saleorderline", type="json", auth="none", methods=['POST'])
    def create_sale_order_line(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    product_id = body.get('product_id')
                    if product_id:
                        product_id = request.env['product.product'].search([('id', '=', product_id)])
                        tax_id = body.get('tax_id')
                        tax_id = request.env['account.tax'].search(
                            [('name', '=', tax_id), ('type_tax_use', '=', 'sale')]) if tax_id else False
                        quantity = body.get('quantity')
                        price_unit = body.get('price_unit')
                        product_package_qty = body.get('product_packaging_qty')
                        sale_order_id = request.env['sale.order'].search([('id', '=', body.get('sale_order_id'))])
                        is_package = body.get('is_package')
                        discount = body.get('discount')
                        product_package = False
                        if is_package:
                            product_package = request.env['product.packaging'].search(
                                [('product_id', '=', product_id.id)], limit=1)
                        if is_package and not product_package:
                            args = {'status': "Product package not found", 'code': "404"}
                        else:
                            if not sale_order_id or not product_id or not quantity:
                                args = {'status': "Required fields not provided (sale_order_id, quantity, product_id)",
                                        'code': "404"}
                            else:
                                vals = {
                                    'product_id': product_id.id,
                                    'name': product_id.name,
                                    'product_uom_qty': quantity,
                                    'manual_qty': quantity,
                                    'price_unit': product_id.lst_price,
                                    'order_id': sale_order_id.id,
                                    'is_api': True
                                }
                                if product_package:
                                    vals['product_packaging_id'] = product_package.id
                                    vals['product_packaging_qty'] = product_package_qty
                                    vals['discount'] = product_package.discount
                                sale_order_line_id = request.env['sale.order.line'].create(vals)
                                if sale_order_line_id:
                                    args = {'status': "Success", 'code': "200", "id": sale_order_line_id.id}
                    else:
                        args = {'status': "Required fields not provided (product_id)", 'code': "404"}
        return args

    @http.route("/api/saleorder", type="json", auth="none", methods=['PUT'])
    def update_sale_order(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    sale_order = body.get('ref_id')
                    if sale_order:
                        sale_order_id = request.env['sale.order'].search([('id', '=', sale_order)])
                        if sale_order_id:
                            payment_term = body.get('payment_term_ref_id')
                            if payment_term:
                                payment_term_id = request.env['account.payment.term'].search(
                                    [('name', '=', payment_term)])
                                if payment_term_id:
                                    sale_order.payment_term_id = payment_term_id
                            if 'online_payment_fail' in body:
                                sale_order_id.online_payment_fail = body.get('online_payment_fail')
                            ordered_by = body.get('ordered_by')
                            if ordered_by:
                                sale_order_id.ordered_by = ordered_by
                            args = {'status': "Success", 'code': "200"}
                        else:
                            args = {'status': "Sale order id provided is not valid", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (sale_order_id)", 'code': "404"}
        return args

    @http.route("/api/saleorderline", type="json", auth="none", methods=['PUT'])
    def update_sale_order_line(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    sale_order_line = body.get('ref_id')
                    if sale_order_line:
                        sale_order_line_id = request.env['sale.order.line'].search([('id', '=', sale_order_line)])
                        if sale_order_line_id:
                            entity_product_id = body.get('entity_product_id')
                            if entity_product_id:
                                pricelist_item = request.env['product.pricelist.item'].search(
                                    [('id', '=', entity_product_id)])
                                if pricelist_item:
                                    product_id = request.env['product.product'].search(
                                        [('product_tmpl_id', '=', pricelist_item.product_tmpl_id.id)])
                                    sale_order_line_id.product_id = product_id.id
                                    sale_order_line_id.name = product_id.name
                            price_unit = body.get('price_unit')
                            if price_unit:
                                sale_order_line_id.price_unit = price_unit
                            # tax_id = body.get('tax_id')
                            # if tax_id:
                            #     odoo_tax_id = request.env['account.tax'].search(
                            #         [('name', '=', tax_id), ('type_tax_use', '=', 'sale')])
                            #     if odoo_tax_id:
                            #         sale_order_line_id.tax_id = [(6, 0, [odoo_tax_id.id])]
                            quantity = body.get('quantity')
                            if quantity:
                                sale_order_line_id.product_uom_qty = quantity
                            args = {'status': "Success", 'code': "200"}
                        else:
                            args = {'status': "Sale order line id provided is not valid", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (sale_order_id)", 'code': "404"}
        return args

    @http.route("/api/cancelorder", type="json", auth="none", methods=['PUT'])
    def cancel_sale_order(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    sale_order = body.get('ref_id')
                    if sale_order:
                        sale_order_id = request.env['sale.order'].search([('id', '=', sale_order)])
                        if sale_order_id:
                            if sale_order_id.state in ['draft', 'sent']:
                                sale_order_id.action_cancel()
                                sale_order_id.message_post(body="Cancelled from the api '/api/cancelorder")
                                args = {'status': "Success", 'code': "200"}
                            else:
                                args = {'status': "You can only cancel sale order in draft state", 'code': "404"}
                        else:
                            args = {'status': "Sale order id provided is not valid", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (ref_id)", 'code': "404"}
        return args

    @http.route("/api/confirmorder", type="json", auth="none", methods=['PUT'])
    def confirm_sale_order(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            _logger.info(body)
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    sale_order = body.get('ref_id')
                    if sale_order:
                        sale_order_id = request.env['sale.order'].search([('id', '=', sale_order)])
                        if sale_order_id:
                            sale_order_id.send_total_amounts_hasura(sale_order_id)
                            sale_order_id.action_apply_package_discount()
                            if sale_order_id.is_blocked:
                                sale_order_id.state = 'pending'
                                args = {'status': "Success", 'code': "200"}
                            elif sale_order_id.partner_id.partner_type_id.is_cancel_order:
                                sale_order_id.action_cancel()
                                sale_order_id.message_post(body=f"Order is cancelled because we are not serving the sector {sale_order_id.partner_id.partner_type_id.name} currently")
                                args = {'status': "Success", 'code': "200"}
                            else:
                                sale_order_id.so_hasura_total_amount_verification()
                                past_orders = request.env['sale.order'].search(
                                    [('partner_id', '=', sale_order_id.partner_id.id),
                                     ('state', '=', 'sale')], limit=1)
                                expected_delivery_date_str = body.get('expected_delivery_date')
                                if expected_delivery_date_str:
                                    try:
                                        expected_delivery_date = datetime.strptime(str(expected_delivery_date_str),
                                                                                   '%Y/%d/%m')
                                    except ValueError:
                                        args = {'status': "Invalid date format for expected_delivery_date",
                                                'code': "400"}
                                        return args
                                    sale_order_id.write({'commitment_date': str(expected_delivery_date)})
                                if not "suplyd tech" in sale_order_id.partner_id.name:
                                    sale_order_id.action_confirm()
                                args = {'status': "Success", 'code': "200"}
                        else:
                            args = {'status': "Sale order id provided is not valid", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (ref_id)", 'code': "404"}
        _logger.info(args)
        return args

    def _get_start_date(self,interval):
        today = datetime.today()
        interval_mapping = {
            'DAILY': today,
            'WEEKLY': today - timedelta(days=7),
            'BI_WEEKLY': today - timedelta(days=15),
            'MONTHLY': today - timedelta(days=30),
        }
        return datetime.combine(interval_mapping.get(interval, today), today.time().min)

    def _check_max_order_interval(self,product_id, max_order_interval, partner_id, product_qty):
        start_date = self._get_start_date(max_order_interval)
        sale_order_line = request.env['sale.order.line'].search([
            ('order_id.partner_id', '=', partner_id),
            ('product_id', '=', product_id),
            ('state', '!=', 'cancel'),
            ('order_id.date_order', '>=', start_date),
        ])
        if sale_order_line:
            product_uom_qty = sum(sale_order_line.mapped('product_uom_qty'))
            if product_uom_qty >= product_qty:
                return True
        return False

    @http.route("/api/bulksaleorderline", type="json", auth="none", methods=['POST'])
    def bulk_create_sale_order_line(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        ids = []
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            sale_order_id = request.env['sale.order'].sudo().search([('id', '=', body.get('sale_order_id'))])
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            elif not sale_order_id:
                args = {'status': "Sale order id not provided or not found in the system"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    product_ids = body.get('product_ids')
                    expected_delivery_date = None
                    is_process_blocked = False
                    item_list = []
                    out_of_stock_products = []
                    max_order_products = []
                    if product_ids:
                        product_ref_ids = [product['product_id'] for product in product_ids]
                        products = request.env['product.product'].search([('id', 'in', product_ref_ids)])
                        products_by_id = {str(product.id): product for product in products}
                        for product in product_ids:
                            _logger.info(product)
                            _logger.info("***______________****")
                            pid = product["product_id"]
                            product_id = products_by_id.get(pid)
                            if 'expected_delivery_date' in product:
                                expected_delivery_date = product["expected_delivery_date"]
                                if expected_delivery_date:
                                    try:
                                        expected_delivery_date = datetime.strptime(str(expected_delivery_date),
                                                                                   '%Y/%d/%m')
                                    except ValueError:
                                        args = {'status': "Invalid date format for expected_delivery_date",
                                                'code': "400"}
                                        return args
                            quantity = product["quantity"]
                            product_package_qty = product["product_packaging_qty"]
                            is_package = product["is_package"]
                            discount = product["discount"]
                            product_package = False
                            if is_package:
                                product_package = request.env['product.packaging'].search(
                                    [('product_id', '=', product_id.id)], limit=1)
                            if is_package and not product_package:
                                args = {'status': "Product package not found", 'code': "404"}
                            else:
                                if not sale_order_id or not product_id or not quantity:
                                    args = {
                                        'status': "Required fields not provided (sale_order_id, quantity, product_id)",
                                        'code': "404"}
                                elif not product_id.allowed_beyond_stock and (product_id.detailed_type == 'product') and (quantity > product_id.qty_available_not_res):
                                    out_of_stock_products.append({
                                        'product_ref_id': product_id.id,
                                        'allowed_beyond_stock': product_id.allowed_beyond_stock,
                                        'stock_level': product_id.qty_available_not_res,
                                        'max_order_qty': product_id.max_order_quantity,
                                        'error': f'{product_id.name} is out of stock and not allowed beyond stock.'
                                    })

                                else:
                                    partner_id = sale_order_id.partner_id.id
                                    if product_id.max_order_quantity > 0 and product_id.max_order_interval:
                                        max_interval_exceeded = self._check_max_order_interval(product_id.id,
                                                                                               product_id.max_order_interval,
                                                                                               partner_id,
                                                                                               product_id.max_order_quantity)
                                        if max_interval_exceeded:
                                            max_order_products.append({
                                                'product_ref_id': product_id.id,
                                                'quantity': quantity,
                                                'max_order_qty': product_id.max_order_quantity,
                                                'error': f'Max order quantity {product_id.max_order_quantity} of {product_id.id} exceeded'
                                            })
                                    vals = {
                                        'product_id': product_id.id,
                                        'name': product_id.name,
                                        'product_uom_qty': quantity,
                                        'manual_qty': quantity,
                                        'price_unit': product_id.lst_price,
                                        'order_id': sale_order_id.id,
                                        'is_api': True
                                    }
                                    if expected_delivery_date:
                                        vals.update({
                                            'expected_delivery_date': expected_delivery_date,
                                        })
                                    if product_package:
                                        vals['product_packaging_id'] = product_package.id
                                        vals['product_packaging_qty'] = product_package_qty
                                    item_list.append(vals)
                        if out_of_stock_products and max_order_products:
                            args = {
                                'status': "Some products are out of stock and some products exceed max order quantity.",
                                'errors': out_of_stock_products + max_order_products,
                                'code': "304",
                                'error': {
                                    'code': '304',
                                    'message': 'ODOO_ERROR',
                                }

                            }
                        elif out_of_stock_products:
                            args = {
                                'status': "Some products are out of stock and not allowed beyond stock.",
                                'errors': out_of_stock_products,
                                'code': "304",
                                'error': {
                                    'code': '304',
                                    'message': 'ODOO_ERROR',
                                }

                            }
                        elif max_order_products:
                            args = {
                                'status': "Max order Quantity of some products is exceeded",
                                'errors': max_order_products,
                                'code': "304",
                                'error': {
                                    'code': '304',
                                    'message': 'ODOO_ERROR',
                                }

                            }
                        else:
                            sale_order_line_ids = request.env['sale.order.line'].create(item_list)
                            ids = [{'order_line_ref_id': str(x.id), 'product_ref_id': str(x.product_id.id)} for x in
                                   sale_order_line_ids]
                            args['status'] = "Success"
                            args['code'] = "200"
                            args['ids'] = ids
                else:
                    args = {'status': "Required fields not provided (product_id)", 'code': "404"}
        return args

    @http.route("/api/bulkdeletesaleorderline", type="json", auth="none", methods=['POST'])
    def remove_sale_order(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    order_id = body.get('order_id')
                    order_line = body.get('order_line_ids')
                    if order_id and order_line:
                        sale_order_id = request.env['sale.order'].search([('id', '=', order_id)])
                        order_line_ids = request.env['sale.order.line'].search([('id', 'in', order_line)])
                        for rec in order_line_ids:
                            sale_order_id.order_line = [(2, rec.id)]
                        args['status'] = "Success"
                        args['code'] = "200"
                    else:
                        args = {'status': "Required fields not provided (order_line_ids,order_id)", 'code': "404"}
        return args

    @http.route("/api/saleorderv2", type="json", auth="none", methods=['POST'])
    def create_sale_order_v2(self, **rec):
        args = {'status': "Bad Content", 'code': "404"}
        body = request.jsonrequest
        if body:
            db = body.get('db')
            username = body.get('username')
            password = body.get('password')
            if not db or not username or not password:
                args = {'status': "Credentials not provided", 'code': "401"}
            else:
                auth = self.login(db, username, password)
                if auth:
                    partner_id = False
                    item_list = []
                    branch_id = body.get('branch_id')
                    branch_id = request.env['res.partner'].search([('id', '=', branch_id)])
                    if branch_id:
                        partner_id = branch_id.parent_id
                    ordered_by = body.get('ordered_by')
                    credit_balance = body.get('use_credit_balance') if body.get('use_credit_balance') else 0
                    payment_term_id = body.get('payment_term_ref_id')
                    if payment_term_id:
                        payment_term_id = request.env['account.payment.term'].search([('name', '=', payment_term_id)])
                    else:
                        payment_term_id = partner_id.property_payment_term_id
                    expected_delivery_date = body.get('expected_delivery_date')
                    if expected_delivery_date:
                        expected_delivery_date = datetime.strptime(expected_delivery_date, '%Y/%d/%m')
                    product_ids = body.get('product_ids')
                    order_line_blocked = False
                    if partner_id:
                        sale_val = {
                            'partner_id': partner_id.id if partner_id else False,
                            'partner_invoice_id': partner_id.id,
                            'partner_shipping_id': branch_id.id,
                            'use_credit_balance': credit_balance,
                            'payment_term_id': payment_term_id.id if payment_term_id else False,
                            'ordered_by': ordered_by,
                            'commitment_date': expected_delivery_date,
                            'is_api': True
                        }
                        for product in product_ids:
                            if not order_line_blocked:
                                pid = product["id"]
                                if pid:
                                    product_id = request.env['product.product'].search([('id', '=', pid)])
                                    tax_id = product["tax_id"]
                                    tax_obj = request.env['account.tax'].search(
                                        [('name', '=', tax_id), ('type_tax_use', '=', 'sale')]) if tax_id else False
                                    quantity = product["quantity"]
                                    product_package_qty = product["product_packaging_qty"]
                                    is_package = product["is_package"]
                                    discount = product["discount"]
                                    product_package = False
                                    if is_package:
                                        product_package = request.env['product.packaging'].search(
                                            [('product_id', '=', product_id.id)], limit=1)
                                    if is_package and not product_package:
                                        args = {'status': "Product package not found", 'code': "404"}
                                    else:
                                        if not product_id or not quantity:
                                            args = {
                                                'status': "Required fields not provided quantity, product_id)",
                                                'code': "404"}
                                            order_line_blocked = True
                                        else:
                                            vals = {
                                                'product_id': product_id.id,
                                                'name': product_id.name,
                                                'product_uom_qty': quantity,
                                                'price_unit': product_id.lst_price,
                                                'is_api': True
                                            }
                                            if product_package:
                                                vals['product_packaging_id'] = product_package.id
                                                vals['product_packaging_qty'] = product_package_qty
                                            if discount:
                                                vals['discount'] = discount
                                            item_list.append((0, 0, vals))
                                else:
                                    args = {'status': "Product id not passed", 'code': "404"}
                                    order_line_blocked = True
                        if not order_line_blocked:
                            sale_val['order_line'] = item_list
                            sale_orders = request.env['sale.order'].search([('partner_id', '=', partner_id.id),('state', '!=', 'cancel')],
                                                                           limit=1)
                            if not sale_orders:
                                tag_id = request.env['sale.order.tag'].search([('name', '=', 'First Order')], limit=1)
                                if tag_id:
                                    sale_val['so_tag_ids'] = [(6, 0, [tag_id.id])]
                            sale_order_id = request.env['sale.order'].create(sale_val)
                            if sale_order_id:
                                line_vals = []
                                for rec in sale_order_id.order_line:
                                    line_vals.append({str(rec.product_id.id): rec.id})
                                args = {'status': "Success", 'code': "200", "id": sale_order_id.id,
                                        "sale_order_number": sale_order_id.name, "order_line_vals": line_vals}
                            else:
                                args = {'status': "Odoo Server Error", 'code': "404"}
                    else:
                        args = {'status': "Required fields not provided (partner_id, lines)", 'code': "404"}
        return args        