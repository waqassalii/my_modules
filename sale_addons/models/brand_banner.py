from odoo import models, fields, api, _
from graphqlclient import GraphQLClient
from odoo.exceptions import UserError


class BrandBanner(models.Model):
    _name = "brand.banner"
    # _inherit = ['hasura.mixin']
    _description = "Brand Banner"
    _rec_name = "name"

    name = fields.Char(string="Name")
    image = fields.Binary(string="Image")
    mobile_deep_link = fields.Char(string="Mobile Deep Link")
    web_deep_link = fields.Char(string="Web Deep Link")