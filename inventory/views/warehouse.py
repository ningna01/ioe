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

from inventory.models import Warehouse, OperationLog
from inventory.forms import WarehouseForm


@login_required
def warehouse_list(request):
    """仓库列表视图"""
    warehouses = Warehouse.objects.annotate(
        product_count=Count('inventories', filter=Q(inventories__quantity__gt=0)),
        total_quantity=Sum('inventories__quantity')
    ).order_by('name')
    
    context = {
        'warehouses': warehouses,
    }
    return render(request, 'inventory/warehouse_list.html', context)


@login_required
def warehouse_create(request):
    """创建仓库视图"""
    if request.method == 'POST':
        form = WarehouseForm(request.POST)
        if form.is_valid():
            warehouse = form.save()
            
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
