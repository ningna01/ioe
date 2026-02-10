"""
库存管理视图
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.contrib import messages
from django.db.models import Q, Sum, F
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator

from inventory.models import (
    Product, InventoryTransaction,
    Warehouse, WarehouseInventory,
    OperationLog, StockAlert, check_inventory,
    update_inventory, Category
)
from inventory.forms import InventoryTransactionForm
from inventory.services.warehouse_scope_service import WarehouseScopeService


def _build_inventory_notes(source, intent, user_notes='', extra_context=None):
    """统一库存交易备注格式，便于审计回溯。"""
    note_parts = [
        f"source={source}",
        f"intent={intent}",
    ]
    cleaned_notes = (user_notes or '').strip()
    if cleaned_notes:
        note_parts.append(f"user_note={cleaned_notes}")
    if extra_context:
        for key, value in extra_context.items():
            note_parts.append(f"{key}={value}")
    return " | ".join(note_parts)


def _build_inventory_success_message(action, product, warehouse, delta_quantity, current_quantity):
    return (
        f"{action}成功: {product.name} ({warehouse.name})，"
        f"变更: {delta_quantity:+d}，当前库存: {current_quantity}"
    )


def _build_inventory_failure_message(action, product, warehouse, reason):
    return f"{action}失败: {product.name} ({warehouse.name})，原因: {reason}"


def _get_warehouse_stock(product, warehouse):
    inventory = WarehouseInventory.objects.filter(product=product, warehouse=warehouse).first()
    if inventory is None:
        return 0
    return inventory.quantity


def _create_inventory_operation_log(
    *,
    operator,
    action,
    product,
    warehouse,
    requested_quantity,
    delta_quantity,
    current_quantity,
    transaction,
    source,
):
    """统一库存操作日志格式。"""
    OperationLog.objects.create(
        operator=operator,
        operation_type='INVENTORY',
        details=(
            f"{action}: 商品={product.name}; 仓库={warehouse.name}; "
            f"请求数量={requested_quantity}; 变更={delta_quantity:+d}; 当前库存={current_quantity}; "
            f"交易ID={transaction.id}; 来源={source}"
        ),
        related_object_id=transaction.id,
        related_content_type=ContentType.objects.get_for_model(InventoryTransaction),
    )


@login_required
def inventory_list(request):
    """库存列表视图（支持按仓库筛选，使用 WarehouseInventory）"""
    category_id = request.GET.get('category', '')
    color = request.GET.get('color', '')
    size = request.GET.get('size', '')
    search_query = request.GET.get('search', '')
    warehouse_param = request.GET.get('warehouse', '')

    # 仓库筛选：按用户授权解析
    available_warehouses = WarehouseScopeService.get_accessible_warehouses(request.user)
    default_warehouse = WarehouseScopeService.get_default_warehouse(request.user)
    show_all_warehouses = warehouse_param == 'all'
    selected_warehouse = None
    selected_warehouse_value = warehouse_param

    if warehouse_param and not show_all_warehouses:
        try:
            selected_warehouse = available_warehouses.get(id=int(warehouse_param))
        except (ValueError, TypeError):
            selected_warehouse = default_warehouse
            selected_warehouse_value = str(default_warehouse.id) if default_warehouse else ''
        except Warehouse.DoesNotExist:
            selected_warehouse = default_warehouse
            selected_warehouse_value = str(default_warehouse.id) if default_warehouse else ''
    elif show_all_warehouses and not WarehouseScopeService.is_admin_user(request.user):
        # 普通用户仅可查看其授权仓集合
        if not available_warehouses.exists():
            show_all_warehouses = False
            selected_warehouse = None
            selected_warehouse_value = ''
    else:
        selected_warehouse = default_warehouse
        if selected_warehouse is not None:
            selected_warehouse_value = str(selected_warehouse.id)

    # 基础查询：使用 WarehouseInventory（与原 Inventory 行为一致，含 0 库存）
    base_qs = WarehouseInventory.objects.select_related(
        'product', 'product__category', 'warehouse'
    ).all()

    if show_all_warehouses:
        inventory_items = WarehouseScopeService.filter_warehouse_inventory_queryset(request.user, base_qs)
    elif selected_warehouse:
        if WarehouseScopeService.can_access_warehouse(request.user, selected_warehouse):
            inventory_items = base_qs.filter(warehouse=selected_warehouse)
        else:
            inventory_items = base_qs.none()
    else:
        inventory_items = base_qs.none()

    if category_id:
        inventory_items = inventory_items.filter(product__category_id=category_id)
    if color:
        inventory_items = inventory_items.filter(product__color=color)
    if size:
        inventory_items = inventory_items.filter(product__size=size)
    if search_query:
        inventory_items = inventory_items.filter(
            Q(product__name__icontains=search_query) |
            Q(product__barcode__icontains=search_query)
        )

    categories = Category.objects.all()
    colors = Product.COLOR_CHOICES
    sizes = Product.SIZE_CHOICES
    warehouses = available_warehouses

    context = {
        'inventory_items': inventory_items,
        'categories': categories,
        'colors': colors,
        'sizes': sizes,
        'warehouses': warehouses,
        'selected_category': category_id,
        'selected_color': color,
        'selected_size': size,
        'selected_warehouse': selected_warehouse_value,
        'show_all_warehouses': show_all_warehouses,
        'search_query': search_query,
    }
    return render(request, 'inventory/inventory_list.html', context)


@login_required
def inventory_transaction_list(request):
    """库存交易记录列表，显示所有入库、出库和调整记录"""
    # 获取筛选参数
    transaction_type = request.GET.get('type', '')
    product_id = request.GET.get('product_id', '')
    search_query = request.GET.get('search', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # 基础查询
    transactions = InventoryTransaction.objects.select_related('product', 'operator', 'warehouse').all()
    transactions = WarehouseScopeService.filter_inventory_transactions_queryset(request.user, transactions)
    
    # 应用筛选条件
    if transaction_type:
        transactions = transactions.filter(transaction_type=transaction_type)
    
    if product_id:
        transactions = transactions.filter(product_id=product_id)
    
    if search_query:
        transactions = transactions.filter(
            Q(product__name__icontains=search_query) | 
            Q(product__barcode__icontains=search_query) |
            Q(notes__icontains=search_query)
        )
    
    if date_from:
        from datetime import datetime
        try:
            date_from = datetime.strptime(date_from, '%Y-%m-%d')
            transactions = transactions.filter(created_at__gte=date_from)
        except (ValueError, TypeError):
            pass
    
    if date_to:
        from datetime import datetime, timedelta
        try:
            date_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)  # 加一天以包含整天
            transactions = transactions.filter(created_at__lt=date_to)
        except (ValueError, TypeError):
            pass
    
    # 排序
    transactions = transactions.order_by('-created_at')
    
    # 分页
    paginator = Paginator(transactions, 20)  # 每页20条记录
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'inventory/inventory_transaction_list.html', {
        'page_obj': page_obj,
        'transaction_type': transaction_type,
        'product_id': product_id,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'transaction_types': dict(InventoryTransaction.TRANSACTION_TYPES)
    })


@login_required
def inventory_in(request):
    """入库视图（支持多仓库）"""
    if request.method == 'POST':
        form = InventoryTransactionForm(request.POST, user=request.user)
        if form.is_valid():
            product = form.cleaned_data['product']
            warehouse = form.cleaned_data['warehouse']
            quantity = form.cleaned_data['quantity']
            notes = _build_inventory_notes(
                source='inventory_in',
                intent='manual_in',
                user_notes=form.cleaned_data['notes'],
            )
            success, inventory, result = update_inventory(
                product=product,
                warehouse=warehouse,
                quantity=quantity,
                transaction_type='IN',
                operator=request.user,
                notes=notes
            )
            if success:
                transaction = result
                _create_inventory_operation_log(
                    operator=request.user,
                    action='入库',
                    product=product,
                    warehouse=warehouse,
                    requested_quantity=quantity,
                    delta_quantity=quantity,
                    current_quantity=inventory.quantity,
                    transaction=transaction,
                    source='inventory_in',
                )
                messages.success(
                    request,
                    _build_inventory_success_message(
                        action='入库',
                        product=product,
                        warehouse=warehouse,
                        delta_quantity=quantity,
                        current_quantity=inventory.quantity,
                    ),
                )
                return redirect('inventory_list')
            messages.error(
                request,
                _build_inventory_failure_message(
                    action='入库',
                    product=product,
                    warehouse=warehouse,
                    reason=result,
                ),
            )
    else:
        form = InventoryTransactionForm(user=request.user)
    return render(request, 'inventory/inventory_transaction_form.html', {
        'form': form,
        'form_title': '商品入库',
        'submit_text': '确认入库',
        'transaction_type': 'IN'
    })


@login_required
def inventory_out(request):
    """出库视图（支持多仓库）"""
    if request.method == 'POST':
        form = InventoryTransactionForm(request.POST, user=request.user)
        if form.is_valid():
            product = form.cleaned_data['product']
            warehouse = form.cleaned_data['warehouse']
            quantity = form.cleaned_data['quantity']
            user_notes = form.cleaned_data['notes']
            
            # 先检查库存是否足够（支持多仓库）
            if not check_inventory(product, quantity, warehouse):
                current_quantity = _get_warehouse_stock(product, warehouse)
                messages.error(
                    request,
                    _build_inventory_failure_message(
                        action='出库',
                        product=product,
                        warehouse=warehouse,
                        reason=f'库存不足，当前库存: {current_quantity}，请求出库: {quantity}',
                    ),
                )
                return render(request, 'inventory/inventory_transaction_form.html', {
                    'form': form,
                    'form_title': '商品出库',
                    'submit_text': '确认出库',
                    'transaction_type': 'OUT'
                })

            notes = _build_inventory_notes(
                source='inventory_out',
                intent='manual_out',
                user_notes=user_notes,
            )
            
            # 使用工具函数更新库存
            success, inventory, result = update_inventory(
                product=product,
                warehouse=warehouse,
                quantity=-quantity,  # 负数表示出库
                transaction_type='OUT',
                operator=request.user,
                notes=notes
            )
            
            if success:
                transaction = result
                _create_inventory_operation_log(
                    operator=request.user,
                    action='出库',
                    product=product,
                    warehouse=warehouse,
                    requested_quantity=quantity,
                    delta_quantity=-quantity,
                    current_quantity=inventory.quantity,
                    transaction=transaction,
                    source='inventory_out',
                )
                messages.success(
                    request,
                    _build_inventory_success_message(
                        action='出库',
                        product=product,
                        warehouse=warehouse,
                        delta_quantity=-quantity,
                        current_quantity=inventory.quantity,
                    ),
                )
                return redirect('inventory_list')
            messages.error(
                request,
                _build_inventory_failure_message(
                    action='出库',
                    product=product,
                    warehouse=warehouse,
                    reason=result,
                ),
            )
    else:
        form = InventoryTransactionForm(user=request.user)
    
    return render(request, 'inventory/inventory_transaction_form.html', {
        'form': form,
        'form_title': '商品出库',
        'submit_text': '确认出库',
        'transaction_type': 'OUT'
    })


@login_required
def inventory_adjust(request):
    """库存调整视图"""
    if request.method == 'POST':
        form = InventoryTransactionForm(request.POST, user=request.user)
        if form.is_valid():
            product = form.cleaned_data['product']
            warehouse = form.cleaned_data['warehouse']
            quantity = form.cleaned_data['quantity']
            notes = form.cleaned_data['notes']
            
            # 获取当前库存
            try:
                inventory = WarehouseInventory.objects.get(product=product, warehouse=warehouse)
                current_quantity = inventory.quantity
            except WarehouseInventory.DoesNotExist:
                current_quantity = 0
            
            # 计算调整值
            adjustment_action = request.POST.get('adjustment_action')
            if adjustment_action == 'set':
                # 设置为指定数量
                if quantity < 0:
                    messages.error(request, '库存数量不能为负数')
                    return render(request, 'inventory/inventory_adjust_form.html', {
                        'form': form,
                        'current_quantity': current_quantity
                    })
                
                adjustment_value = quantity - current_quantity
            elif adjustment_action == 'add':
                # 增加指定数量
                adjustment_value = quantity
            elif adjustment_action == 'subtract':
                # 减少指定数量
                if quantity > current_quantity:
                    messages.error(request, f'减少的数量({quantity})超过了当前库存({current_quantity})')
                    return render(request, 'inventory/inventory_adjust_form.html', {
                        'form': form,
                        'current_quantity': current_quantity
                    })
                
                adjustment_value = -quantity
            else:
                messages.error(request, '请选择有效的调整方式')
                return render(request, 'inventory/inventory_adjust_form.html', {
                    'form': form,
                    'current_quantity': current_quantity
                })
            
            # 使用工具函数更新库存
            notes = _build_inventory_notes(
                source='inventory_adjust',
                intent=f'manual_adjust_{adjustment_action}',
                user_notes=notes,
                extra_context={
                    'before': current_quantity,
                    'delta': f'{adjustment_value:+d}',
                },
            )
            success, inventory, result = update_inventory(
                product=product,
                warehouse=warehouse,
                quantity=adjustment_value,
                transaction_type='ADJUST',
                operator=request.user,
                notes=notes,
            )
            
            if success:
                transaction = result
                _create_inventory_operation_log(
                    operator=request.user,
                    action='调整',
                    product=product,
                    warehouse=warehouse,
                    requested_quantity=quantity,
                    delta_quantity=adjustment_value,
                    current_quantity=inventory.quantity,
                    transaction=transaction,
                    source='inventory_adjust',
                )
                messages.success(
                    request,
                    _build_inventory_success_message(
                        action='调整',
                        product=product,
                        warehouse=warehouse,
                        delta_quantity=adjustment_value,
                        current_quantity=inventory.quantity,
                    ),
                )
                return redirect('inventory_list')
            messages.error(
                request,
                _build_inventory_failure_message(
                    action='调整',
                    product=product,
                    warehouse=warehouse,
                    reason=result,
                ),
            )
    else:
        form = InventoryTransactionForm(user=request.user)
        product_id = request.GET.get('product_id')
        if product_id:
            try:
                product = Product.objects.get(id=product_id)
                form.fields['product'].initial = product
            except Product.DoesNotExist:
                pass
    
    # 获取当前库存（如果已选择商品）
    current_quantity = 0
    selected_product = form.initial.get('product')
    selected_warehouse = form.initial.get('warehouse')
    if selected_product and selected_warehouse:
        if not isinstance(selected_warehouse, Warehouse):
            selected_warehouse = Warehouse.objects.filter(id=selected_warehouse).first()
        try:
            inventory = WarehouseInventory.objects.get(product=selected_product, warehouse=selected_warehouse)
            current_quantity = inventory.quantity
        except WarehouseInventory.DoesNotExist:
            pass
    
    return render(request, 'inventory/inventory_adjust_form.html', {
        'form': form,
        'current_quantity': current_quantity
    })


@login_required
def inventory_transaction_create(request):
    """创建入库交易视图"""
    if request.method == 'POST':
        form = InventoryTransactionForm(request.POST, user=request.user)
        if form.is_valid():
            product = form.cleaned_data['product']
            warehouse = form.cleaned_data['warehouse']
            quantity = form.cleaned_data['quantity']
            notes = _build_inventory_notes(
                source='inventory_transaction_create',
                intent='manual_in_legacy_entry',
                user_notes=form.cleaned_data['notes'],
            )

            success, inventory, result = update_inventory(
                product=product,
                warehouse=warehouse,
                quantity=quantity,
                transaction_type='IN',
                operator=request.user,
                notes=notes
            )

            if success:
                transaction = result
                _create_inventory_operation_log(
                    operator=request.user,
                    action='入库',
                    product=product,
                    warehouse=warehouse,
                    requested_quantity=quantity,
                    delta_quantity=quantity,
                    current_quantity=inventory.quantity,
                    transaction=transaction,
                    source='inventory_transaction_create',
                )
                messages.success(
                    request,
                    _build_inventory_success_message(
                        action='入库',
                        product=product,
                        warehouse=warehouse,
                        delta_quantity=quantity,
                        current_quantity=inventory.quantity,
                    ),
                )
                return redirect('inventory_list')

            messages.error(
                request,
                _build_inventory_failure_message(
                    action='入库',
                    product=product,
                    warehouse=warehouse,
                    reason=result,
                ),
            )
    else:
        form = InventoryTransactionForm(user=request.user)
    
    return render(request, 'inventory/inventory_form.html', {'form': form}) 
