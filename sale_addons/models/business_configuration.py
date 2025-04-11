from odoo import models, fields, api, _
from graphqlclient import GraphQLClient
from odoo.exceptions import UserError
import odoo.addons.s3_addons.s3_helper as s3


class BusinessConfiguration(models.Model):
    _name = "business.configuration"
    _inherit = ['hasura.mixin']
    _description = "abstract attributes"

    name = fields.Char(string="Name")
    coins_to_value_conversion = fields.Integer(string="Coins to Value Conversion")

    def create_business_configuration_new_backend(self, res):
        query = """
               mutation createBusinessConfigFromOdoo($object: CreateBusinessConfigInputByOdooDTO!) {
                  createBusinessConfigFromOdoo(business_config_input: $object) {
                    message
                    ok
                  }
                }
           """
        object = {
            "ref_id": res.id,
            "name": res.name,
            "coins_to_value_conversion": res.coins_to_value_conversion
        }
        variables = {"object": object}
        res.run_query(query, variables, False, True)

    def update_business_configuration_new_backend(self, values):
        query = """
          mutation updateBusinessConfigFromOdoo($object: UpdateBusinessConfigInputByOdooDTO!) {
                  updateBusinessConfigFromOdoo(business_config_update_input: $object) {
                    message
                  }
                }
        """
        object = {
            "ref_id": self.id,
            "name": values.get("name", self.name),
            "coins_to_value_conversion": values.get('coins_to_value_conversion', self.coins_to_value_conversion)
        }
        variables = {"object": object}
        self.run_query(query, variables, False, True)

    @api.model
    def create(self, vals):
        res = super(BusinessConfiguration, self).create(vals)
        self.create_business_configuration_new_backend(res)
        return res

    def write(self, values):
        res = super(BusinessConfiguration, self).write(values)
        if values:
            self.update_business_configuration_new_backend(values)
        return res