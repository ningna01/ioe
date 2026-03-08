"""
Report views.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Sum
from django.utils import timezone
from datetime import datetime, timedelta

from .forms import DateRangeForm, TopProductsForm, InventoryTurnoverForm
from .models import UserWarehouseAccess, DebtOrder, Sale, SaleItem
from .services.report_service import ReportService
from .services.export_service import ExportService
from .services.payable_service import PayableService
from .services.warehouse_scope_service import WarehouseScopeService
from .utils.query_utils import paginate_queryset, build_elided_page_range
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


def _today_report_initial(extra_initial=None):
    today = timezone.localdate()
    initial = {
        'start_date': today,
        'end_date': today,
        'date_range_preset': 'today',
    }
    if extra_initial:
        initial.update(extra_initial)
    return initial


def _parse_query_date(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None

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
        form = DateRangeForm(initial=_today_report_initial({'period': 'day'}))
        start_date = timezone.localdate()
        end_date = start_date

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
        form = TopProductsForm(initial=_today_report_initial({'limit': 10}))
        start_date = timezone.localdate()
        end_date = start_date
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
        form = InventoryTurnoverForm(initial=_today_report_initial())
        start_date = timezone.localdate()
        end_date = start_date
        
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
        form = DateRangeForm(initial=_today_report_initial({'period': 'day'}))
        start_date = timezone.localdate()
        end_date = start_date
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
        form = DateRangeForm(initial=_today_report_initial())
        start_date = timezone.localdate()
        end_date = start_date
        
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


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def stock_in_report(request):
    """入库报表：按零售价统计入库金额，并展示当前仓库库存总值。"""
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    raw_show_voided = (
        request.POST.get('show_voided')
        if request.method == 'POST'
        else request.GET.get('show_voided', '0')
    )
    show_voided = str(raw_show_voided or '').strip().lower() in {'1', 'true', 'on', 'yes'}

    if request.method == 'POST':
        form = DateRangeForm(request.POST)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            report_data = ReportService.get_stock_in_report(
                start_date=start_date,
                end_date=end_date,
                warehouse_ids=warehouse_ids,
                include_voided=show_voided,
            )
            context = {
                'form': form,
                'start_date': start_date,
                'end_date': end_date,
                'report_data': report_data,
                'show_voided': show_voided,
            }
            return render(
                request,
                'inventory/reports/stock_in.html',
                _append_scope_context(context, scope),
            )

    if request.GET:
        form = DateRangeForm(request.GET)
        if form.is_valid():
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
        else:
            form = DateRangeForm(initial=_today_report_initial())
            start_date = timezone.localdate()
            end_date = start_date
    else:
        form = DateRangeForm(initial=_today_report_initial())
        start_date = timezone.localdate()
        end_date = start_date

    report_data = ReportService.get_stock_in_report(
        start_date=start_date,
        end_date=end_date,
        warehouse_ids=warehouse_ids,
        include_voided=show_voided,
    )
    context = {
        'form': form,
        'start_date': start_date,
        'end_date': end_date,
        'report_data': report_data,
        'show_voided': show_voided,
    }
    return render(
        request,
        'inventory/reports/stock_in.html',
        _append_scope_context(context, scope),
    )


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def all_sales_report(request):
    """全部销售订单报表：支持全历史检索与分页浏览。"""
    scope = _resolve_report_scope(request)
    today = timezone.now().date()

    base_sales = Sale.objects.select_related('operator', 'warehouse').prefetch_related('items').order_by('-created_at')
    base_sales = WarehouseScopeService.filter_sales_queryset(
        request.user,
        base_sales,
        required_permission=UserWarehouseAccess.PERMISSION_REPORT_VIEW,
    )

    search_query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    date_scope_raw = request.GET.get('date_scope')
    if date_scope_raw is None and (date_from or date_to):
        date_scope = ''
    else:
        date_scope = (date_scope_raw or 'all').strip().lower()
    if date_scope not in {'', 'all'}:
        date_scope = 'all'

    legacy_sale_type = request.GET.get('sale_type', '').strip().lower()
    status_filter = request.GET.get('status_filter', '').strip().lower()
    sale_type_filter = request.GET.get('sale_type_filter', '').strip().lower()
    amount_scope = request.GET.get('amount_scope', 'total').strip().lower()

    if not status_filter:
        status_filter = 'all'
    if status_filter not in ['all', 'completed', 'unsettled', 'abandoned', 'deleted']:
        status_filter = 'all'

    if not sale_type_filter:
        sale_type_filter = legacy_sale_type if legacy_sale_type in ['retail', 'wholesale'] else 'all'
    if sale_type_filter not in ['all', 'retail', 'wholesale']:
        sale_type_filter = 'all'

    if amount_scope not in ['retail', 'wholesale', 'total']:
        amount_scope = 'retail'

    sales = base_sales
    if status_filter == 'deleted':
        sales = sales.filter(status='DELETED')
    elif status_filter == 'abandoned':
        sales = sales.filter(status='ABANDONED')
    elif status_filter == 'unsettled':
        sales = sales.filter(status='UNSETTLED')
    elif status_filter == 'completed':
        sales = sales.filter(status='COMPLETED')

    if search_query:
        sales = sales.filter(
            Q(id__icontains=search_query) |
            Q(account_holder__icontains=search_query)
        )

    date_from_obj = None
    date_to_obj = None
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
        except ValueError:
            date_from_obj = None
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
        except ValueError:
            date_to_obj = None

    if date_from_obj and date_to_obj and date_from_obj > date_to_obj:
        date_from_obj, date_to_obj = date_to_obj, date_from_obj

    if date_from_obj and date_to_obj:
        sales = sales.filter(
            created_at__range=[
                date_from_obj,
                datetime.combine(date_to_obj.date(), datetime.max.time()),
            ]
        )
    elif date_from_obj:
        sales = sales.filter(created_at__date__gte=date_from_obj.date())
    elif date_to_obj:
        sales = sales.filter(created_at__date__lte=date_to_obj.date())

    if sale_type_filter in ['retail', 'wholesale']:
        sales = sales.filter(items__sale_type=sale_type_filter).distinct()

    active_sales = base_sales.exclude(status='DELETED')
    deposit_locked_sales = active_sales.filter(status__in=['UNSETTLED', 'ABANDONED'])
    if amount_scope == 'total':
        today_sales = active_sales.filter(
            created_at__date=today
        ).aggregate(total=Sum('final_amount'))['total'] or 0
        month_sales = active_sales.filter(
            created_at__year=today.year,
            created_at__month=today.month,
        ).aggregate(total=Sum('final_amount'))['total'] or 0
    else:
        metrics_items = SaleItem.objects.filter(
            sale__in=active_sales.filter(status='COMPLETED')
        )
        metrics_items = metrics_items.filter(sale_type=amount_scope)
        today_sales = metrics_items.filter(
            sale__created_at__date=today
        ).aggregate(total=Sum('subtotal'))['total'] or 0
        month_sales = metrics_items.filter(
            sale__created_at__year=today.year,
            sale__created_at__month=today.month,
        ).aggregate(total=Sum('subtotal'))['total'] or 0

        unsettled_today_deposit = deposit_locked_sales.filter(
            created_at__date=today
        ).aggregate(total=Sum('deposit_amount'))['total'] or 0
        unsettled_month_deposit = deposit_locked_sales.filter(
            created_at__year=today.year,
            created_at__month=today.month,
        ).aggregate(total=Sum('deposit_amount'))['total'] or 0

        today_sales += unsettled_today_deposit
        month_sales += unsettled_month_deposit

    total_sales = sales.count()
    amount_scope_labels = {
        'retail': '零售+定金',
        'wholesale': '批发+定金',
        'total': '总额',
    }

    page_number = request.GET.get('page', 1)
    paginated_sales = paginate_queryset(sales, page_number)
    page_items = build_elided_page_range(paginated_sales, on_each_side=1, on_ends=1)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    pagination_query = query_params.urlencode()
    pagination_param_pairs = []
    for key, values in query_params.lists():
        for value in values:
            pagination_param_pairs.append((key, value))

    context = {
        'sales': paginated_sales,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'date_scope': date_scope,
        'status_filter': status_filter,
        'sale_type_filter': sale_type_filter,
        'amount_scope': amount_scope,
        'amount_scope_label': amount_scope_labels[amount_scope],
        'today_sales': today_sales,
        'month_sales': month_sales,
        'total_sales': total_sales,
        'pagination_query': pagination_query,
        'pagination_param_pairs': pagination_param_pairs,
        'page_items': page_items,
        'total_pages': paginated_sales.paginator.num_pages,
    }
    return render(
        request,
        'inventory/reports/all_sales_orders.html',
        _append_scope_context(context, scope),
    )


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def receivable_report(request):
    """应收款报表：按挂账人统计未结算应收款（不按日期筛选）。"""
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    today = timezone.localdate()
    default_history_start = today.replace(day=1)
    history_start_date = _parse_query_date(request.GET.get('history_start_date')) or default_history_start
    history_end_date = _parse_query_date(request.GET.get('history_end_date')) or today
    if history_start_date > history_end_date:
        history_start_date, history_end_date = history_end_date, history_start_date
    history_query = (request.GET.get('history_q') or '').strip()
    history_page = (request.GET.get('history_page') or '1').strip() or '1'

    report_data = ReportService.get_receivable_report(
        warehouse_ids=warehouse_ids,
        history_start_date=history_start_date,
        history_end_date=history_end_date,
        history_query=history_query,
        history_page=history_page,
    )
    context = {
        'report_data': report_data,
        'history_start_date': history_start_date,
        'history_end_date': history_end_date,
        'history_query': history_query,
    }
    return render(
        request,
        'inventory/reports/receivable.html',
        _append_scope_context(context, scope),
    )


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def payable_report(request):
    """应付款报表：按供货商统计应付款，并支持软删除应付款订单（不按日期筛选）。"""
    scope = _resolve_report_scope(request)
    warehouse_ids = scope['warehouse_ids']
    available_warehouses = scope['warehouses']

    if request.method == 'POST':
        action = (request.POST.get('action') or 'query').strip()
        if action == 'create_payable_order':
            messages.error(request, '手工新增应付款订单入口已停用，请通过入库流程登记应付款。')
        elif action == 'delete_payable_order':
            order_id_param = (request.POST.get('order_id') or '').strip()
            delete_reason = (request.POST.get('delete_reason') or '').strip()
            try:
                order_id = int(order_id_param)
            except (TypeError, ValueError):
                order_id = None

            debt_order = None
            if order_id is not None:
                debt_order = DebtOrder.objects.select_related('warehouse', 'supplier').filter(
                    id=order_id,
                    is_deleted=False,
                ).first()
            if debt_order is None:
                messages.error(request, '删除失败：未找到可操作的应付款订单')
            else:
                if debt_order.warehouse_id and not available_warehouses.filter(id=debt_order.warehouse_id).exists():
                    messages.error(request, '删除失败：您无权限操作该仓库的应付款订单')
                else:
                    try:
                        PayableService.soft_delete_payable_order(
                            order=debt_order,
                            operator=request.user,
                            reason=delete_reason,
                        )
                    except Exception as exc:
                        messages.error(request, f'删除失败：{exc}')
                    else:
                        messages.success(request, f'应付款订单 #{debt_order.id} 已删除（软删除）')
                        return redirect('payable_report')

    report_data = ReportService.get_payable_report(
        warehouse_ids=warehouse_ids,
    )
    context = {
        'report_data': report_data,
    }
    return render(
        request,
        'inventory/reports/payable.html',
        _append_scope_context(context, scope),
    )


@login_required
@log_view_access('OTHER')
@permission_required('view_reports')
def data_tools_report(request):
    """数据导入导出工具页。"""
    scope = _resolve_report_scope(request)

    can_manage_products = WarehouseScopeService.get_accessible_warehouses(
        request.user,
        required_permission=UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
    ).exists()
    can_stock_in = WarehouseScopeService.get_accessible_warehouses(
        request.user,
        required_permission=UserWarehouseAccess.PERMISSION_STOCK_IN,
    ).exists()
    can_view_inventory = WarehouseScopeService.get_accessible_warehouses(
        request.user,
        required_permission=UserWarehouseAccess.PERMISSION_VIEW,
    ).exists()

    context = {
        'can_manage_products': can_manage_products,
        'can_stock_in': can_stock_in,
        'can_view_inventory': can_view_inventory,
    }
    return render(
        request,
        'inventory/reports/data_tools.html',
        _append_scope_context(context, scope),
    )
