from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Sum, Count, Avg, Max
from django.db import transaction, connection
from django.utils import timezone
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from django.http import JsonResponse, HttpResponse
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from django.conf import settings
from django.utils.safestring import mark_safe
from django.urls import reverse

from inventory.models import (
    Sale,
    SaleItem,
    WarehouseInventory,
    OperationLog,
    Product,
    Category,
    Supplier,
    Warehouse,
    UserWarehouseAccess,
    check_inventory,
    update_inventory,
)  # Member, MemberTransaction, MemberLevel 已禁用
from inventory.forms import SaleForm, SaleItemForm
from inventory.services.warehouse_scope_service import WarehouseScopeService
from inventory.services.user_mode_service import is_sales_focus_user
from inventory.utils.query_utils import paginate_queryset


def _ensure_sale_module_access(user):
    WarehouseScopeService.ensure_any_warehouse_permission(
        user=user,
        required_permission=UserWarehouseAccess.PERMISSION_SALE,
        error_message='您无权访问销售模块',
    )


def _get_sale_status(sale):
    return (sale.status or '').strip().upper()


def _is_sale_completed(sale):
    return _get_sale_status(sale) == 'COMPLETED'


def _is_sale_unsettled(sale):
    return _get_sale_status(sale) == 'UNSETTLED'


def _is_sale_abandoned(sale):
    return _get_sale_status(sale) == 'ABANDONED'


def _is_sale_deposit_locked(sale):
    return _get_sale_status(sale) in {'UNSETTLED', 'ABANDONED'}


def _is_sale_deleted(sale):
    if _get_sale_status(sale) == 'DELETED':
        return True

    sale_content_type = ContentType.objects.get_for_model(Sale)
    return OperationLog.objects.filter(
        operation_type='SALE',
        related_object_id=sale.id,
        related_content_type=sale_content_type,
    ).filter(
        Q(details__startswith=f'删除销售单 #{sale.id}') |
        Q(details__startswith=f'取消销售单 #{sale.id}')
    ).exists()


def _sale_needs_inventory_revert(sale):
    """
    仅对已扣库存的单据执行回补。
    兼容旧流程中的 DRAFT 状态单据。
    """
    return _get_sale_status(sale) in {'COMPLETED', 'DRAFT'}


def _get_sale_for_user_or_404(user, sale_id):
    _ensure_sale_module_access(user)
    sale = get_object_or_404(Sale, pk=sale_id)
    WarehouseScopeService.ensure_sale_access(user, sale)
    return sale


def _get_available_stock_quantity(product, warehouse=None):
    """返回指定仓库当前可用库存数量。"""
    if warehouse is None:
        return 0

    warehouse_inventory = WarehouseInventory.objects.filter(
        product=product,
        warehouse=warehouse
    ).only('quantity').first()
    return warehouse_inventory.quantity if warehouse_inventory else 0


def _build_sale_inventory_notes(*, source, intent, sale, product, quantity, user_note=''):
    """统一销售链路库存交易备注，便于回放和排障。"""
    parts = [
        f"source={source}",
        f"intent={intent}",
        f"sale_id={sale.id}",
        f"warehouse_id={sale.warehouse_id}",
        f"product_id={product.id}",
        f"quantity={quantity}",
    ]
    cleaned_user_note = (user_note or '').strip()
    if cleaned_user_note:
        parts.append(f"user_note={cleaned_user_note}")
    return " | ".join(parts)


def _create_sale_stock_change_log(
    *,
    operator,
    sale,
    product,
    action,
    requested_quantity,
    delta_quantity,
    current_quantity,
    transaction_obj,
    source,
):
    warehouse_name = sale.warehouse.name if sale.warehouse else '未绑定仓库'
    OperationLog.objects.create(
        operator=operator,
        operation_type='SALE',
        details=(
            f'销售库存变更: 单据#{sale.id}; 动作={action}; 商品={product.name}; '
            f'仓库={warehouse_name}; 请求数量={requested_quantity}; 变更={delta_quantity:+d}; '
            f'当前库存={current_quantity}; 交易ID={transaction_obj.id}; 来源={source}'
        ),
        related_object_id=sale.id,
        related_content_type=ContentType.objects.get_for_model(Sale)
    )


@login_required
def sale_list(request):
    """销售单列表视图"""
    _ensure_sale_module_access(request.user)
    if is_sales_focus_user(request.user):
        return redirect('sale_create')

    today = timezone.now().date()
    base_sales = Sale.objects.select_related('operator', 'warehouse').prefetch_related('items').order_by('-created_at')
    base_sales = WarehouseScopeService.filter_sales_queryset(
        request.user,
        base_sales,
        required_permission=UserWarehouseAccess.PERMISSION_SALE,
    )
    # 从 GET 参数获取搜索和筛选条件
    search_query = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    legacy_sale_type = request.GET.get('sale_type', '').strip().lower()
    status_filter = request.GET.get('status_filter', '').strip().lower()
    sale_type_filter = request.GET.get('sale_type_filter', '').strip().lower()
    amount_scope = request.GET.get('amount_scope', 'total').strip().lower()

    if not status_filter:
        status_filter = 'deleted' if legacy_sale_type == 'deleted' else 'completed'
    if status_filter not in ['completed', 'unsettled', 'abandoned', 'deleted']:
        status_filter = 'completed'

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
    else:
        sales = sales.filter(status='COMPLETED')

    if search_query:
        sales = sales.filter(id__icontains=search_query)

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

        # 未结算单的销售额按定金计入统计，避免定金漏记。
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

    # 分页
    page_number = request.GET.get('page', 1)
    paginated_sales = paginate_queryset(sales, page_number)
    
    context = {
        'sales': paginated_sales,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'status_filter': status_filter,
        'sale_type_filter': sale_type_filter,
        'amount_scope': amount_scope,
        'amount_scope_label': amount_scope_labels[amount_scope],
        'today_sales': today_sales,
        'month_sales': month_sales,
        'total_sales': total_sales
    }

    return render(request, 'inventory/sale_list.html', context)

@login_required
def sale_detail(request, sale_id):
    """销售单详情视图"""
    sale = _get_sale_for_user_or_404(request.user, sale_id)
    items = SaleItem.objects.filter(sale=sale).select_related('product')
    
    # 确保销售单金额与商品项总和一致
    items_total = sum((item.subtotal or Decimal('0.00')) for item in items)
    if items_total > 0 and (sale.total_amount == 0 or abs(sale.total_amount - items_total) > Decimal('0.01')):
        print(f"警告: 销售单金额({sale.total_amount})与商品项总和({items_total})不一致，正在修复")
        if _is_sale_deposit_locked(sale):
            deposit_amount = sale.deposit_amount or Decimal('0.00')
            if deposit_amount < 0:
                deposit_amount = Decimal('0.00')
            if deposit_amount > items_total:
                deposit_amount = items_total

            Sale.objects.filter(pk=sale.id).update(
                total_amount=items_total,
                discount_amount=Decimal('0.00'),
                deposit_amount=deposit_amount,
                final_amount=deposit_amount
            )
            sale.refresh_from_db(fields=['total_amount', 'discount_amount', 'deposit_amount', 'final_amount'])
        else:
            discount_amount = sale.discount_amount or Decimal('0.00')
            if discount_amount < 0:
                discount_amount = Decimal('0.00')
            if discount_amount > items_total:
                discount_amount = items_total
            final_amount = items_total - discount_amount

            Sale.objects.filter(pk=sale.id).update(
                total_amount=items_total,
                discount_amount=discount_amount,
                final_amount=final_amount
            )
            sale.refresh_from_db(fields=['total_amount', 'discount_amount', 'final_amount'])
    
    context = {
        'sale': sale,
        'items': items,
    }
    
    return render(request, 'inventory/sale_detail.html', context)

@login_required
def sale_create(request):
    """创建销售单视图"""
    _ensure_sale_module_access(request.user)
    if request.method == 'POST':
        # 添加调试信息
        print("=" * 80)
        print("销售单提交数据：")
        for key, value in request.POST.items():
            print(f"{key}: {value}")
        print("=" * 80)

        available_warehouses = WarehouseScopeService.get_accessible_warehouses(
            request.user,
            required_permission=UserWarehouseAccess.PERMISSION_SALE,
        )
        warehouse_id = request.POST.get('warehouse')
        if warehouse_id:
            try:
                selected_warehouse = available_warehouses.get(id=int(warehouse_id))
            except (ValueError, TypeError, Warehouse.DoesNotExist):
                messages.error(request, '所选仓库无效或未授权，请重新选择')
                return redirect('sale_create')
        else:
            selected_warehouse = WarehouseScopeService.get_default_warehouse(request.user)
            if selected_warehouse is None or not WarehouseScopeService.can_access_warehouse(
                request.user,
                selected_warehouse,
                required_permission=UserWarehouseAccess.PERMISSION_SALE,
            ):
                selected_warehouse = available_warehouses.first()

        if selected_warehouse is None:
            messages.error(request, '当前用户没有可用仓库，请先配置仓库授权')
            return redirect('sale_create')

        settlement_type = (request.POST.get('settlement_type', 'completed') or 'completed').strip().lower()
        if settlement_type not in ['completed', 'unsettled']:
            settlement_type = 'completed'
        is_unsettled_sale = settlement_type == 'unsettled'
        
        # 获取前端提交的商品信息
        products_data = []
        for key, value in request.POST.items():
            if key.startswith('products[') and key.endswith('][id]'):
                index = key[9:-5]
                product_id = value
                quantity = request.POST.get(f'products[{index}][quantity]', 1)
                price = request.POST.get(f'products[{index}][price]', 0)
                sale_type = request.POST.get(f'products[{index}][sale_type]', 'retail')
                
                products_data.append({
                    'product_id': product_id,
                    'quantity': quantity,
                    'price': price,
                    'sale_type': sale_type
                })
        
        # 验证是否有商品数据
        if not products_data:
            messages.error(request, '销售单创建失败，未能找到任何商品数据。')
            return redirect('sale_create')
            
        # 验证商品数据
        valid_products = True
        valid_products_data = []
        
        for item_data in products_data:
            try:
                product = Product.objects.get(id=item_data['product_id'])
                # 解析数量
                try:
                    quantity = int(item_data['quantity'])
                    if quantity <= 0:
                        raise ValueError("Quantity must be positive")
                except (ValueError, TypeError):
                    print(f"Error parsing quantity for product {item_data['product_id']}: Value='{item_data['quantity']}'")
                    messages.error(request, f"商品 {product.name} 的数量 '{item_data['quantity']}' 无效。")
                    valid_products = False
                    continue

                # 获取销售方式（默认为零售）
                sale_type = item_data.get('sale_type', 'retail')
                
                # 解析价格
                try:
                    # 打印原始价格字符串用于调试
                    raw_price = item_data['price']
                    print(f"原始价格字符串: '{raw_price}', 类型: {type(raw_price)}, 销售方式: {sale_type}")
                    
                    # 确保价格是字符串
                    if not isinstance(raw_price, str):
                        raw_price = str(raw_price)
                    
                    # 尝试直接从前端获取价格
                    price = Decimal(raw_price.replace(',', '.'))
                    
                    if price <= 0:
                        # 如果解析的价格为0或负数，根据销售方式从数据库获取商品价格
                        if sale_type == 'wholesale' and product.wholesale_price:
                            db_price = product.wholesale_price
                            print(f"使用数据库中的商品批发价: {db_price}")
                        else:
                            db_price = Product.objects.filter(id=item_data['product_id']).values_list('price', flat=True).first()
                            print(f"使用数据库中的商品零售价: {db_price}")
                        if db_price:
                            price = Decimal(db_price)
                    
                    print(f"成功解析商品 {product.name} 的价格: {price}, 销售方式: {sale_type}")
                    
                    # 安全检查：如果价格仍然为0，中止处理
                    if price <= 0:
                        raise ValueError(f"商品价格不能为0或负数: {raw_price}")
                        
                except (InvalidOperation, ValueError, TypeError) as e:
                    print(f"Error parsing price for product {item_data['product_id']}: Value='{item_data['price']}', Error: {str(e)}")
                    messages.error(request, f"商品 {product.name} 的价格解析错误，请联系管理员。")
                    valid_products = False
                    continue

                # 未结算订单不锁库存；直接结算订单按仓库库存校验
                if is_unsettled_sale or check_inventory(product, quantity, selected_warehouse):
                    # 确保使用Decimal类型计算小计，避免精度问题
                    subtotal = price * Decimal(str(quantity))
                    print(f"商品 {product.name} 的小计: 价格={price} * 数量={quantity} = {subtotal}")
                    
                    valid_products_data.append({
                        'product': product,
                        'quantity': quantity,
                        'price': price,
                        'subtotal': subtotal,
                        'sale_type': sale_type,
                    })
                else:
                    available_quantity = _get_available_stock_quantity(product, selected_warehouse)
                    print(
                        f"Insufficient stock for product {product.id} ({product.name}): "
                        f"needed={quantity}, available={available_quantity}, warehouse={selected_warehouse.id if selected_warehouse else 'global'}"
                    )
                    messages.warning(
                        request,
                        f"商品 {product.name} 库存不足 (需要 {quantity}, 可用 {available_quantity})。该商品未添加到销售单。"
                    )
                    valid_products = False

            except Product.DoesNotExist:
                print(f"Error processing sale item: Product with ID {item_data['product_id']} does not exist.")
                messages.error(request, f"处理商品时出错：无效的商品 ID {item_data['product_id']}。")
                valid_products = False
            except Exception as e:
                print(f"Unexpected error processing sale item for product ID {item_data.get('product_id', 'N/A')}: {type(e).__name__} - {e}")
                messages.error(request, f"处理商品 ID {item_data.get('product_id', 'N/A')} 时发生意外错误。请联系管理员。")
                valid_products = False
        
        # 如果没有有效商品，返回错误
        if not valid_products_data:
            messages.error(request, '销售单创建失败，未能添加任何有效商品。')
            return redirect('sale_create')
            
        # 再次确认所有商品价格都有效
        for i, item in enumerate(valid_products_data):
            if item['price'] <= 0 or item['subtotal'] <= 0:
                print(f"警告：商品{i+1} {item['product'].name} 价格或小计为0，尝试从数据库重新获取价格")
                db_price = Product.objects.filter(id=item['product'].id).values_list('price', flat=True).first() or Decimal('0')
                if db_price > 0:
                    item['price'] = Decimal(db_price)
                    item['subtotal'] = item['price'] * Decimal(str(item['quantity']))
                    print(f"已更新商品 {item['product'].name} 的价格: {item['price']}, 小计: {item['subtotal']}")
            
        # 计算总金额
        total_amount_calculated = sum(item['subtotal'] for item in valid_products_data)
        print(f"后端计算的总金额: {total_amount_calculated}, 商品数量: {len(valid_products_data)}")
        
        # 验证计算是否正确
        if total_amount_calculated == 0 and valid_products_data:
            print("警告：后端计算的总金额为0，但有有效商品，检查每个商品的金额:")
            for i, item in enumerate(valid_products_data):
                print(f"商品{i+1}: {item['product'].name}, 价格={item['price']}, 数量={item['quantity']}, 小计={item['subtotal']}")
        
        # 获取前端提交的金额数据作为参考
        try:
            total_amount_frontend = Decimal(request.POST.get('total_amount', '0.00'))
            discount_amount_frontend = Decimal(request.POST.get('discount_amount', '0.00'))
            final_amount_frontend = Decimal(request.POST.get('final_amount', '0.00'))
            print(f"前端提交的金额 - 总金额: {total_amount_frontend}, 折扣: {discount_amount_frontend}, 最终金额: {final_amount_frontend}")
            
            # 决定使用哪个总金额
            if total_amount_calculated > 0:
                # 如果后端计算有效，优先使用后端计算的金额
                total_amount = total_amount_calculated
                
                # 会员折扣获取（已禁用）
                discount_rate = Decimal('1.0')  # 默认无折扣
                # member_id = request.POST.get('member')
                # 
                # if member_id:
                #     try:
                #         member = Member.objects.get(id=member_id)
                #         if member.level and member.level.discount is not None:
                #             discount_rate = Decimal(str(member.level.discount))
                #         print(f"会员折扣: 会员ID={member_id}, 折扣率={discount_rate}")
                #     except Member.DoesNotExist:
                #         print(f"找不到ID为{member_id}的会员，不应用折扣")
                # else:
                #     print("无会员信息，不应用折扣")
                
                discount_amount = total_amount * (Decimal('1.0') - discount_rate)
                final_amount = total_amount - discount_amount
                
                print(f"使用后端计算的金额: 总金额={total_amount}, 折扣率={discount_rate}, 折扣金额={discount_amount}, 最终金额={final_amount}")
            elif total_amount_frontend > 0:
                # 如果后端计算无效但前端有值，使用前端数据
                total_amount = total_amount_frontend
                discount_amount = discount_amount_frontend
                final_amount = final_amount_frontend
                print(f"使用前端提交的金额: 总金额={total_amount}, 折扣金额={discount_amount}, 最终金额={final_amount}")
            else:
                # 两者都无效，使用商品数据库价格重新计算
                print("警告：前端和后端计算的金额都无效，尝试使用数据库价格")
                db_total = Decimal('0.00')
                
                # 尝试从数据库获取每个商品的价格
                for item in valid_products_data:
                    product_id = item['product'].id
                    quantity = item['quantity']
                    db_price = Product.objects.filter(id=product_id).values_list('price', flat=True).first() or Decimal('0')
                    
                    if db_price > 0:
                        item_total = db_price * Decimal(str(quantity))
                        db_total += item_total
                        print(f"使用数据库价格: 商品ID={product_id}, 价格={db_price}, 数量={quantity}, 小计={item_total}")
                
                total_amount = db_total
                discount_amount = Decimal('0.00')
                final_amount = total_amount
                print(f"使用数据库价格计算的总金额: {total_amount}")
                
        except (InvalidOperation, ValueError, TypeError) as e:
            print(f"解析金额时出错: {e}，尝试使用数据库中的商品价格")
            # 尝试从数据库获取商品价格重新计算
            db_total = Decimal('0.00')
            for item in valid_products_data:
                product_id = item['product'].id
                quantity = item['quantity']
                db_price = Product.objects.filter(id=product_id).values_list('price', flat=True).first() or Decimal('0')
                
                if db_price > 0:
                    item_total = db_price * Decimal(str(quantity))
                    db_total += item_total
                    # 更新商品数据
                    item['price'] = db_price
                    item['subtotal'] = item_total
                    
            total_amount = db_total
            discount_amount = Decimal('0.00')
            final_amount = total_amount
            print(f"使用数据库价格计算的总金额: {total_amount}")
        
        # 最终安全检查，确保总金额大于0
        if total_amount <= 0 and valid_products_data:
            print("警告：计算的总金额仍然为0或负数，使用固定价格作为最后的保障")
            # 使用855.33作为固定价格，这只是一个保底措施
            total_amount = Decimal('855.33')
            discount_amount = Decimal('0.00')
            final_amount = total_amount

        deposit_amount = Decimal('0.00')
        if is_unsettled_sale:
            raw_deposit = (request.POST.get('deposit_amount', '0') or '').strip()
            try:
                deposit_amount = Decimal(raw_deposit)
            except (InvalidOperation, TypeError, ValueError):
                messages.error(request, '定金金额格式无效，请输入正确金额')
                return redirect('sale_create')

            if deposit_amount <= 0:
                messages.error(request, '未结算订单的定金金额必须大于 0')
                return redirect('sale_create')

            if deposit_amount > total_amount:
                messages.error(request, '定金金额不能大于订单应付金额')
                return redirect('sale_create')

            discount_amount = Decimal('0.00')
            final_amount = deposit_amount
        
        form = SaleForm(request.POST)
        if form.is_valid():
            # 创建销售单，但暂不保存
            sale = form.save(commit=False)
            sale.operator = request.user
            sale.warehouse = selected_warehouse
            sale.status = 'UNSETTLED' if is_unsettled_sale else 'COMPLETED'
            
            # 设置金额
            sale.total_amount = total_amount
            sale.discount_amount = discount_amount
            sale.final_amount = final_amount
            sale.deposit_amount = deposit_amount if is_unsettled_sale else Decimal('0.00')
            
            # 处理会员关联（已禁用）
            # member_id = request.POST.get('member')
            # if member_id:
            #     try:
            #         member = Member.objects.get(id=member_id)
            #         sale.member = member
            #     except Member.DoesNotExist:
            #         pass
            
            # 设置支付方式
            submitted_payment_method = (request.POST.get('payment_method', 'cash') or 'cash').strip()
            allowed_payment_methods = {code for code, _ in Sale.PAYMENT_METHODS}
            if submitted_payment_method not in allowed_payment_methods:
                submitted_payment_method = 'cash'
            sale.payment_method = submitted_payment_method
            
            # 设置积分（已禁用：实付金额的整数部分）
            # sale.points_earned = int(sale.final_amount) if sale.final_amount is not None else 0
            
            # 保存销售单基本信息
            sale.save()
            
            # 使用事务处理，确保所有操作要么全部成功，要么全部失败
            try:
                with transaction.atomic():
                    # 添加商品项；仅直接结账单据在此时扣减库存
                    for item_data in valid_products_data:
                        # 手动创建SaleItem，避免触发连锁更新
                        sale_item = SaleItem(
                            sale=sale,
                            product=item_data['product'],
                            quantity=item_data['quantity'],
                            price=item_data['price'],
                            actual_price=item_data['price'],
                            subtotal=item_data['subtotal'],
                            sale_type=item_data.get('sale_type', 'retail')
                        )
                        
                        # 确保小计已设置
                        if not sale_item.subtotal or sale_item.subtotal == 0:
                            sale_item.subtotal = sale_item.price * sale_item.quantity
                            print(f"重新计算小计: {sale_item.price} * {sale_item.quantity} = {sale_item.subtotal}")
                        
                        # 保存SaleItem到数据库（库存写入由后续服务层统一处理）
                        sale_item.save(sync_sale_totals=False)
                        
                        # 打印保存后的数据，确认数据正确
                        print(f"保存的SaleItem - ID: {sale_item.id}, 商品: {sale_item.product.name}, "
                              f"价格: {sale_item.price}, 数量: {sale_item.quantity}, 小计: {sale_item.subtotal}")
                        
                        # 直接使用SQL更新记录，确保价格正确
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "UPDATE inventory_saleitem SET price = %s, actual_price = %s, subtotal = %s WHERE id = %s",
                                [str(item_data['price']), str(item_data['price']), str(item_data['subtotal']), sale_item.id]
                            )
                            print(f"直接执行SQL更新SaleItem记录: id={sale_item.id}, price={item_data['price']}, subtotal={item_data['subtotal']}")
                        
                        # 强制重新加载销售项
                        sale_item = SaleItem.objects.get(id=sale_item.id)
                        print(f"重新加载后的SaleItem - ID: {sale_item.id}, 价格: {sale_item.price}, 小计: {sale_item.subtotal}")
                        
                        if not is_unsettled_sale:
                            stock_notes = _build_sale_inventory_notes(
                                source='sale_create',
                                intent='sale_create_item_out',
                                sale=sale,
                                product=item_data['product'],
                                quantity=item_data['quantity'],
                            )
                            success, inventory_obj, stock_result = update_inventory(
                                product=item_data['product'],
                                warehouse=selected_warehouse,
                                quantity=-item_data['quantity'],
                                transaction_type='OUT',
                                operator=request.user,
                                notes=stock_notes
                            )
                            if not success:
                                raise ValueError(
                                    f"商品 {item_data['product'].name} 库存更新失败: {stock_result}"
                                )
                            
                            stock_transaction = stock_result
                            _create_sale_stock_change_log(
                                operator=request.user,
                                sale=sale,
                                product=item_data['product'],
                                action='出库',
                                requested_quantity=item_data['quantity'],
                                delta_quantity=-item_data['quantity'],
                                current_quantity=inventory_obj.quantity,
                                transaction_obj=stock_transaction,
                                source='sale_create',
                            )
                    
                    # 如果有会员，更新会员积分和消费记录（已禁用）
                    # if sale.member:
                    #     sale.member.points += sale.points_earned
                    #     sale.member.purchase_count += 1
                    #     sale.member.total_spend += sale.final_amount
                    #     sale.member.save()
                    
                    # 记录销售操作日志
                    if is_unsettled_sale:
                        operation_details = (
                            f'创建未结算销售单 #{sale.id}，定金: {sale.deposit_amount}，'
                            f'商品总额: {sale.total_amount}，支付方式: {sale.get_payment_method_display()}，'
                            f'仓库: {selected_warehouse.name}；来源: sale_create'
                        )
                    else:
                        operation_details = (
                            f'完成销售单 #{sale.id}，总金额: {sale.final_amount}，'
                            f'支付方式: {sale.get_payment_method_display()}，仓库: {selected_warehouse.name}；'
                            f'来源: sale_create'
                        )
                    OperationLog.objects.create(
                        operator=request.user,
                        operation_type='SALE',
                        details=operation_details,
                        related_object_id=sale.id,
                        related_content_type=ContentType.objects.get_for_model(Sale)
                    )
                    
                    # 最后确保销售单金额正确
                    with connection.cursor() as cursor:
                        # 将Decimal转换为字符串，避免数据类型问题
                        total_str = str(total_amount)
                        discount_str = str(discount_amount)
                        final_str = str(final_amount)
                        points = int(final_amount) if (final_amount and not is_unsettled_sale) else 0
                        
                        print(f"更新销售单最终金额: total={total_str}, discount={discount_str}, final={final_str}, points={points}")
                        
                        cursor.execute(
                            "UPDATE inventory_sale SET total_amount = %s, discount_amount = %s, final_amount = %s, points_earned = %s WHERE id = %s",
                            [total_str, discount_str, final_str, points, sale.id]
                        )
                        print(f"直接执行SQL更新Sale记录: id={sale.id}, total={total_str}, discount={discount_str}, final={final_str}")
                
                # 从数据库重新获取销售单，确保显示正确的金额
                refreshed_sale = get_object_or_404(Sale, pk=sale.id)
                print(f"刷新后的销售单金额: total={refreshed_sale.total_amount}, discount={refreshed_sale.discount_amount}, final={refreshed_sale.final_amount}")
                
                # 交易成功，显示成功消息
                if is_sales_focus_user(request.user):
                    if is_unsettled_sale:
                        messages.success(request, '未结算订单创建成功，库存未扣减，已进入新建销售页面')
                    else:
                        messages.success(request, '销售单创建成功，已进入新建销售页面')
                    return redirect('sale_create')

                if is_unsettled_sale:
                    messages.success(request, '未结算订单创建成功，库存未扣减')
                else:
                    messages.success(request, '销售单创建成功')
                return redirect('sale_detail', sale_id=sale.id)
                
            except Exception as e:
                # 出现任何异常，回滚事务
                print(f"创建销售单时发生错误: {type(e).__name__} - {e}")
                messages.error(request, f'创建销售单时发生错误: {str(e)}')
                # 由于使用了事务，所有数据库操作都会自动回滚
                return redirect('sale_create')
        else:
            # 表单验证失败
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = SaleForm()
    
    # 获取会员等级列表（已禁用）
    # from inventory.models import MemberLevel
    # member_levels = MemberLevel.objects.all()
    
    warehouses = WarehouseScopeService.get_accessible_warehouses(
        request.user,
        required_permission=UserWarehouseAccess.PERMISSION_SALE,
    )
    default_warehouse = WarehouseScopeService.get_default_warehouse(request.user)
    if default_warehouse and not WarehouseScopeService.can_access_warehouse(
        request.user,
        default_warehouse,
        required_permission=UserWarehouseAccess.PERMISSION_SALE,
    ):
        default_warehouse = warehouses.first()
    selected_warehouse_id = request.POST.get('warehouse', '') if request.method == 'POST' else (
        str(default_warehouse.id) if default_warehouse else ''
    )

    return render(request, 'inventory/sale_form.html', {
        'form': form,
        'warehouses': warehouses,
        'selected_warehouse_id': selected_warehouse_id,
    })

@login_required
def sale_item_create(request, sale_id):
    """添加销售单商品视图"""
    _ensure_sale_module_access(request.user)
    sale = _get_sale_for_user_or_404(request.user, sale_id)

    if _is_sale_completed(sale):
        messages.error(request, '已完成的销售单不能新增商品')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_abandoned(sale):
        messages.error(request, '已放弃的销售单不能新增商品')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_deleted(sale):
        messages.error(request, '已删除的销售单不能新增商品')
        return redirect('sale_detail', sale_id=sale.id)

    if request.method == 'POST':
        form = SaleItemForm(request.POST, warehouse=sale.warehouse)
        if form.is_valid():
            sale_item = form.save(commit=False)
            sale_item.sale = sale
            
            # 确保price字段也被设置
            if hasattr(sale_item, 'actual_price') and not hasattr(sale_item, 'price'):
                sale_item.price = sale_item.actual_price
            elif hasattr(sale_item, 'price') and not hasattr(sale_item, 'actual_price'):
                sale_item.actual_price = sale_item.price

            if (not _is_sale_unsettled(sale)) and (not check_inventory(sale_item.product, sale_item.quantity, sale.warehouse)):
                available_quantity = _get_available_stock_quantity(sale_item.product, sale.warehouse)
                messages.error(
                    request,
                    f'商品 {sale_item.product.name} 库存不足（当前可用: {available_quantity}，请求数量: {sale_item.quantity}）'
                )
                sale_items = sale.items.all()
                return render(request, 'inventory/sale_item_form.html', {
                    'form': form,
                    'sale': sale,
                    'items': sale_items
                })

            try:
                with transaction.atomic():
                    # 保存销售项；库存写入由后续服务层统一处理
                    sale_item.save(sync_sale_totals=False)
                    sale.update_total_amount()
                    sale.save()

                    if not _is_sale_unsettled(sale):
                        stock_notes = _build_sale_inventory_notes(
                            source='sale_item_create',
                            intent='sale_item_create_out',
                            sale=sale,
                            product=sale_item.product,
                            quantity=sale_item.quantity,
                        )
                        success, inventory_obj, stock_result = update_inventory(
                            product=sale_item.product,
                            warehouse=sale.warehouse,
                            quantity=-sale_item.quantity,
                            transaction_type='OUT',
                            operator=request.user,
                            notes=stock_notes
                        )
                        if not success:
                            raise ValueError(stock_result)

                        stock_transaction = stock_result
                        _create_sale_stock_change_log(
                            operator=request.user,
                            sale=sale,
                            product=sale_item.product,
                            action='出库',
                            requested_quantity=sale_item.quantity,
                            delta_quantity=-sale_item.quantity,
                            current_quantity=inventory_obj.quantity,
                            transaction_obj=stock_transaction,
                            source='sale_item_create',
                        )

                if _is_sale_unsettled(sale):
                    messages.success(request, '商品添加成功（未结算订单暂不扣减库存）')
                else:
                    messages.success(request, '商品添加成功')
                return redirect('sale_item_create', sale_id=sale.id)
            except Exception as e:
                messages.error(request, f'商品添加失败: {str(e)}')
    else:
        form = SaleItemForm(warehouse=sale.warehouse)
    
    sale_items = sale.items.all()
    return render(request, 'inventory/sale_item_form.html', {
        'form': form,
        'sale': sale,
        'items': sale_items
    })

@login_required
def sale_complete(request, sale_id):
    """完成销售视图"""
    _ensure_sale_module_access(request.user)
    sale = _get_sale_for_user_or_404(request.user, sale_id)
    if _is_sale_deleted(sale):
        messages.error(request, '已删除的销售单不能执行完成操作')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_abandoned(sale):
        messages.error(request, '已放弃的销售单不能执行完成操作')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_completed(sale):
        messages.warning(request, '销售单已完成，请勿重复提交')
        return redirect('sale_detail', sale_id=sale.id)

    sale_was_unsettled = _is_sale_unsettled(sale)

    if request.method == 'POST':
        form = SaleForm(request.POST, instance=sale)
        if form.is_valid():
            sale = form.save(commit=False)
            sale.operator = request.user
            sale.status = 'COMPLETED'

            # 更新总金额（防止异常情况）
            sale.update_total_amount()

            # 设置支付方式
            payment_method = (request.POST.get('payment_method', '') or '').strip()
            if payment_method:
                allowed_payment_methods = {code for code, _ in Sale.PAYMENT_METHODS}
                if payment_method not in allowed_payment_methods:
                    payment_method = 'cash'
                sale.payment_method = payment_method

            try:
                with transaction.atomic():
                    if sale_was_unsettled:
                        sale_items = list(sale.items.select_related('product'))
                        for item in sale_items:
                            if not check_inventory(item.product, item.quantity, sale.warehouse):
                                available_quantity = _get_available_stock_quantity(item.product, sale.warehouse)
                                messages.error(
                                    request,
                                    f'商品 {item.product.name} 库存不足（当前可用: {available_quantity}，请求数量: {item.quantity}）'
                                )
                                raise ValueError('库存不足，无法完成结算')

                        for item in sale_items:
                            stock_notes = _build_sale_inventory_notes(
                                source='sale_complete',
                                intent='sale_complete_unsettled_out',
                                sale=sale,
                                product=item.product,
                                quantity=item.quantity,
                                user_note='settle_unsettled_sale',
                            )
                            success, inventory_obj, stock_result = update_inventory(
                                product=item.product,
                                warehouse=sale.warehouse,
                                quantity=-item.quantity,
                                transaction_type='OUT',
                                operator=request.user,
                                notes=stock_notes
                            )
                            if not success:
                                raise ValueError(stock_result)

                            stock_transaction = stock_result
                            _create_sale_stock_change_log(
                                operator=request.user,
                                sale=sale,
                                product=item.product,
                                action='出库',
                                requested_quantity=item.quantity,
                                delta_quantity=-item.quantity,
                                current_quantity=inventory_obj.quantity,
                                transaction_obj=stock_transaction,
                                source='sale_complete',
                            )

                    deposit_before = sale.deposit_amount or Decimal('0.00')
                    sale.save()

                    remaining_amount = sale.final_amount - deposit_before if sale.final_amount > deposit_before else Decimal('0.00')
                    if sale_was_unsettled:
                        details = (
                            f'未结算销售单结算完成 #{sale.id}，原定金: {deposit_before}，'
                            f'补收金额: {remaining_amount}，最终金额: {sale.final_amount}，'
                            f'支付方式: {sale.get_payment_method_display()}；来源: sale_complete'
                        )
                    else:
                        details = (
                            f'完成销售单 #{sale.id}，总金额: {sale.final_amount}，'
                            f'支付方式: {sale.get_payment_method_display()}；来源: sale_complete'
                        )

                    OperationLog.objects.create(
                        operator=request.user,
                        operation_type='SALE',
                        details=details,
                        related_object_id=sale.id,
                        related_content_type=ContentType.objects.get_for_model(Sale)
                    )
            except ValueError as exc:
                if '库存不足' not in str(exc):
                    messages.error(request, f'完成销售失败: {exc}')
                return redirect('sale_detail', sale_id=sale.id)
            except Exception as exc:
                messages.error(request, f'完成销售失败: {exc}')
                return redirect('sale_detail', sale_id=sale.id)
            
            if is_sales_focus_user(request.user):
                if sale_was_unsettled:
                    messages.success(request, '未结算销售单已完成结算，已进入新建销售页面')
                else:
                    messages.success(request, '销售单已完成，已进入新建销售页面')
                return redirect('sale_create')

            if sale_was_unsettled:
                messages.success(request, '未结算销售单已完成结算')
            else:
                messages.success(request, '销售单已完成')
            return redirect('sale_detail', sale_id=sale.id)
    else:
        form = SaleForm(instance=sale)
    
    return render(request, 'inventory/sale_complete.html', {
        'form': form,
        'sale': sale,
        'items': sale.items.all(),
        'payment_method_choices': Sale.PAYMENT_METHODS,
    })

@login_required
def sale_cancel(request, sale_id):
    """删除销售单并回滚相关业务影响。"""
    _ensure_sale_module_access(request.user)
    sale = _get_sale_for_user_or_404(request.user, sale_id)

    if _is_sale_deleted(sale):
        messages.warning(request, '销售单已删除，请勿重复操作')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_abandoned(sale):
        messages.warning(request, '未结算销售单已放弃，请勿重复操作')
        return redirect('sale_detail', sale_id=sale.id)
    
    if request.method == 'POST':
        reason = request.POST.get('reason', '')

        try:
            with transaction.atomic():
                restored_items = 0
                sale_was_unsettled = _is_sale_unsettled(sale)
                if _sale_needs_inventory_revert(sale):
                    for item in sale.items.select_related('product'):
                        stock_notes = _build_sale_inventory_notes(
                            source='sale_cancel',
                            intent='sale_cancel_restore_in',
                            sale=sale,
                            product=item.product,
                            quantity=item.quantity,
                            user_note='delete_sale_restore_stock',
                        )
                        success, inventory_obj, stock_result = update_inventory(
                            product=item.product,
                            warehouse=sale.warehouse,
                            quantity=item.quantity,
                            transaction_type='IN',
                            operator=request.user,
                            notes=stock_notes
                        )
                        if not success:
                            raise ValueError(stock_result)

                        stock_transaction = stock_result
                        _create_sale_stock_change_log(
                            operator=request.user,
                            sale=sale,
                            product=item.product,
                            action='回补',
                            requested_quantity=item.quantity,
                            delta_quantity=item.quantity,
                            current_quantity=inventory_obj.quantity,
                            transaction_obj=stock_transaction,
                            source='sale_cancel',
                        )
                        restored_items += 1

                reason_text = reason.strip() if reason else '未填写'
                removed_amount = sale.final_amount or Decimal('0.00')
                removed_deposit = sale.deposit_amount or Decimal('0.00')
                if sale_was_unsettled:
                    sale.status = 'ABANDONED'
                    # 未结算放弃单保留定金记账口径，仅改变业务状态用于区分“已删除”。
                    sale.save(update_fields=['status', 'deposit_amount', 'final_amount'])
                    OperationLog.objects.create(
                        operator=request.user,
                        operation_type='SALE',
                        details=(
                            f'放弃未结算销售单 #{sale.id}，原因: {reason_text}；'
                            f'保留定金金额: {removed_deposit}；'
                            f'来源: sale_cancel'
                        ),
                        related_object_id=sale.id,
                        related_content_type=ContentType.objects.get_for_model(Sale)
                    )
                else:
                    sale.status = 'DELETED'
                    # 删除已结算单时冲销记账金额。
                    sale.deposit_amount = Decimal('0.00')
                    sale.final_amount = Decimal('0.00')
                    sale.save(update_fields=['status', 'deposit_amount', 'final_amount'])
                    OperationLog.objects.create(
                        operator=request.user,
                        operation_type='SALE',
                        details=(
                            f'删除销售单 #{sale.id}，原因: {reason_text}；'
                            f'回补商品项: {restored_items}；移除金额口径: {removed_amount}；'
                            f'冲销定金: {removed_deposit}；'
                            f'来源: sale_cancel'
                        ),
                        related_object_id=sale.id,
                        related_content_type=ContentType.objects.get_for_model(Sale)
                    )
        except Exception as exc:
            messages.error(request, f'删除销售单失败: {exc}')
            return redirect('sale_detail', sale_id=sale.id)

        if sale_was_unsettled:
            messages.success(request, '未结算销售单已标记为已放弃，定金保留在销售额中')
        else:
            messages.success(request, '销售单已删除，库存与金额统计已回滚')
        return redirect('sale_list')
    
    return render(request, 'inventory/sale_cancel.html', {'sale': sale})

@login_required
def sale_delete_item(request, sale_id, item_id):
    """删除销售单商品视图"""
    _ensure_sale_module_access(request.user)
    sale = _get_sale_for_user_or_404(request.user, sale_id)

    if request.method != 'POST':
        messages.error(request, '无效请求方式，删除操作必须使用 POST')
        return redirect('sale_detail', sale_id=sale.id)
    
    # 检查销售单状态
    if _is_sale_completed(sale):
        messages.error(request, '已完成的销售单不能修改')
        return redirect('sale_detail', sale_id=sale.id)
    if _is_sale_abandoned(sale):
        messages.error(request, '已放弃的销售单不能修改')
        return redirect('sale_detail', sale_id=sale.id)

    if _is_sale_deleted(sale):
        messages.error(request, '已删除的销售单不能修改')
        return redirect('sale_detail', sale_id=sale.id)

    item = SaleItem.objects.select_related('product').filter(id=item_id, sale=sale).first()
    if item is None:
        messages.warning(request, '销售商品已删除，请勿重复提交')
        return redirect('sale_item_create', sale_id=sale.id)
    
    try:
        with transaction.atomic():
            if not _is_sale_unsettled(sale):
                stock_notes = _build_sale_inventory_notes(
                    source='sale_delete_item',
                    intent='sale_delete_item_in',
                    sale=sale,
                    product=item.product,
                    quantity=item.quantity,
                    user_note='delete_item_restore_stock',
                )
                success, inventory_obj, stock_result = update_inventory(
                    product=item.product,
                    warehouse=sale.warehouse,
                    quantity=item.quantity,
                    transaction_type='IN',
                    operator=request.user,
                    notes=stock_notes
                )
                if not success:
                    raise ValueError(stock_result)

                stock_transaction = stock_result
                _create_sale_stock_change_log(
                    operator=request.user,
                    sale=sale,
                    product=item.product,
                    action='回补',
                    requested_quantity=item.quantity,
                    delta_quantity=item.quantity,
                    current_quantity=inventory_obj.quantity,
                    transaction_obj=stock_transaction,
                    source='sale_delete_item',
                )

            # 删除商品并更新销售单总额
            item.delete()
            sale.update_total_amount()
            sale.save()
    except Exception as e:
        messages.error(request, f'删除销售商品失败: {str(e)}')
        return redirect('sale_item_create', sale_id=sale.id)
    
    messages.success(request, '商品已从销售单中删除')
    return redirect('sale_item_create', sale_id=sale.id)

# @login_required
# def member_purchases(request):
#     """会员购买历史报表"""
#     # 获取查询参数
#     member_id = request.GET.get('member_id')
#     start_date = request.GET.get('start_date')
#     end_date = request.GET.get('end_date')
#     
#     # 初始查询集
#     sales = Sale.objects.filter(member__isnull=False)
#     member = None
#     
#     # 应用筛选
#     if member_id:
#         try:
#             member = Member.objects.get(pk=member_id)
#             sales = sales.filter(member=member)
#         except (Member.DoesNotExist, ValueError):
#             messages.error(request, '无效的会员ID')
#     
#     # 日期筛选
#     if start_date:
#         try:
#             start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
#             sales = sales.filter(created_at__date__gte=start_date_obj)
#         except ValueError:
#             messages.error(request, '开始日期格式无效')
#     
#     if end_date:
#         try:
#             end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
#             sales = sales.filter(created_at__date__lte=end_date_obj)
#         except ValueError:
#             messages.error(request, '结束日期格式无效')
#     
#     # 按会员分组统计
#     if not member_id:
#         member_stats = sales.values(
#             'member__id', 'member__name', 'member__phone'
#         ).annotate(
#             total_amount=Sum('total_amount'),
#             total_sales=Count('id'),
#             avg_amount=Avg('total_amount'),
#             last_purchase=Max('created_at')
#         ).order_by('-total_amount')
#         
#         context = {
#             'member_stats': member_stats,
#             'start_date': start_date,
#             'end_date': end_date
#         }
#         return render(request, 'inventory/member_purchases.html', context)
#     
#     # 会员详细信息
#     sales = sales.order_by('-created_at')
#     
#     context = {
#         'member': member,
#         'sales': sales,
#         'start_date': start_date,
#         'end_date': end_date,
#         'total_amount': sales.aggregate(total=Sum('total_amount'))['total'] or 0
#     }
#     
#     return render(request, 'inventory/member_purchase_details.html', context)

# @login_required
# def birthday_members_report(request):
#     """生日会员报表"""
#     # 获取查询参数
#     month = request.GET.get('month')
#     
#     # 默认显示当月
#     if not month:
#         month = timezone.now().month
#     else:
#         try:
#             month = int(month)
#             if month < 1 or month > 12:
#                 month = timezone.now().month
#         except ValueError:
#             month = timezone.now().month
#     
#     # 获取指定月份的生日会员
#     members = Member.objects.filter(
#         birthday__isnull=False,  # 确保生日字段不为空
#         birthday__month=month,
#         is_active=True
#     ).order_by('birthday__day')
#     
#     # 计算各项统计数据
#     total_members = members.count()
#     
#     # 即将到来的生日会员(7天内)
#     today = timezone.now().date()
#     upcoming_birthdays = []
#     
#     for member in members:
#         if member.birthday:
#             # 计算今年的生日日期
#             current_year = today.year
#             birthday_this_year = date(current_year, member.birthday.month, member.birthday.day)
#             
#             # 如果今年的生日已经过了，计算明年的生日
#             if birthday_this_year < today:
#                 birthday_this_year = date(current_year + 1, member.birthday.month, member.birthday.day)
#             
#             # 计算距离生日还有多少天
#             days_until_birthday = (birthday_this_year - today).days
#             
#             # 如果在7天内
#             if 0 <= days_until_birthday <= 7:
#                 upcoming_birthdays.append({
#                     'member': member,
#                     'days_until_birthday': days_until_birthday,
#                     'birthday_date': birthday_this_year
#                 })
#     
#     # 按距离生日天数排序
#     upcoming_birthdays.sort(key=lambda x: x['days_until_birthday'])
#     
#     context = {
#         'members': members,
#         'total_members': total_members,
#         'month': month,
#         'month_name': {
#             1: '一月', 2: '二月', 3: '三月', 4: '四月',
#             5: '五月', 6: '六月', 7: '七月', 8: '八月',
#             9: '九月', 10: '十月', 11: '十一月', 12: '十二月'
#         }[month],
#         'upcoming_birthdays': upcoming_birthdays
#     }
# 
#     return render(request, 'inventory/birthday_members_report.html', context)
