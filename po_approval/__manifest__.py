# -*- coding: utf-8 -*-
{
    'name': "Purchase Requisition",
    'summary': "this module accepts the approval requests from other DBs",
    'description': """ this module accepts the approval requests from other DBs """,
    'author': "Wayz",
    'website': "www.odoocoach.com",
    'version': '17.0.1.0.0',
    'depends': ['base','mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/views.xml',
        'wizard/cancel_reason_wizard.xml',
        'wizard/send_back_wizard.xml',
    ],
}

