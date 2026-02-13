"""
核心视图模块
包含首页和仪表盘等核心功能
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import F, Min, Sum
from django.utils import timezone
from datetime import timedelta

from inventory.models import (
    Product, Sale, SaleItem, UserWarehouseAccess, WarehouseInventory
)
from inventory.permissions.decorators import permission_required
from inventory.services.warehouse_scope_service import WarehouseScopeService
from inventory.services.user_mode_service import is_sales_focus_user


def _build_dashboard_scope(user, request, required_permission=None):
    warehouse_param = request.GET.get('warehouse', 'all')
    return WarehouseScopeService.resolve_warehouse_selection(
        user=user,
        warehouse_param=warehouse_param,
        include_all_option=True,
        required_permission=required_permission,
    )


def _ensure_report_module_access(user):
    WarehouseScopeService.ensure_any_warehouse_permission(
        user=user,
        required_permission=UserWarehouseAccess.PERMISSION_REPORT_VIEW,
        error_message='您无权访问报表中心',
        code='warehouse_scope_denied',
    )


@login_required
def index(request):
    """系统首页/仪表盘视图"""
    if is_sales_focus_user(request.user):
        return redirect('sale_create')

    # 获取系统概览统计
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)
    scope = _build_dashboard_scope(request.user, request)
    warehouse_ids = scope['warehouse_ids']

    inventory_scope_query = WarehouseInventory.objects.select_related('product', 'warehouse').filter(
        warehouse__is_active=True
    )
    if warehouse_ids is not None:
        if warehouse_ids:
            inventory_scope_query = inventory_scope_query.filter(warehouse_id__in=warehouse_ids)
        else:
            inventory_scope_query = inventory_scope_query.none()

    product_stock_summary = inventory_scope_query.values('product_id').annotate(
        total_quantity=Sum('quantity'),
        warning_level=Min('warning_level'),
    )

    # 商品统计（仓库口径）
    total_products = product_stock_summary.count()
    active_products = Product.objects.filter(
        is_active=True,
        warehouse_inventories__in=inventory_scope_query,
    ).distinct().count()
    low_stock_products = product_stock_summary.filter(
        total_quantity__lte=F('warning_level')
    ).count()
    out_of_stock_products = product_stock_summary.filter(total_quantity=0).count()
    
    # 销售统计
    sales_scope = Sale.objects.exclude(status='DELETED')
    if warehouse_ids is not None:
        if warehouse_ids:
            sales_scope = sales_scope.filter(warehouse_id__in=warehouse_ids)
        else:
            sales_scope = sales_scope.none()

    total_sales = sales_scope.count()
    today_sales = sales_scope.filter(created_at__date=today).count()
    today_sales_amount = sales_scope.filter(created_at__date=today).aggregate(
        total=Sum('final_amount')
    )['total'] or 0
    
    yesterday_sales = sales_scope.filter(created_at__date=yesterday).count()
    yesterday_sales_amount = sales_scope.filter(created_at__date=yesterday).aggregate(
        total=Sum('final_amount')
    )['total'] or 0
    
    # 会员统计（已禁用）
    # total_members = Member.objects.count()
    # active_members = total_members
    # new_members_month = Member.objects.filter(created_at__gte=month_ago).count()
    
    # 近期销售走势
    sales_trend = []
    for i in range(7):
        date = today - timedelta(days=i)
        daily_sales = sales_scope.filter(created_at__date=date).aggregate(
            total=Sum('final_amount')
        )['total'] or 0
        sales_trend.append({
            'date': date.strftime('%m-%d'),
            'amount': float(daily_sales)
        })
    sales_trend.reverse()
    
    # 热销商品
    top_products_query = SaleItem.objects.filter(
        sale__created_at__gte=week_ago,
        sale__status='COMPLETED',
    )
    if warehouse_ids is not None:
        if warehouse_ids:
            top_products_query = top_products_query.filter(sale__warehouse_id__in=warehouse_ids)
        else:
            top_products_query = top_products_query.none()

    top_products = top_products_query.values(
        'product__name'
    ).annotate(
        total_qty=Sum('quantity'),
        total_amount=Sum('subtotal')
    ).order_by('-total_qty')[:5]
    
    # 超期未结算订单预警
    alert_deadline = timezone.now() - timedelta(days=60)
    overdue_orders = list(
        sales_scope.filter(
            status='UNSETTLED',
            created_at__lt=alert_deadline,
        ).select_related('warehouse', 'operator').order_by('created_at')[:10]
    )
    order_alerts = []
    for sale in overdue_orders:
        order_alerts.append({
            'sale': sale,
            'overdue_days': (today - sale.created_at.date()).days,
            'remaining_amount': sale.remaining_amount,
        })
    
    # 获取当月生日会员（已禁用）
    # current_month = today.month
    # birthday_members = Member.objects.filter(
    #     birthday__isnull=False,  # 确保生日字段不为空
    #     birthday__month=current_month,
    #     is_active=True
    # ).order_by('birthday__day')[:10]
    
    context = {
        'total_products': total_products,
        'active_products': active_products,
        'low_stock_products': low_stock_products,
        'out_of_stock_products': out_of_stock_products,
        'total_sales': total_sales,
        'today_sales': today_sales,
        'today_sales_amount': today_sales_amount,
        'yesterday_sales': yesterday_sales,
        'yesterday_sales_amount': yesterday_sales_amount,
        # 会员统计（已禁用）
        # 'total_members': total_members,
        # 'active_members': active_members,
        # 'new_members_month': new_members_month,
        'sales_trend': sales_trend,
        'top_products': top_products,
        'order_alerts': order_alerts,
        'order_alert_count': len(order_alerts),
        'warehouses': scope['warehouses'],
        'selected_warehouse': scope['selected_warehouse_value'],
        'warehouse_scope_label': scope['scope_label'],
        'selected_warehouse_obj': scope['selected_warehouse'],
        # 'birthday_members': birthday_members,
        # 'current_month': current_month,
    }
    
    return render(request, 'inventory/index.html', context)


@login_required
@permission_required('view_reports')
def reports_index(request):
    """报表首页视图"""
    _ensure_report_module_access(request.user)
    scope = _build_dashboard_scope(
        request.user,
        request,
        required_permission=UserWarehouseAccess.PERMISSION_REPORT_VIEW,
    )
    return render(request, 'inventory/reports/index.html', {
        'warehouses': scope['warehouses'],
        'selected_warehouse': scope['selected_warehouse_value'],
        'warehouse_scope_label': scope['scope_label'],
    })
