from odoo import models, fields, api


class DbCredentials(models.Model):
    _name = 'db.credential'
    _description = 'this model contains the credentials of syncing db'

    url = fields.Char(string='URL')
    db_name = fields.Char(string='Data Base Name')
    user_name = fields.Char(string='User Name')
    user_login = fields.Char(string='User Login')
    user_password = fields.Char(string='Password')
    current_url = fields.Char(string='Current DB URL')
    current_db_name = fields.Char(string='Current DB Name')
    current_user_name = fields.Char(string='Current DB User Name')
    current_user_login = fields.Char(string='Current DB User login')
    current_user_password = fields.Char(string='Current DBPassword')
