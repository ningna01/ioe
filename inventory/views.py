from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import F, Sum
from .models import Product, Category, Inventory, Sale, SaleItem, check_inventory, update_inventory
from django.http import JsonResponse
from .models import OperationLog
from django.db.models import Q
from decimal import Decimal
from django.utils import timezone
import re
from inventory.services.stock_scope_service import StockScopeService


def product_by_barcode(request, barcode):
    warehouse_ids = StockScopeService.resolve_request_warehouse_ids(request)
    try:
        # 先尝试精确匹配条码
        product = Product.objects.get(barcode=barcode)
        stock = StockScopeService.get_product_stock(product, warehouse_ids=warehouse_ids)
            
        return JsonResponse({
            'success': True,
            'product_id': product.id,
            'name': product.name,
            'price': product.price,
            'stock': stock,
            'category': product.category.name if product.category else '',
            'specification': product.specification,
            'manufacturer': product.manufacturer
        })
    except Product.DoesNotExist:
        # 如果精确匹配失败，尝试模糊匹配条码
        products = Product.objects.filter(barcode__icontains=barcode).order_by('barcode')[:5]
        if products.exists():
            # 返回匹配的多个商品
            product_list = []
            for product in products:
                stock = StockScopeService.get_product_stock(product, warehouse_ids=warehouse_ids)
                    
                product_list.append({
                    'product_id': product.id,
                    'barcode': product.barcode,
                    'name': product.name,
                    'price': float(product.price),
                    'stock': stock
                })
                
            return JsonResponse({
                'success': True,
                'multiple_matches': True,
                'products': product_list
            })
        else:
            return JsonResponse({'success': False, 'message': '未找到商品'})


from .forms import ProductForm, InventoryTransactionForm, SaleForm, SaleItemForm

@login_required
def index(request):
    products = Product.objects.all()[:5]  # 获取最新的5个商品
    low_stock_items = Inventory.objects.filter(quantity__lte=F('warning_level'))[:5]  # 获取库存预警商品
    
    # 计算库存不足和无库存商品数量
    low_stock_count = Inventory.objects.filter(quantity__lte=F('warning_level')).count()
    out_of_stock_count = Inventory.objects.filter(quantity=0).count()
    
    context = {
        'products': products,
        'low_stock_items': low_stock_items,
        'low_stock_products': low_stock_count,
        'out_of_stock_products': out_of_stock_count,
    }
    return render(request, 'inventory/index.html', context)

@login_required
def product_list(request):
    products = Product.objects.all()
    categories = Category.objects.all()
    return render(request, 'inventory/product_list.html', {'products': products, 'categories': categories})

@login_required
def inventory_list(request):
    # 获取筛选参数
    category_id = request.GET.get('category', '')
    color = request.GET.get('color', '')
    size = request.GET.get('size', '')
    search_query = request.GET.get('search', '')
    
    # 基础查询
    inventory_items = Inventory.objects.select_related('product', 'product__category').all()
    
    # 应用筛选条件
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
    
    # 获取所有分类
    categories = Category.objects.all()
    
    # 获取所有可用的颜色和尺码
    colors = Product.COLOR_CHOICES
    sizes = Product.SIZE_CHOICES
    
    context = {
        'inventory_items': inventory_items,
        'categories': categories,
        'colors': colors,
        'sizes': sizes,
        'selected_category': category_id,
        'selected_color': color,
        'selected_size': size,
        'search_query': search_query,
    }
    
    return render(request, 'inventory/inventory_list.html', context)

@login_required
def sale_list(request):
    sales = Sale.objects.all().order_by('-created_at')
    return render(request, 'inventory/sale_list.html', {'sales': sales})

@login_required
def sale_detail(request, sale_id):
    """销售单详情视图"""
    sale = get_object_or_404(Sale, pk=sale_id)
    items = sale.items.all()
    
    context = {
        'sale': sale,
        'items': items,
    }
    
    return render(request, 'inventory/sale_detail.html', context)

@login_required
def product_create(request):
    initial_data = {}
    
    # 如果是从条码API跳转过来的，预填表单
    if request.method == 'GET' and 'barcode' in request.GET:
        initial_data = {
            'barcode': request.GET.get('barcode', ''),
            'name': request.GET.get('name', ''),
            'price': request.GET.get('price', 0),
            'specification': request.GET.get('specification', ''),
            'manufacturer': request.GET.get('manufacturer', ''),
            'description': request.GET.get('description', '')
        }
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save()
            Inventory.objects.get_or_create(product=product)
            
            # 记录操作日志
            from django.contrib.contenttypes.models import ContentType
            OperationLog.objects.create(
                operator=request.user,
                operation_type='INVENTORY',
                details=f'添加新商品: {product.name} (条码: {product.barcode})',
                related_object_id=product.id,
                related_content_type=ContentType.objects.get_for_model(Product)
            )
            
            messages.success(request, '商品添加成功')
            return redirect('product_list')
    else:
        form = ProductForm(initial=initial_data)
    
    return render(request, 'inventory/product_form.html', {
        'form': form,
        'is_from_barcode_api': bool(initial_data)
    })

@login_required
def product_edit(request, product_id):
    """编辑商品信息"""
    product = get_object_or_404(Product, id=product_id)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            
            # 记录操作日志
            from django.contrib.contenttypes.models import ContentType
            OperationLog.objects.create(
                operator=request.user,
                operation_type='INVENTORY',
                details=f'编辑商品: {product.name} (条码: {product.barcode})',
                related_object_id=product.id,
                related_content_type=ContentType.objects.get_for_model(Product)
            )
            
            messages.success(request, '商品信息更新成功')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'inventory/product_form.html', {
        'form': form,
        'product': product,
        'is_edit': True
    })

@login_required
def inventory_transaction_create(request):
    if request.method == 'POST':
        form = InventoryTransactionForm(request.POST, user=request.user)
        if form.is_valid():
            success, _, result = update_inventory(
                product=form.cleaned_data['product'],
                warehouse=form.cleaned_data.get('warehouse'),
                quantity=form.cleaned_data['quantity'],
                transaction_type='IN',
                operator=request.user,
                notes=form.cleaned_data.get('notes') or ''
            )

            if success:
                messages.success(request, '入库操作成功')
                return redirect('inventory_list')
            messages.error(request, f'入库失败: {result}')
    else:
        form = InventoryTransactionForm(user=request.user)
    return render(request, 'inventory/inventory_form.html', {'form': form})

@login_required
def sale_create(request):
    if request.method == 'POST':
        form = SaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            sale.operator = request.user
            sale.save()
            messages.success(request, '销售单创建成功')
            return redirect('sale_item_create', sale_id=sale.id)
    else:
        form = SaleForm()
    
    return render(request, 'inventory/sale_form.html', {
        'form': form
    })

@login_required
def sale_item_create(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    if request.method == 'POST':
        form = SaleItemForm(request.POST)
        if form.is_valid():
            sale_item = form.save(commit=False)
            sale_item.sale = sale

            if not check_inventory(sale_item.product, sale_item.quantity, warehouse=sale.warehouse):
                messages.error(request, '库存不足')
            else:
                success, _, result = update_inventory(
                    product=sale_item.product,
                    warehouse=sale.warehouse,
                    quantity=-sale_item.quantity,
                    transaction_type='OUT',
                    operator=request.user,
                    notes=f'销售单号：{sale.id}'
                )
                if success:
                    sale_item.save()
                    sale.update_total_amount()

                    messages.success(request, '商品添加成功')

                    # 记录操作日志
                    from django.contrib.contenttypes.models import ContentType
                    OperationLog.objects.create(
                        operator=request.user,
                        operation_type='SALE',
                        details=f'销售商品 {sale_item.product.name} 数量 {sale_item.quantity}',
                        related_object_id=sale.id,
                        related_content_type=ContentType.objects.get_for_model(Sale)
                    )
                    return redirect('sale_item_create', sale_id=sale.id)
                messages.error(request, f'扣减库存失败: {result}')
    else:
        form = SaleItemForm()
    
    sale_items = sale.items.all()
    return render(request, 'inventory/sale_item_form.html', {
        'form': form,
        'sale': sale,
        'sale_items': sale_items
    })

# 报表中心相关视图
@login_required
def reports_index(request):
    """报表中心首页，显示所有可用报表及其统计信息"""
    # 获取销售记录数量
    total_sales_count = Sale.objects.count()
    
    # 获取库存偏低的商品数量
    low_stock_count = Inventory.objects.filter(quantity__lt=F('warning_level')).count() or 0
    
    # 获取本月销售额
    current_month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_sales_amount = Sale.objects.filter(
        created_at__gte=current_month_start
    ).aggregate(total=Sum('final_amount'))['total'] or 0
    
    # 获取今日操作日志数量
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_log_count = OperationLog.objects.filter(timestamp__gte=today_start).count()
    
    context = {
        'total_sales_count': total_sales_count,
        'low_stock_count': low_stock_count,
        'monthly_sales_amount': monthly_sales_amount,
        'today_log_count': today_log_count,
    }
    
    return render(request, 'inventory/reports_index.html', context)

    return JsonResponse({'success': False, 'message': '不支持的请求方法'})
