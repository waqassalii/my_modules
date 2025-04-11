from odoo import models, fields, api, _
from graphqlclient import GraphQLClient
from odoo.exceptions import UserError
import odoo.addons.s3_addons.s3_helper as s3


class AdvertisingProgram(models.Model):
    _name = "advertising.program"
    _inherit = ['hasura.mixin']
    _description = "Advertising program"
    _rec_name = "name"

    name = fields.Char(string="Name")
    image = fields.Binary(string="Image")
    image_url = fields.Char(string="Image url")
    product_ids = fields.Many2many(comodel_name="product.product", relation="product_advertising_rel", column1="advertising_id", column2="product_id", string="Products")
    active = fields.Boolean(default=True)
    start_date = fields.Datetime(string='Start Time')
    end_date = fields.Datetime(string='End Time')
    page = fields.Char(string="Page", default="Home")
    rank = fields.Integer(string="Rank", default=9999)
    
    @api.constrains('product_ids')
    def _check_product_ids(self):
        for record in self:
            if not len(record.product_ids) >= 2:
                raise UserError(_("There should be atlest 2 products"))
            
    @api.model
    def create(self, vals):
        res = super(AdvertisingProgram, self).create(vals)   
        query = self.prepare_query("upsertAdvertisingProgram","UpsertAdvertisingProgramInputDTO!","input","ok")
        if res.image:
            key = self.env['ir.config_parameter'].sudo().get_param('aws_access_key_id')
            secret = self.env['ir.config_parameter'].sudo().get_param('aws_secret_access_key')
            if not key or not secret:
                raise UserError(_("Aws credentials not provided, please contact administrator"))
            image_url = s3.upload_base64_file(str(res.image)[1:], key, secret)
        data_var = {
            'banner_url': image_url,
            'name': res.name,
            'product_ref_ids': [ int(x.id) for x in res.product_ids],
            'rank': int(res.rank),
            'ref_id': int(res.id),
            'page': "Home",
            'is_active': True
        }       
        variable = {
            "object": data_var
        } 
        # if res.start_date:
        #     variable['start_date'] = res.start_date.isoformat(),
        # if res.end_date:
        #     variable['end_date'] = res.end_date.isoformat()
        self.run_query(query, variable, delivery=True)
        return res

    def write(self, values):
        res = super(AdvertisingProgram, self).write(values)
        if values:
            query = self.prepare_query("upsertAdvertisingProgram","UpsertAdvertisingProgramInputDTO!","input","ok")
            key = self.env['ir.config_parameter'].sudo().get_param('aws_access_key_id')
            secret = self.env['ir.config_parameter'].sudo().get_param('aws_secret_access_key')
            if not key or not secret:
                raise UserError(_("Aws credentials not provided, please contact administrator"))
            if values.get('image'):
                image_url = s3.upload_base64_file(values.get('image'), key, secret)
            else:
                image_url = s3.upload_base64_file(str(self.image)[1:], key, secret)
            variable = {
                'banner_url': image_url,
                'name': values.get('name', self.name),
                'product_ref_ids': [ x.id for x in self.product_ids],
                'rank': values.get('rank', self.rank),
                'ref_id': self.id,  
                'page': "Home",
                'is_active': values.get('rank', self.active),
            }        
            if values.get('page'):
                variable['page'] = res.page
            # if values.get('start_date'):
            #     variable['start_date'] = values.get('start_date').isoformat(),
            # if values.get('end_date'):
            #     variable['end_date'] = values.get('end_date').isoformat()
            data = {
                "object": variable
            }
            self.run_query(query, data, delivery=True)
        return res