from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.contrib.contenttypes.models import ContentType

# 显式导入原始models
import inventory.models
from .models.common import OperationLog 
from . import forms
from .ali_barcode_service import AliBarcodeService
from .services.warehouse_scope_service import WarehouseScopeService


def _ensure_product_manage_access(user):
    WarehouseScopeService.ensure_any_warehouse_permission(
        user=user,
        required_permission=inventory.models.UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
        error_message='您无权访问条码建档模块',
    )


def _get_preferred_product_warehouse(user):
    manageable_warehouses = WarehouseScopeService.get_accessible_warehouses(
        user,
        required_permission=inventory.models.UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE,
    )
    preferred_default = WarehouseScopeService.get_default_warehouse(user)
    if preferred_default and manageable_warehouses.filter(id=preferred_default.id).exists():
        return preferred_default
    return manageable_warehouses.first()

@login_required
def barcode_product_create(request):
    """
    通过条码查询商品信息并创建商品的视图
    支持GET方式查询条码，POST方式保存商品
    先查询数据库，如果不存在再调用API
    """
    _ensure_product_manage_access(request.user)
    barcode = request.GET.get('barcode', '')
    barcode_data = None
    initial_data = {}
    
    # 如果提供了条码，尝试查询商品信息
    if barcode:
        # 首先检查数据库中是否已存在该条码的商品
        try:
            existing_product = inventory.models.Product.objects.get(barcode=barcode)
            messages.warning(request, f'条码 {barcode} 的商品已存在，请勿重复添加')
            return redirect('product_list')
        except inventory.models.Product.DoesNotExist:
            # 调用阿里云条码服务查询商品信息
            barcode_data = AliBarcodeService.search_barcode(barcode)
            
            if barcode_data:
                # 预填表单数据
                initial_data = {
                    'barcode': barcode,
                    'name': barcode_data.get('name', ''),
                    'specification': barcode_data.get('specification', ''),
                    'manufacturer': barcode_data.get('manufacturer', ''),
                    'price': barcode_data.get('suggested_price', 0),
                    'cost': barcode_data.get('suggested_price', 0) * 0.8 if barcode_data.get('suggested_price') else 0,  # 默认成本价为建议售价的80%
                    'description': barcode_data.get('description', ''),
                    'is_active': True  # 确保初始化时is_active为True
                }
                
                # 尝试从数据库中查找匹配的商品类别
                category_name = barcode_data.get('category', '')
                if category_name:
                    try:
                        category = inventory.models.Category.objects.filter(name__icontains=category_name).first()
                        if category:
                            initial_data['category'] = category.id
                    except Exception as e:
                        print(f"查找商品类别出错: {e}")
                        # 错误处理，但不影响表单的其他字段
                messages.success(request, '成功获取商品信息，请确认并完善商品详情')
            else:
                messages.info(request, f'未找到条码 {barcode} 的商品信息，请手动填写')
                initial_data = {'barcode': barcode, 'is_active': True}  # 确保初始化时is_active为True
    
    # 处理表单提交
    if request.method == 'POST':
        form = forms.ProductForm(request.POST, request.FILES)
        if form.is_valid():
            # 确保is_active为True
            product = form.save(commit=False)
            product.is_active = True
            product.save()
            
            # 创建初始库存记录
            initial_stock = request.POST.get('initial_stock', 0)
            try:
                initial_stock = int(initial_stock)
                if initial_stock < 0:
                    initial_stock = 0
            except ValueError:
                initial_stock = 0
                
            warning_level = form.cleaned_data.get('warning_level')
            if warning_level is None:
                warning_level = 10

            target_warehouse = _get_preferred_product_warehouse(request.user)
            if target_warehouse is not None:
                warehouse_inventory, _ = inventory.models.WarehouseInventory.objects.get_or_create(
                    product=product,
                    warehouse=target_warehouse,
                    defaults={'warning_level': warning_level}
                )
                if warehouse_inventory.warning_level != warning_level:
                    warehouse_inventory.warning_level = warning_level
                    warehouse_inventory.save(update_fields=['warning_level'])

            stock_update_error = None
            if initial_stock > 0:
                if target_warehouse is None:
                    stock_update_error = '未找到可用仓库，无法自动写入初始库存。请先配置仓库并执行入库。'
                else:
                    success, _, stock_result = inventory.models.update_inventory(
                        product=product,
                        warehouse=target_warehouse,
                        quantity=initial_stock,
                        transaction_type='IN',
                        operator=request.user,
                        notes=f'条码建档时设置初始库存 | source=barcode_product_create | warehouse_id={target_warehouse.id}'
                    )
                    if not success:
                        stock_update_error = stock_result
            if stock_update_error:
                messages.warning(request, f'商品已创建，但初始库存写入失败: {stock_update_error}')
            
            # 记录操作日志
            OperationLog.objects.create(
                operator=request.user,
                operation_type='INVENTORY',
                details=f'添加新商品: {product.name} (条码: {product.barcode}), 初始库存: {initial_stock}',
                related_object_id=product.id,
                related_content_type=ContentType.objects.get_for_model(product)
            )
            
            if initial_stock > 0 and target_warehouse is not None and not stock_update_error:
                messages.success(request, f'商品添加成功，初始库存已在仓库 {target_warehouse.name} 设置为 {initial_stock}')
            else:
                messages.success(request, '商品添加成功')
            return redirect('product_list')
    else:
        form = forms.ProductForm(initial=initial_data)
    
    # 确保barcode_data不为None时为字典类型
    if barcode_data is None:
        barcode_data = {}
        
    # 渲染模板
    return render(request, 'inventory/barcode_product_form.html', {
        'form': form,
        'barcode': barcode,
        'barcode_data': barcode_data
    })

@login_required
def barcode_lookup(request):
    """
    AJAX接口，用于查询条码信息
    先查询数据库，如果不存在再调用API
    """
    _ensure_product_manage_access(request.user)
    barcode = request.GET.get('barcode', '')
    if not barcode:
        return JsonResponse({'success': False, 'message': '请提供条码'})
        
    # 首先检查数据库中是否已存在该条码的商品
    try:
        product = inventory.models.Product.objects.get(barcode=barcode)
        return JsonResponse({
            'success': True,
            'exists': True,
            'product_id': product.id,
            'name': product.name,
            'price': float(product.price),
            'specification': product.specification,
            'manufacturer': product.manufacturer,
            'description': product.description,
            'message': '商品已存在于系统中'
        })
    except inventory.models.Product.DoesNotExist:
        # 调用阿里云条码服务查询商品信息
        barcode_data = AliBarcodeService.search_barcode(barcode)
        
        if barcode_data:
            return JsonResponse({
                'success': True,
                'exists': False,
                'data': barcode_data,
                'message': '成功获取商品信息'
            })
        else:
            return JsonResponse({
                'success': False,
                'exists': False,
                'message': '未找到商品信息'
            })
