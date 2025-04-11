from odoo import models, fields, api, _
from graphqlclient import GraphQLClient
from odoo.exceptions import UserError
import odoo.addons.s3_addons.s3_helper as s3


class HotSearchItems(models.Model):
    _name = "hot.search.items"
    _inherit = ['hasura.mixin']
    _description = "Hot search items"
    _rec_name = "name"

    name = fields.Char(string="Name")
    arabic_name = fields.Char(string="Arabic Name")
    image = fields.Binary(string="Image")
    is_hot = fields.Boolean(string="Hot")
    rank = fields.Integer(string="Rank", default=9999)
    active = fields.Boolean(string="Active")

    @api.model
    def create(self, vals):
        res = super(HotSearchItems, self).create(vals)   
        query = self.prepare_query("upsertHotSearchItem","HotSearchItemInputDTO!","input","ok")
        if res.image:
            key = self.env['ir.config_parameter'].sudo().get_param('aws_access_key_id')
            secret = self.env['ir.config_parameter'].sudo().get_param('aws_secret_access_key')
            if not key or not secret:
                raise UserError(_("Aws credentials not provided, please contact administrator"))
            image_url = s3.upload_base64_file(str(res.image)[1:], key, secret)
        data_var = {
            'image_url': image_url,
            'name': res.name,
            'arabic_name': res.arabic_name,
            'rank': int(res.rank),
            'ref_id': int(res.id),
            'is_hot': res.is_hot,
            'is_active': True
        }       
        variable = {
            "object": data_var
        } 
        self.run_query(query, variable, delivery=True)
        return res

    def write(self, values):
        res = super(HotSearchItems, self).write(values)
        if values:
            query = self.prepare_query("upsertHotSearchItem","HotSearchItemInputDTO!","input","ok")
            key = self.env['ir.config_parameter'].sudo().get_param('aws_access_key_id')
            secret = self.env['ir.config_parameter'].sudo().get_param('aws_secret_access_key')
            if not key or not secret:
                raise UserError(_("Aws credentials not provided, please contact administrator"))
            if values.get('image'):
                image_url = s3.upload_base64_file(values.get('image'), key, secret)
            else:
                image_url = s3.upload_base64_file(str(self.image)[1:], key, secret)
            variable = {
                'image_url': image_url,
                'name': values.get('name', self.name),
                'arabic_name': values.get('name', self.arabic_name),
                'rank': values.get('rank', self.rank),
                'ref_id': self.id,  
                'is_hot': values.get('is_hot', self.is_hot),
                'is_active': values.get('active', self.active)
            }        
            data = {
                "object": variable
            }
            self.run_query(query, data, delivery=True)
        return res

    def unlink(self):
        query = """
            mutation deleteHotSearch($ref_id: Float!) {
                deleteHotSearchItem(ref_id: $ref_id) {
                    ok
                }
            }
        """
        for record in self:
            variable = {
                "ref_id": float(record.id)
            }
            self.run_query(query, variable,delivery=True)
        return super(HotSearchItems, self).unlink()