# -*- coding: utf-8 -*-
{
    'name': "Sale Addons",
    'description': """ Sale Addons """,
    'author': "Suplyd",
    'website': "http://www.suplyd.app",
    'category': 'Uncategorized',
    'version': '15.0.0.2',
    'depends': ["base",
                "sale",
                "hasura_addon",
                "l10n_eg_edi_eta",
                "sale_margin",
                'l10n_eg',
                'sale_order_tag',
                'delivery',
                'sp_product',
                'product_bundle_pack',
                'sp_coupon_program',
                'sp_contact',
                'timer','auth_jwt'
                ],
    'data': [
        'data/ir_cron.xml',
        'data/business_config.xml',
        'security/sales_security.xml',
        'security/ir.model.access.csv',
        'views/sale_order_view.xml',
        'views/sale_order_line_view.xml',
        'views/sale_order_search_view.xml',
        'views/account_move_views.xml',
        'views/delivery_carrier.xml',
        'views/sale_performa_report.xml',
        'views/sync_actions_view.xml',
        'views/tax_total_view.xml',
        'views/sale_order_server_action.xml',
        'views/res_company_views.xml',
        'views/stock_picking_views.xml',
        'views/brand_banner_view.xml',
        'views/hot_search_items.xml',
        'views/business_configuration.xml',
        'views/advertising_program_view.xml',
        'wizards/mark_delivered_wizard_view.xml',
        'wizards/cancel_order_wizard_view.xml',
    ],
    'post_load': 'post_load_hook',
    'installable': True
}
