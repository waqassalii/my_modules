# -*- coding: utf-8 -*-

from odoo import models, fields, _, api
import json
import logging
import bson

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'hasura.mixin']
    
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
        if package_id:
            product['product_packcage'] = {
                'ref_id': package_id.id,
                'package_name': package_id.name,
                'discount': package_id.discount,
                'product_conversion_count': package_id.qty,
                'conversion_units': package_id.product_uom_id.name,
                'arabic_name': package_id.arabic_name if package_id.arabic_name else None
            }
        return product
        
    def prepare_shipments_for_sale_sync(self):
        shipments = []
        pickings = self.picking_ids.filtered(lambda x: (x.picking_type_id.is_out_delivery or x.picking_type_id.is_dropship) and x.state != 'cancel')
        for rec in pickings:
            status = "PENDING"
            shipment_lines, total_vals = self.prepare_shipment_lines_for_sale_sync(rec)
            if rec.state == 'done':
                status = "DELIVERED"
            shipment_vals = {
                'ref_id': rec.id,
                'shipment_number': rec.shipment_number,
                'shipment_delivery_date':  rec.date_deadline.strftime("%Y-%m-%d"),
                'shipment_original_delivery_date': rec.date_deadline.strftime("%Y-%m-%d"),
                'actual_final_delivery_date': None,
                'is_reschedule_blocked': False,
                'status': status,
                'original_price_after_terms_fee': total_vals.get('total_original_price_after_terms'),
                'price_after_terms_after_discount': total_vals.get('price_after_terms_after_discount'),
                'price_after_terms_after_discount_after_tax': total_vals.get('price_after_terms_after_discount_after_tax'),
                'added_tax_amount_after_terms': total_vals.get('total_tax'),
                'discount_percentage': total_vals.get('total_discount_percentage'),
                'shipment_lines': shipment_lines
            }
            shipments.append(shipment_vals)
        return shipments

    def prepare_shipment_lines_for_sale_sync(self, move):
        shipment_lines = []
        total_original_price_after_terms = 0
        price_after_terms_after_discount  = 0
        price_after_terms_after_discount_after_tax = 0
        total_tax = 0
        total_discount_percentage = 0
        for rec in move.move_ids_without_package.filtered(lambda x: not x.product_pack_id):
            original_price = rec.sale_line_id.price_unit * rec.product_uom_qty
            original_price_after_terms = rec.sale_line_id.price_unit * rec.product_uom_qty
            total_original_price_after_terms += original_price_after_terms
            discount =  rec.sale_line_id.total_order_discount + rec.sale_line_id.product_specific_discount
            price_after_discount =  original_price_after_terms + discount
            price_after_terms_after_discount += price_after_discount
            added_tax = rec.sale_line_id.price_tax + rec.sale_line_id.tax_line 
            final_price = price_after_discount + added_tax
            total_tax += added_tax
            price_after_terms_after_discount_after_tax += final_price
            discount_percentage = 0 
            if discount > 0:
                discount_percentage = (discount/original_price) * 100
            total_discount_percentage += discount_percentage
            quantity_ordered = rec.product_uom_qty
            if rec.product_packaging_id:
                quantity_ordered = rec.sale_line_id.product_packaging_qty
            shipment_line = {
                'ref_id': rec.id,
                'quantity_ordered': quantity_ordered,
                'quantity_delivered': int(rec.product_uom_qty if rec.picking_id.state == 'done' else 0),
                'original_price': round(original_price,2), 
                'original_price_after_terms_fee': round(original_price_after_terms,2),
                'price_after_terms_after_discount': round(price_after_discount,2),
                'price_after_terms_after_discount_after_tax': round(final_price,2),
                'added_tax_amount_after_terms': round(added_tax,2),
                'product': self.prepare_product_for_lines(rec),
                'discount_percentage': abs(round(discount_percentage,2)),
                'adjusted_original_price': 0,
                'adjusted_price_after_discount': 0,
                'adjusted_price_after_discount_after_tax': 0,
                'adjusted_discount_percentage': 0,
                'backorder_quantity': 0
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
                org_after_terms = original_price + 0
                total_original_price_after_terms += org_after_terms
                price_after_terms_after_discount += item.price_subtotal
                price_after_terms_after_discount_after_tax += item.price_total
                discount = original_price - item.price_unit
                discount_percentage = 0
                if discount > 0:
                    discount_percentage = (discount/org_after_terms) * 100
                total_discount_percentage += discount_percentage
                shipment_line = {
                    '_id': str(obj_id),
                    'quantity_ordered': item.product_uom_qty,
                    'original_price': round(sum([rec.product_id.lst_price for rec in item.product_id.pack_ids]),2),
                    'original_price_after_terms_fee': round(org_after_terms,2),
                    'price_after_terms_after_discount': round(item.price_subtotal,2),
                    'price_after_terms_after_discount_after_tax': round(item.price_total,2),
                    'added_tax_amount_after_terms': round(item.price_tax,2),
                    'product': self.prepare_product_for_lines(item, True, no_image=True),
                    'discount_percentage': round(discount_percentage,2)
                }
                shipment_lines.append(shipment_line)
        total_vals = {
            'total_original_price_after_terms':total_original_price_after_terms,
            'price_after_terms_after_discount':price_after_terms_after_discount,
            'price_after_terms_after_discount_after_tax':price_after_terms_after_discount_after_tax,
            'total_discount_percentage':total_discount_percentage,
            'total_tax': total_tax
        }
        return shipment_lines, total_vals
                
    def get_all_sales(self):
        order_id = self.env['sale.order'].search([('date_order', '>', '2024-11-01'),
                                                ('state', 'not in', ['cancel', 'draft']),
                                                ('delivery_status', 'not in', ['Returned','Delivered']),                                                  
                                                ])
        orders = []
        for rec in order_id:
            sale_val = {
                "ref_id": rec.id,
                "shipments": rec.prepare_shipments_for_sale_sync(),
            }
            orders.append(sale_val)
        return orders
    
    def get_sale_ref(self, ref):
        order_id = self.env['sale.order'].search([('id','=', ref)])
        orders = []
        for rec in order_id:
            sale_val = {
                "ref_id": rec.id,
                "shipments": rec.prepare_shipments_for_sale_sync(),
            }
            orders.append(sale_val)
        return orders

    def get_all_sales_customers(self):
        query = """
            SELECT 
                so.id, 

                (
                    SELECT row_to_json(entity) AS entity 
                    FROM (
                        SELECT 
                            name, 
                            phone, 
                            id AS ref_id, 
                            active AS is_active 
                        FROM res_partner 
                        WHERE id = entity.id
                    ) AS entity
                ) AS entity, 

                (
                    SELECT row_to_json(branch) AS branch
                    FROM (
                        SELECT 
                            rl.name, 
                            rl.id AS ref_id, 
                            parent_id AS entity_ref_id, 
                            region_master_id AS region_ref_id, 
                            area_master_id AS area_ref_id, 
                            x_studio_location AS location, 
                            longitude, 
                            latitude, 
                            street AS address, 
                            receipient_name, 
                            active AS is_active, 

                            (
                                SELECT json_agg(time_window) AS time_window
                                FROM (
                                    SELECT 
                                        time_window_start, 
                                        time_window_end, 
                                        COALESCE(is_ramadan_time, FALSE) AS is_ramadan_window
                                    FROM branch_timing_windows
                                    WHERE partner_id = branch.id
                                ) AS time_window
                            ) AS time_window
                        FROM res_partner rl
                        WHERE id = branch.id
                    ) AS branch
                ) AS branch,

                CASE 
                    WHEN (
                        SELECT COUNT(*) 
                        FROM res_partner 
                        WHERE parent_id = branch.id 
                        AND type = 'contact' 
                        AND is_company = FALSE
                    ) > 0 THEN (
                        SELECT row_to_json(user_ref) AS user_ref
                        FROM (
                            SELECT 
                                rp.name, 
                                rp.phone, 
                                rp.id AS ref_id, 
                                rp.active AS is_active, 
                                CASE
                                    WHEN parent.type = 'delivery' THEN 'ENTITY_MANAGER'
                                    WHEN parent.type = 'invoice' THEN 'BRANCH_MANAGER'
                                    ELSE 'ENTITY_MANAGER'
                                END AS role
                            FROM res_partner rp
                            LEFT JOIN res_partner parent ON parent.id = rp.parent_id
                            WHERE rp.parent_id = branch.id 
                            AND rp.type = 'contact' 
                            AND rp.is_company = FALSE 
                            LIMIT 1
                        ) AS user_ref
                    )
                    WHEN (
                        SELECT COUNT(*) 
                        FROM res_partner 
                        WHERE parent_id = entity.id 
                        AND type = 'contact' 
                        AND is_company = FALSE
                    ) > 0 THEN (
                        SELECT row_to_json(user_ref) AS user_ref
                        FROM (
                            SELECT 
                                rp.name, 
                                rp.phone, 
                                rp.id AS ref_id, 
                                rp.active AS is_active, 
                                CASE
                                    WHEN parent.type = 'delivery' THEN 'ENTITY_MANAGER'
                                    WHEN parent.type = 'invoice' THEN 'BRANCH_MANAGER'
                                    ELSE 'ENTITY_MANAGER'
                                END AS role
                            FROM res_partner rp
                            LEFT JOIN res_partner parent ON parent.id = rp.parent_id
                            WHERE rp.parent_id = entity.id 
                            AND rp.type = 'contact' 
                            AND rp.is_company = FALSE 
                            LIMIT 1
                        ) AS user_ref
                    )
                    WHEN (
                        SELECT COUNT(*) 
                        FROM res_partner 
                        WHERE parent_id = 70485 
                        AND type = 'contact' 
                        AND is_company = FALSE
                    ) > 0 THEN (
                        SELECT row_to_json(user_ref) AS user_ref
                        FROM (
                            SELECT 
                                rp.name, 
                                rp.phone, 
                                rp.id AS ref_id, 
                                rp.active AS is_active, 
                                CASE
                                    WHEN parent.type = 'delivery' THEN 'ENTITY_MANAGER'
                                    WHEN parent.type = 'invoice' THEN 'BRANCH_MANAGER'
                                    ELSE 'ENTITY_MANAGER'
                                END AS role
                            FROM res_partner rp
                            LEFT JOIN res_partner parent ON parent.id = rp.parent_id
                            WHERE rp.parent_id = company.id 
                            AND rp.type = 'contact' 
                            AND rp.is_company = FALSE 
                            LIMIT 1
                        ) AS user_ref
                    )
                    ELSE NULL
                END AS customer

            FROM 
                sale_order so
            LEFT JOIN 
                res_partner entity ON so.partner_id = entity.id
            LEFT JOIN 
                res_partner branch ON so.partner_shipping_id = branch.id
            LEFT JOIN 
                res_partner company ON company.id = entity.parent_id
            WHERE 
                state = 'sale' 
                order by id desc"""
        data = self.env.cr.execute(query)
        data = self.env.cr.fetchall()
        orders = [
            {
                "ref_id": rec[0],
                "entity": rec[1],
                "branch": rec[2],
                "customer": rec[3]
            }
            for rec in data
        ]
        return orders

    def update_customers(self):
        data = [
            {"id": "c3556ad3-b1b7-4fb0-9a99-6823a5f6b55a", "value": 1635},
            {"id": "4fc55002-92ce-48eb-a7da-82aaa6e852aa", "value": 1714},
            {"id": "30a8cfc6-4e73-40fb-80c5-302651caa919", "value": 1716},
            {"id": "0d1b52b4-8174-4e62-bfdd-fc26916b61b4", "value": 1717},
            {"id": "5b01a185-aa30-49c9-807d-ca4d5bc3aa92", "value": 1718},
            {"id": "05dc8c46-04e7-4235-8436-298a19ad4ed2", "value": 1719},
            {"id": "eb2b660b-e587-4b97-aaf0-4c8992465590", "value": 1712},
            {"id": "4c3cba27-06da-48aa-a660-5a10f42a2972", "value": 1713},
            {"id": "700266df-f050-4ce1-a559-51050204eb04", "value": 1721},
            {"id": "f1abab03-48f1-4769-be7a-e62a8a50d3fd", "value": 1722},
            {"id": "7b0510dd-2c10-4098-8ecc-c056a1280161", "value": 1723},
            {"id": "92dbe625-3136-4a9e-9b67-48a310b2d0ae", "value": 1724},
            {"id": "1e9b9cc7-4ea0-4c59-bd24-78dd44c5d4a6", "value": 1727},
            {"id": "e520a1b5-51b5-48c4-9c12-2d4386c2eb7d", "value": 1730},
            {"id": "328f48e3-3365-416f-8489-ae6b16cf34d3", "value": 1729},
            {"id": "f9e29d42-cfce-49e1-8e5a-e5bfdf8ce4c8", "value": 1734},
            {"id": "42adf90c-0998-4b04-b8f5-bdf9b5fca739", "value": 1732},
            {"id": "ed8acb56-0829-453b-bfca-7741112649ab", "value": 1866},
            {"id": "95ccc25d-2e31-49d5-a8bb-bd27ec3e3744", "value": 1740},
            {"id": "5cef8e8d-e1d4-490a-9e81-05dc90be9175", "value": 1738},
            {"id": "d6458339-9238-45d0-9d65-5797c30b6bde", "value": 1739},
            {"id": "a89730ad-585d-41fc-86ec-b6cc6998a7e7", "value": 1821},
            {"id": "25fb1776-50bc-4cf8-bebb-71359cf29d2f", "value": 1742},
            {"id": "52714e28-7871-4a7c-9f05-1076d49f9a89", "value": 1746},
            {"id": "c3dd75da-822c-4433-89b6-43c126bb2c72", "value": 1747},
            {"id": "08a4bea5-221e-48aa-a407-7ad4aeb73f98", "value": 1748},
            {"id": "e527fc1b-baea-491a-8fb3-0775eb0ad276", "value": 1754},
            {"id": "664f5ca7-294c-4047-92d0-720fdee1a0b4", "value": 1753},
            {"id": "6259ed85-6ed6-4177-a7b4-36851fdf536d", "value": 1736},
            {"id": "83c2dd25-5362-484c-aa4b-2b215f010e2b", "value": 1673},
            {"id": "1d270ad4-52ce-408d-b98d-00efd193f628", "value": 1674},
            {"id": "f26c2a73-8bfc-42fb-8c81-e7d62b569023", "value": 1676},
            {"id": "ca05ad65-80b4-4a67-9b8c-7ad0a8652b9b", "value": 1680},
            {"id": "88bfc1c7-df77-4a63-8bed-2d656abaa463", "value": 1685},
            {"id": "0f6f4d3e-5894-40b3-8e8e-fbcf414a838c", "value": 1686},
            {"id": "6048f2be-2668-47d2-93c3-890694a748ef", "value": 750},
            {"id": "a7140c52-8096-48aa-b9e2-aa8d07891bf5", "value": 1690},
            {"id": "b37273ca-8a14-4ee5-9d81-ee0965dbaca3", "value": 1691},
            {"id": "3e4e09b1-b492-4bb9-9f51-b1541479045a", "value": 1788},
            {"id": "00e6419f-ee72-4170-b013-1b1add0be012", "value": 1693},
            {"id": "87ed3695-2b76-4dac-9454-1f541ceb9d50", "value": 1603},
            {"id": "83b21443-2847-474d-8feb-a2fe1889bd14", "value": 1696},
            {"id": "61f02e4c-debe-4aa3-937e-d56cca2950f6", "value": 1698},
            {"id": "77a0622d-0772-41e3-9a2a-9ad75a244051", "value": 1699},
            {"id": "96024f38-8c9d-4324-8411-78afd033c15a", "value": 1700},
            {"id": "efc50178-8a79-4a54-9a67-37ce4b1a058d", "value": 1701},
            {"id": "d328ed3e-f4c6-4f81-9373-4a92599fc656", "value": 1704},
            {"id": "ebff1f4f-4352-4b4e-84fc-b54c644ff4be", "value": 1702},
            {"id": "835e85fd-53ce-4c5b-bbb8-36058a6dd81c", "value": 1710},
            {"id": "917a2027-656d-442a-a790-7b12b1f8ba79", "value": 1707},
            {"id": "3953b1fe-3bac-4fe2-b2ca-12e48814d202", "value": 1709},
            {"id": "11d66b0a-287d-4c82-a56b-1ab557e4e4c4", "value": 1711},
            {"id": "cc15da02-0902-481d-8dbd-a8545162b3c4", "value": 1706},
            {"id": "568f64d8-ffe0-49e6-9ca3-73fd80995393", "value": 1835},
            {"id": "af2b5fa7-8e9e-4d1a-9e05-e11f02676899", "value": 1751},
            {"id": "6cd6e5e3-41bd-4ce5-bdfc-0ad275a0e880", "value": 1670},
            {"id": "c24d2d23-7973-4e3f-b6a9-34cf9090a8c2", "value": 10043},
            {"id": "be46e422-f771-4d68-b423-0795fbc22743", "value": 1708},
            {"id": "9ba2fbea-30ce-4dd1-9d91-03541c05750a", "value": 1720},
            {"id": "00d4322a-9768-4315-af65-ff434c2bf599", "value": 1688},
            {"id": "ce73cc7c-7bc0-4a27-b44b-0f66f53afcff", "value": 1733},
            {"id": "05e1e93b-1f77-4852-ac91-b0bccefd0984", "value": 908},
            {"id": "19f407c2-9eb7-4bdb-9dc7-27402f97bc9b", "value": 1741},
            {"id": "b0738e43-6fea-478f-98a7-76d187ef0126", "value": 1807},
            {"id": "9263dd84-2c50-4387-a81b-876bce4502de", "value": 1087},
            {"id": "68af6bd4-d799-4e7c-97bb-9fa077cf14d4", "value": 1679},
            {"id": "ba8cddfc-3f16-41ce-996f-b7065086d13d", "value": 1796},
            {"id": "a0feb813-3b86-4d61-b394-5e15c05b0150", "value": 1684},
            {"id": "aa88a39d-0d95-42ca-a0f9-09158e18b45a", "value": 823},
            {"id": "b0c26526-b106-4748-ad13-2005919dfcd7", "value": 1735},
            {"id": "d8ef274c-ba83-4679-bea8-af1e462af835", "value": 1495},
            {"id": "b47ff254-3953-4697-afb0-c28a1b67f73f", "value": 1743},
            {"id": "b86d9f32-7ad0-4014-a327-7c6415bf895a", "value": 1705},
            {"id": "43808506-1219-4387-99cd-4cceb24d1788", "value": 1675},
            {"id": "a2e138e4-0739-4177-bb2f-8fa8882632a0", "value": 1703},
            {"id": "58ea9e37-709a-4a9d-be07-58934ca9cdf8", "value": 1749},
            {"id": "a8e5cd31-32a0-4950-b8ab-9ac30c3cde58", "value": 1725}
        ]    
        objs = [] 
        ids = []
        for rec in data:
            company_id = self.env['res.partner'].search([('id', '=', rec["value"])])
            partner_id = self.env['res.partner'].search([('parent_id', '=', rec["value"]),('is_company','=', False),('type','=', 'contact')],limit=1)
            if not partner_id:
                partner_id = company_id.child_ids.child_ids.filtered(lambda x: not x.is_company and x.type == 'contact')
                if partner_id:
                    partner_id = partner_id[0]
            if partner_id:
                if partner_id.id not in [10544,4953,4292,1460,26012,26012,26012]:
                    update_object = {
                        "where": {
                            "id": {
                                "_eq": rec["id"]
                            }
                        },
                        "_set": {
                            "user_ref_id": str(partner_id.id)
                        }
                    }
                    ids.append(str(partner_id.id))
                    objs.append(update_object)
        query = """
            mutation userUpdates($updates: [users_updates!]!) {
            update_users_many(updates: $updates) {
                affected_rows
                returning {
                id
                }
            }
            }
        """
        variables = {
            "updates": objs
        }
        self.run_query(query, variables)