# -*- coding: utf-8 -*-

from odoo import models, _
from graphqlclient import GraphQLClient
from odoo.exceptions import UserError
from urllib.error import URLError
import logging
import json
import jwt

_logger = logging.getLogger(__name__)

class HasuraMixin(models.AbstractModel):
    _name = 'hasura.mixin'
    
    def run_query(self, query,variable, return_data=False, delivery=False):
        graphql_link = 'hasura' if not delivery else 'delivery'
        admin_secret_key = 'adminsecret' if not delivery else 'deliveryadminsecret'
        graphql = self.env['ir.config_parameter'].sudo().get_param(graphql_link)
        admin_secret = self.env['ir.config_parameter'].sudo().get_param(admin_secret_key)
        if not graphql or not admin_secret:
            raise UserError(_("Hasura not configured. Please contact administrator"))
        client = GraphQLClient(graphql)
        if delivery:
            secret_key = admin_secret
            encoded_jwt = jwt.encode({"service_name": "ODOO"},secret_key)
            client.inject_token(encoded_jwt, 'x-suplyd-odoo-service-token')
        else:
            client.inject_token(admin_secret,'x-hasura-admin-secret')
        try:
            _logger.info(query)
            _logger.info(variable)
            result = client.execute(query, variable)
            if return_data:
                return result
        except URLError:
            raise UserError(_(f"query: {query}\n variable: {json.dumps(variable)} \nSomething is wrong with the backend. Please try again after sometime."))
        _logger.info(result)
        if "error" in result:
            errors = json.loads(result).get('errors')
            if errors:
                error_message = f"query:\n {query}\n variable: \n{json.dumps(variable)} \n \nIssue sending data to the backend. Please contact the Administrator.\n"
                error_message = f"{error_message}\n Error: {result}"
                _logger.exception(error_message)
                raise UserError(_(error_message))

    def prepare_query(self, mutation,mut_input,var_input,return_msg):
        query = f"""mutation {mutation}($object:{mut_input}){{
                        {mutation}({var_input}:$object) {{
                                {return_msg}
                        }}
                }}"""
        return query
    
    def get_aws_key(self):
        key = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("aws_access_key_id")
        )
        secret = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("aws_secret_access_key")
        )
        return key, secret
     
    def check_backend_status(self):
        return True