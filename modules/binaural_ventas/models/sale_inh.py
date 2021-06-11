# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import datetime, timedelta
from functools import partial
from itertools import groupby
from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import formatLang, get_lang
from odoo.osv import expression
from odoo.tools import float_is_zero, float_compare
from werkzeug.urls import url_encode
import logging
_logger = logging.getLogger(__name__)


class SaleOrderBinauralVentas(models.Model):
    _inherit = 'sale.order'
    
    @api.onchange('filter_partner')
    def get_domain_partner(self):
        for record in self:
            record.partner_id = False
            if record.filter_partner == 'customer':
                return {'domain': {
                    'partner_id': [('customer_rank', '>=', 1)],
                }}
            elif record.filter_partner == 'supplier':
                return {'domain': {
                    'partner_id': [('supplier_rank', '>=', 1)],
                }}
            elif record.filter_partner == 'contact':
                return {'domain': {
                    'partner_id': [('supplier_rank', '=', 0), ('customer_rank', '=', 0)],
                }}
            else:
                return []

    phone = fields.Char(string='Teléfono', related='partner_id.phone')
    vat = fields.Char(string='RIF', compute='_get_vat')
    address = fields.Char(string='Dirección', related='partner_id.street')
    business_name = fields.Char(string='Razón Social', related='partner_id.business_name')
    
    amount_by_group = fields.Binary(string="Tax amount by group",compute='_compute_invoice_taxes_by_group',help='Edit Tax amounts if you encounter rounding issues.')
    partner_id = fields.Many2one(
        'res.partner', string='Customer', readonly=True,
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
        required=True, change_default=True, index=True, tracking=1)
    filter_partner = fields.Selection([('customer', 'Clientes'), ('supplier', 'Proveedores'), ('contact', 'Contactos')],\
                                      string='Filtro de Contacto', default='customer')
    
    amount_by_group_base = fields.Binary(string="Tax amount by group",compute='_compute_invoice_taxes_by_group',help='Edit Tax amounts if you encounter rounding issues.')

    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency',
        readonly=True, store=True,
        help='Utility field to express amount currency')
    # Foreing cyrrency fields
    foreign_currency_id = fields.Many2one('res.currency', default=lambda self: self.env.ref('base.USD').id,
                                          tracking=True)
    foreign_currency_rate = fields.Float(string="Tasa", compute='_compute_foreign_currency_rate',
                                         inverse='_inverse_foreign_currency_rate', tracking=True)
    foreign_currency_date = fields.Date(string="Fecha", default=fields.Date.today(), tracking=True)
    # fields convert
    # amount_untaxed_foreing = fields.Monetary(string='tes', compute='_amount_untaxed_coin')

    # @api.depends('amount_untaxed', 'foreign_currency_id')
    # def _amount_untaxed_coin(self):
    #     for record in self:
    #         print("------>", record.currency_id + record.foreign_currency_id)
            # if not record.foreign_currency_id.is_zero(record.amount_untaxed):
                    #order.currency_id._convert(record.amount_untaxed, record.company_id, record.company_id, fields.Date.today())

            # else:
            #    amount_untaxed = 0
            # record.amount_untaxed = 0

    @api.depends('foreign_currency_id', 'foreign_currency_date')
    def _compute_foreign_currency_rate(self):
        for record in self:
            rate = 0
            test_rate = record.currency_id._get_rates(record.company_id, record.foreign_currency_date)
            test_rate.update({record.foreign_currency_id.id: record.foreign_currency_rate})
            print("------>", test_rate)
            # if record.foreign_currency_id and record.foreign_currency_date:
            #     currency_rate = self.env['res.currency.rate'].search([
            #         ('currency_id', '=', record.foreign_currency_id.id), ('name', '=', record.foreign_currency_date)])
            #     if currency_rate.exists():
            #         rate = currency_rate
            record.foreign_currency_rate = rate

    def _inverse_foreign_currency_rate(self):
        for record in self:
            record.foreign_currency_rate = record.foreign_currency_rate

    @api.depends('partner_id')
    def _get_vat(self):
        for p in self:
            if p.partner_id.prefix_vat and p.partner_id.vat:
                vat = str(p.partner_id.prefix_vat) + str(p.partner_id.vat)
            else:
                vat = str(p.partner_id.vat)
            p.vat = vat.upper()

    @api.depends('order_line.price_subtotal', 'order_line.price_tax', 'order_line.tax_id', 'partner_id', 'currency_id')
    def _compute_invoice_taxes_by_group(self):
        ''' Helper to get the taxes grouped according their account.tax.group.
        This method is only used when printing the invoice.
        '''
        _logger.info("se ejecuto la funcion:_compute_invoice_taxes_by_group")
        for move in self:
            lang_env = move.with_context(lang=move.partner_id.lang).env
            tax_lines = move.order_line.filtered(lambda line: line.tax_id)
            tax_balance_multiplicator = 1 #-1 if move.is_inbound(True) else 1
            res = {}
            # There are as many tax line as there are repartition lines
            done_taxes = set()
            for line in tax_lines:
                res.setdefault(line.tax_id.tax_group_id, {'base': 0.0, 'amount': 0.0})
                _logger.info("line.price_subtotal en primer for %s",line.price_subtotal)
                res[line.tax_id.tax_group_id]['base'] += tax_balance_multiplicator * (line.price_subtotal if line.currency_id else line.price_subtotal)
                tax_key_add_base = tuple(move._get_tax_key_for_group_add_base(line))
                _logger.info("done_taxesdone_taxes %s",done_taxes)

                if line.currency_id and line.company_currency_id and line.currency_id != line.company_currency_id:
                    amount = line.company_currency_id._convert(line.price_tax, line.currency_id, line.company_id, line.date or fields.Date.context_today(self))
                else:
                    amount = line.price_tax
                res[line.tax_id.tax_group_id]['amount'] += amount
                """if tax_key_add_base not in done_taxes:
                    _logger.info("line.price_tax en primer for %s",line.price_tax)
                    if line.currency_id and line.company_currency_id and line.currency_id != line.company_currency_id:
                        amount = line.company_currency_id._convert(line.price_tax, line.currency_id, line.company_id, line.date or fields.Date.context_today(self))
                    else:
                        amount = line.price_tax
                    res[line.tax_id.tax_group_id]['amount'] += amount
                    # The base should be added ONCE
                    done_taxes.add(tax_key_add_base)"""

            # At this point we only want to keep the taxes with a zero amount since they do not
            # generate a tax line.
            zero_taxes = set()
            for line in move.order_line:
                for tax in line.tax_id.flatten_taxes_hierarchy():
                    if tax.tax_group_id not in res or tax.tax_group_id in zero_taxes:
                        res.setdefault(tax.tax_group_id, {'base': 0.0, 'amount': 0.0})
                        res[tax.tax_group_id]['base'] += tax_balance_multiplicator * (line.price_subtotal if line.currency_id else line.price_subtotal)
                        zero_taxes.add(tax.tax_group_id)

            _logger.info("res========== %s",res)

            res = sorted(res.items(), key=lambda l: l[0].sequence)
            move.amount_by_group = [(
                group.name, amounts['amount'],
                amounts['base'],
                formatLang(lang_env, amounts['amount'], currency_obj=move.currency_id),
                formatLang(lang_env, amounts['base'], currency_obj=move.currency_id),
                len(res),
                group.id
            ) for group, amounts in res]

            move.amount_by_group_base = [(
                group.name.replace("IVA", "Total G").replace("TAX", "Total G"), amounts['base'],
                amounts['amount'],
                formatLang(lang_env, amounts['base'], currency_obj=move.currency_id),
                formatLang(lang_env, amounts['amount'], currency_obj=move.currency_id),
                len(res),
                group.id
            ) for group, amounts in res]

    @api.model
    def _get_tax_key_for_group_add_base(self, line):
        """
        Useful for _compute_invoice_taxes_by_group
        must be consistent with _get_tax_grouping_key_from_tax_line
         @return list
        """
        return [line.tax_id.id]


class SaleOrderLineBinauralVentas(models.Model):
    _inherit = 'sale.order.line'
    
    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency',
        readonly=True, store=True,
        help='Utility field to express amount currency')


class SaleAdvancePaymentInvBinaural(models.TransientModel):
    _inherit = "sale.advance.payment.inv"
    
    def create_invoices(self):
        _logger.info('Creando factura')
        sale_orders = self.env['sale.order'].browse(self._context.get('active_ids', []))
        _logger.info('Ordenes')
        _logger.info(sale_orders)
        qty_max = int(self.env['ir.config_parameter'].sudo().get_param('qty_max'))
        _logger.info('QTY_MAX')
        _logger.info(qty_max)
        qty_lines = 0
        for order in sale_orders:
            qty_lines = len(order.order_line)
        _logger.info('QTY_LINES')
        _logger.info(qty_lines)
        if qty_max and qty_max <= qty_lines:
            qty_invoice = qty_lines / qty_max
        else:
            qty_invoice = 1
        if (qty_invoice - int(qty_invoice)) > 0:
            qty_invoice = int(qty_invoice) + 1
        else:
            qty_invoice = int(qty_invoice)
        _logger.info('QTY_INVOICE')
        _logger.info(qty_invoice)
        for i in range(0, qty_invoice):
            if self.advance_payment_method == 'delivered':
                sale_orders._create_invoices(final=self.deduct_down_payments)
            else:
                # Create deposit product if necessary
                if not self.product_id:
                    vals = self._prepare_deposit_product()
                    self.product_id = self.env['product.product'].create(vals)
                    self.env['ir.config_parameter'].sudo().set_param('sale.default_deposit_product_id',
                                                                     self.product_id.id)
                
                sale_line_obj = self.env['sale.order.line']
                for order in sale_orders:
                    amount, name = self._get_advance_details(order)
                    
                    if self.product_id.invoice_policy != 'order':
                        raise UserError(_(
                            'The product used to invoice a down payment should have an invoice policy set to "Ordered quantities". Please update your deposit product to be able to create a deposit invoice.'))
                    if self.product_id.type != 'service':
                        raise UserError(_(
                            "The product used to invoice a down payment should be of type 'Service'. Please use another product or update this product."))
                    taxes = self.product_id.taxes_id.filtered(
                        lambda r: not order.company_id or r.company_id == order.company_id)
                    tax_ids = order.fiscal_position_id.map_tax(taxes, self.product_id, order.partner_shipping_id).ids
                    analytic_tag_ids = []
                    for line in order.order_line:
                        analytic_tag_ids = [(4, analytic_tag.id, None) for analytic_tag in line.analytic_tag_ids]
                    
                    so_line_values = self._prepare_so_line(order, analytic_tag_ids, tax_ids, amount)
                    so_line = sale_line_obj.create(so_line_values)
                    self._create_invoice(order, so_line, amount)
        if self._context.get('open_invoices', False):
            return sale_orders.action_view_invoice()
        return {'type': 'ir.actions.act_window_close'}