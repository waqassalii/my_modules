# -*- coding: utf-8 -*-
# from odoo import http


# class PoApproval(http.Controller):
#     @http.route('/po_approval/po_approval', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/po_approval/po_approval/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('po_approval.listing', {
#             'root': '/po_approval/po_approval',
#             'objects': http.request.env['po_approval.po_approval'].search([]),
#         })

#     @http.route('/po_approval/po_approval/objects/<model("po_approval.po_approval"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('po_approval.object', {
#             'object': obj
#         })

