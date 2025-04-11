# -*- coding: utf-8 -*-
{
    'name': "PO Send Approval",
    'summary': "this module sends the approval requests from current DB",
    'description': """ this module sends the approval requests from current DB""",
    'author': "Wayz",
    'website': "www.odoocoach.com",
    'version': '17.0.1.0.0',
    'depends': ['purchase','base'],
    'data': [
        'security/ir.model.access.csv',
        'data/acc_payment_seq.xml',
        'views/views.xml',
        'views/db_credential.xml',
        'views/res_partner.xml',
        'views/account_payment.xml',
    ],
}

