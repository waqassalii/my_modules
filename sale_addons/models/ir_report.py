from odoo import api, fields, models


class IrReport(models.AbstractModel):
    _name = 'report.sale_addons.sales_proforma_report'

    @api.model
    def render_html(self, docids, data=None):
        # Get the record(s)
        records = self.env['sale.order'].browse(docids)

        # Render the report, passing context
        report_context = {'record': record,
                          'remaining_order_line_ids': records.remaining_order_line_ids}
        return self.env.ref('sale_addons.sales_proforma_report').render(report_context)

