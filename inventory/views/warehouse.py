"""
仓库管理视图
提供仓库的CRUD操作
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Sum, Q
from django.utils.text import capfirst

from inventory.models import Warehouse, OperationLog, UserWarehouseAccess
from inventory.forms import WarehouseForm
from inventory.permissions.decorators import superuser_required
from inventory.services.warehouse_scope_service import WarehouseScopeService


WAREHOUSE_MANAGER_PERMISSION_BITS = UserWarehouseAccess.bits_for_codes([
    'view',
    'sale',
    'stock_in',
    'stock_out',
    'inventory_check',
    'stock_adjust',
    'product_manage',
    'report_view',
])


def _ensure_warehouse_manage_module_access(user):
    WarehouseScopeService.ensure_any_warehouse_permission(
        user=user,
        required_permission=UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
        error_message='您无权访问仓库管理模块',
    )


@login_required
def warehouse_list(request):
    """仓库列表视图"""
    _ensure_warehouse_manage_module_access(request.user)
    warehouses = Warehouse.objects.annotate(
        product_count=Count('inventories', filter=Q(inventories__quantity__gt=0)),
        annotated_total=Sum('inventories__quantity')  # 避免与模型 @property total_quantity 冲突
    )
    if not WarehouseScopeService.is_admin_user(request.user):
        warehouse_ids = WarehouseScopeService.get_accessible_warehouse_ids(
            request.user,
            required_permission=UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
        )
        warehouses = warehouses.filter(id__in=warehouse_ids)
    warehouses = warehouses.order_by('name')
    context = {'warehouses': warehouses}
    return render(request, 'inventory/warehouse_list.html', context)


@login_required
def warehouse_create(request):
    """创建仓库视图"""
    _ensure_warehouse_manage_module_access(request.user)
    if request.method == 'POST':
        form = WarehouseForm(request.POST)
        if form.is_valid():
            warehouse = form.save()

            # 分仓管理员新增仓库后，自动授予该仓库管理模板权限
            if not request.user.is_superuser:
                has_active_default = UserWarehouseAccess.objects.filter(
                    user=request.user,
                    is_active=True,
                    is_default=True,
                    warehouse__is_active=True,
                ).exists()
                access, _ = UserWarehouseAccess.objects.get_or_create(
                    user=request.user,
                    warehouse=warehouse,
                    defaults={
                        'is_default': not has_active_default,
                        'is_active': True,
                        'permission_bits': WAREHOUSE_MANAGER_PERMISSION_BITS,
                    },
                )
                dirty_fields = []
                merged_bits = UserWarehouseAccess.ensure_permission_bits(
                    (access.permission_bits or 0) | WAREHOUSE_MANAGER_PERMISSION_BITS
                )
                if access.permission_bits != merged_bits:
                    access.permission_bits = merged_bits
                    dirty_fields.append('permission_bits')
                if not access.is_active:
                    access.is_active = True
                    dirty_fields.append('is_active')
                if not has_active_default and not access.is_default:
                    access.is_default = True
                    dirty_fields.append('is_default')
                if dirty_fields:
                    access.save(update_fields=dirty_fields)
            
            # 记录操作日志
            OperationLog.objects.create(
                operator=request.user,
                operation_type='INVENTORY',
                details=f'添加仓库: {warehouse.name}',
                related_object_id=warehouse.id,
                related_content_type=ContentType.objects.get_for_model(warehouse)
            )
            
            messages.success(request, f'仓库 "{warehouse.name}" 添加成功')
            return redirect('warehouse_list')
    else:
        form = WarehouseForm()
    
    return render(request, 'inventory/warehouse_form.html', {'form': form, 'title': '添加仓库'})


@login_required
def warehouse_edit(request, warehouse_id):
    """编辑仓库视图"""
    warehouse = get_object_or_404(Warehouse, id=warehouse_id)
    if not request.user.is_superuser:
        WarehouseScopeService.ensure_warehouse_permission(
            user=request.user,
            warehouse=warehouse,
            required_permission=UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
            error_message='您无权编辑该仓库',
            code='warehouse_scope_denied',
        )
    
    if request.method == 'POST':
        form = WarehouseForm(request.POST, instance=warehouse)
        if form.is_valid():
            warehouse = form.save()
            
            # 记录操作日志
            OperationLog.objects.create(
                operator=request.user,
                operation_type='INVENTORY',
                details=f'编辑仓库: {warehouse.name}',
                related_object_id=warehouse.id,
                related_content_type=ContentType.objects.get_for_model(warehouse)
            )
            
            messages.success(request, f'仓库 "{warehouse.name}" 更新成功')
            return redirect('warehouse_list')
    else:
        form = WarehouseForm(instance=warehouse)
    
    return render(request, 'inventory/warehouse_form.html', {
        'form': form, 
        'title': '编辑仓库',
        'warehouse': warehouse
    })


@login_required
@superuser_required
def warehouse_delete(request, warehouse_id):
    """删除仓库视图"""
    warehouse = get_object_or_404(Warehouse, id=warehouse_id)
    
    # 检查是否有库存记录
    has_inventory = warehouse.inventories.exists()
    has_transactions = warehouse.transactions.exists()
    
    if has_inventory or has_transactions:
        messages.error(request, f'无法删除仓库 "{warehouse.name}"，因为该仓库有关联库存记录或交易历史')
        return redirect('warehouse_list')
    
    if request.method == 'POST':
        warehouse_name = warehouse.name
        warehouse.delete()
        
        # 记录操作日志
        OperationLog.objects.create(
            operator=request.user,
            operation_type='INVENTORY',
            details=f'删除仓库: {warehouse_name}',
            related_object_id=None,
            related_content_type=None
        )
        
        messages.success(request, f'仓库 "{warehouse_name}" 删除成功')
        return redirect('warehouse_list')
    
    return render(request, 'inventory/warehouse_confirm_delete.html', {
        'warehouse': warehouse
    })
