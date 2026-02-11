"""
Report views.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from datetime import datetime, timedelta

from .forms import DateRangeForm, TopProductsForm, InventoryTurnoverForm
from .models import UserWarehouseAccess
from .services.report_service import ReportService
from .services.export_service import ExportService
from .services.warehouse_scope_service import WarehouseScopeService
from .utils.logging import log_view_access
from .permissions.decorators import permission_required


def _ensure_report_module_access(user):
    WarehouseScopeService.ensure_any_warehouse_permission(
        user=user,
        required_permission=UserWarehouseAccess.PERMISSION_REPORT_VIEW,
        error_message='您无权访问报表中心',
        code='warehouse_scope_denied',
    )


def _resolve_report_scope(request):
    _ensure_report_module_access(request.user)
    warehouse_param = (
        request.POST.get('warehouse')
        if request.method == 'POST'
        else request.GET.get('warehouse', 'all')
    )
    return WarehouseScopeService.resolve_warehouse_selection(
        user=request.user,
        warehouse_param=warehouse_param,
        include_all_option=True,
        required_permission=UserWarehouseAccess.PERMISSION_REPORT_VIEW,
    )


def _append_scope_context(context, scope):
    context.update({
        'warehouses': scope['warehouses'],
        'selected_warehouse': scope['selected_warehouse_value'],
        'selected_warehouse_obj': scope['selected_warehouse'],
        'warehouse_scope_label': scope['scope_label'],
    })
    return context


def _normalize_sale_type(raw_value):
    normalized = (raw_value or '').strip().lower()
    if normalized in ['retail', 'wholesale']:
        return normalized
    return 'all'


def _resolve_sale_type_filter(raw_value):
    normalized = _normalize_sale_type(raw_value)
    if normalized == 'all':
        return normalized, None
    return normalized, normalized


def _resolve_profit_sale_type_filter(raw_value):
    normalized = (raw_value or '').strip().lower()
    if normalized in ['retail', 'wholesale']:
        return normalized, normalized
    # 利润报表默认按零售口径，避免零售与批发混算造成误导。
    return 'retail', 'retail'

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def report_index(request):
    """
    Report index view. Redirects to new reports_index.
    """
    _ensure_report_module_access(request.user)
    return redirect('reports_index')

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def sales_trend_report(request):
    """
    Sales trend report view.
    """
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            period = form.cleaned_data['period']

            sale_type, sale_type_filter = _resolve_sale_type_filter(
                request.POST.get('sale_type', '')
            )

            # Get sales trend data
            sales_data = ReportService.get_sales_by_period(
                start_date=start_date,
                end_date=end_date,
                period=period,
                sale_type=sale_type_filter,
                warehouse_ids=warehouse_ids,
            )
            
            context = {
                'form': form,
                'sales_data': sales_data,
                'start_date': start_date,
                'end_date': end_date,
                'period': period,
                'sale_type': sale_type,
                'sale_type_label': {'all': '全部', 'retail': '零售', 'wholesale': '批发'}[sale_type],
            }
            return render(
                request,
                'inventory/reports/sales_trend.html',
                _append_scope_context(context, scope),
            )
    else:
        form = DateRangeForm()
        
        # Get default data for last 30 days
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()

        sale_type, sale_type_filter = _resolve_sale_type_filter(
            request.GET.get('sale_type', '')
        )

        # Get sales trend data
        sales_data = ReportService.get_sales_by_period(
            start_date=start_date,
            end_date=end_date,
            period='day',
            sale_type=sale_type_filter,
            warehouse_ids=warehouse_ids,
        )
        
        context = {
            'form': form,
            'sales_data': sales_data,
            'start_date': start_date,
            'end_date': end_date,
            'period': 'day',
            'sale_type': sale_type,
            'sale_type_label': {'all': '全部', 'retail': '零售', 'wholesale': '批发'}[sale_type],
        }
        return render(
            request,
            'inventory/reports/sales_trend.html',
            _append_scope_context(context, scope),
        )

def _map_top_products(raw_data):
    """将 ReportService 返回的键名映射为模板期望的格式"""
    return [
        {
            'name': p.get('product__name') or '-',
            'code': p.get('product__barcode') or '-',
            'quantity_sold': p.get('total_quantity') or 0,
            'total_sales': p.get('total_sales') or 0,
            'profit': p.get('profit') or 0,
            'profit_margin': p.get('profit_margin') or 0,
        }
        for p in raw_data
    ]


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def top_products_report(request):
    """
    Top selling products report view.
    """
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    if request.method == 'POST':
        form = TopProductsForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            limit = form.cleaned_data['limit']

            sale_type, sale_type_filter = _resolve_sale_type_filter(
                request.POST.get('sale_type', '')
            )

            raw_data = ReportService.get_top_selling_products(
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                sale_type=sale_type_filter,
                warehouse_ids=warehouse_ids,
            )
            top_products = _map_top_products(raw_data)

            context = {
                'form': form,
                'top_products': top_products,
                'start_date': start_date,
                'end_date': end_date,
                'sale_type': sale_type,
                'sale_type_label': {'all': '全部', 'retail': '零售', 'wholesale': '批发'}[sale_type],
            }
            return render(
                request,
                'inventory/reports/top_products.html',
                _append_scope_context(context, scope),
            )
    else:
        form = TopProductsForm()
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        limit = 10

        sale_type, sale_type_filter = _resolve_sale_type_filter(
            request.GET.get('sale_type', '')
        )

        raw_data = ReportService.get_top_selling_products(
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            sale_type=sale_type_filter,
            warehouse_ids=warehouse_ids,
        )
        top_products = _map_top_products(raw_data)

        context = {
            'form': form,
            'top_products': top_products,
            'start_date': start_date,
            'end_date': end_date,
            'sale_type': sale_type,
            'sale_type_label': {'all': '全部', 'retail': '零售', 'wholesale': '批发'}[sale_type],
        }
        return render(
            request,
            'inventory/reports/top_products.html',
            _append_scope_context(context, scope),
        )

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def inventory_turnover_report(request):
    """
    Inventory turnover report view.
    """
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    if request.method == 'POST':
        form = InventoryTurnoverForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            category = form.cleaned_data['category']
            
            # Get inventory turnover data
            inventory_data = ReportService.get_inventory_turnover_rate(
                start_date=start_date,
                end_date=end_date,
                category=category,
                warehouse_ids=warehouse_ids,
            )
            
            context = {
                'form': form,
                'inventory_data': inventory_data,
                'start_date': start_date,
                'end_date': end_date
            }
            return render(
                request,
                'inventory/reports/inventory_turnover.html',
                _append_scope_context(context, scope),
            )
    else:
        form = InventoryTurnoverForm()
        
        # Get default data for last 30 days
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        
        # Get inventory turnover data
        inventory_data = ReportService.get_inventory_turnover_rate(
            start_date=start_date,
            end_date=end_date,
            warehouse_ids=warehouse_ids,
        )
        
        context = {
            'form': form,
            'inventory_data': inventory_data,
            'start_date': start_date,
            'end_date': end_date
        }
        return render(
            request,
            'inventory/reports/inventory_turnover.html',
            _append_scope_context(context, scope),
        )

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def profit_report(request):
    """
    Profit report view.
    模板需要 summary（汇总卡片）和 profit_data（按时间分组的明细列表）。
    """
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            period = form.cleaned_data.get('period', 'day')

            sale_type, sale_type_filter = _resolve_profit_sale_type_filter(
                request.POST.get('sale_type', '')
            )

            sales_by_period = ReportService.get_sales_by_period(
                start_date=start_date,
                end_date=end_date,
                period=period,
                sale_type=sale_type_filter,
                warehouse_ids=warehouse_ids,
            )
            profit_data = [
                {
                    'period': d['period'],
                    'sales': d['total_sales'] or 0,
                    'cost': d['total_cost'] or 0,
                    'profit': d['profit'] or 0,
                    'profit_margin': d.get('profit_margin', 0) or 0,
                    'order_count': d.get('order_count', 0) or 0,
                }
                for d in sales_by_period
            ]

            report = ReportService.get_profit_report(
                start_date=start_date,
                end_date=end_date,
                sale_type=sale_type_filter,
                warehouse_ids=warehouse_ids,
            )
            summary = {
                'total_sales': report['total_sales'],
                'total_cost': report['total_cost'],
                'total_profit': report['gross_profit'],
                'avg_profit_margin': report['profit_margin'],
            }

            context = {
                'form': form,
                'summary': summary,
                'profit_data': profit_data,
                'start_date': start_date,
                'end_date': end_date,
                'period': period,
                'sale_type': sale_type,
                'sale_type_label': {'retail': '零售', 'wholesale': '批发'}[sale_type],
            }
            return render(
                request,
                'inventory/reports/profit.html',
                _append_scope_context(context, scope),
            )
    else:
        form = DateRangeForm()
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        period = 'day'

        sale_type, sale_type_filter = _resolve_profit_sale_type_filter(
            request.GET.get('sale_type', '')
        )

        sales_by_period = ReportService.get_sales_by_period(
            start_date=start_date,
            end_date=end_date,
            period=period,
            sale_type=sale_type_filter,
            warehouse_ids=warehouse_ids,
        )
        profit_data = [
            {
                'period': d['period'],
                'sales': d['total_sales'] or 0,
                'cost': d['total_cost'] or 0,
                'profit': d['profit'] or 0,
                'profit_margin': d.get('profit_margin', 0) or 0,
                'order_count': d.get('order_count', 0) or 0,
            }
            for d in sales_by_period
        ]

        report = ReportService.get_profit_report(
            start_date=start_date,
            end_date=end_date,
            sale_type=sale_type_filter,
            warehouse_ids=warehouse_ids,
        )
        summary = {
            'total_sales': report['total_sales'],
            'total_cost': report['total_cost'],
            'total_profit': report['gross_profit'],
            'avg_profit_margin': report['profit_margin'],
        }

        context = {
            'form': form,
            'summary': summary,
            'profit_data': profit_data,
            'start_date': start_date,
            'end_date': end_date,
            'period': period,
            'sale_type': sale_type,
            'sale_type_label': {'retail': '零售', 'wholesale': '批发'}[sale_type],
        }
        return render(
            request,
            'inventory/reports/profit.html',
            _append_scope_context(context, scope),
        )

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def member_analysis_report(request):
    """
    Member analysis report view.
    """
    _ensure_report_module_access(request.user)
    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            
            # 处理导出Excel请求
            if 'export_excel' in request.POST:
                member_data = ReportService.get_member_analysis(
                    start_date=start_date,
                    end_date=end_date
                )
                return ExportService.export_member_analysis(member_data, start_date, end_date)
            
            # Get member analysis data
            member_data = ReportService.get_member_analysis(
                start_date=start_date,
                end_date=end_date
            )
            
            return render(request, 'inventory/reports/member_analysis.html', {
                'form': form,
                'member_data': member_data,
                'start_date': start_date,
                'end_date': end_date
            })
    else:
        form = DateRangeForm()
        
        # Get default data for last 30 days
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        
        # Get member analysis data
        member_data = ReportService.get_member_analysis(
            start_date=start_date,
            end_date=end_date
        )
        
        return render(request, 'inventory/reports/member_analysis.html', {
            'form': form,
            'member_data': member_data,
            'start_date': start_date,
            'end_date': end_date
        })

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def recharge_report(request):
    """
    Member recharge report view.
    """
    _ensure_report_module_access(request.user)
    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            
            # 获取会员充值数据
            recharge_data = ReportService.get_recharge_report(
                start_date=start_date,
                end_date=end_date
            )
            
            return render(request, 'inventory/reports/recharge_report.html', {
                'form': form,
                'recharge_data': recharge_data,
                'start_date': start_date,
                'end_date': end_date
            })
    else:
        form = DateRangeForm()
        
        # 默认显示最近30天的数据
        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        
        # 获取会员充值数据
        recharge_data = ReportService.get_recharge_report(
            start_date=start_date,
            end_date=end_date
        )
        
        return render(request, 'inventory/reports/recharge_report.html', {
            'form': form,
            'recharge_data': recharge_data,
            'start_date': start_date,
            'end_date': end_date
        })

@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def operation_log_report(request):
    """
    Operation log report view.
    """
    _ensure_report_module_access(request.user)
    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            
            # 获取操作日志数据
            log_data = ReportService.get_operation_logs(
                start_date=start_date,
                end_date=end_date
            )
            
            return render(request, 'inventory/reports/operation_log.html', {
                'form': form,
                'log_data': log_data,
                'start_date': start_date,
                'end_date': end_date
            })
    else:
        form = DateRangeForm()
        
        # 默认显示最近7天的日志
        start_date = timezone.now().date() - timedelta(days=7)
        end_date = timezone.now().date()
        
        # 获取操作日志数据
        log_data = ReportService.get_operation_logs(
            start_date=start_date,
            end_date=end_date
        )
        
        return render(request, 'inventory/reports/operation_log.html', {
            'form': form,
            'log_data': log_data,
            'start_date': start_date,
            'end_date': end_date
        }) 
