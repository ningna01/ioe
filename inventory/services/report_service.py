"""
Report generation and data analysis services.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, F, Case, When, Value
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone

from inventory.models import (
    Product, Inventory, WarehouseInventory,
    Sale, SaleItem, InventoryTransaction, OperationLog
)
from inventory.utils.date_utils import get_period_boundaries


def _normalize_date_range(start_date, end_date):
    """
    确保 end_date 包含整天，避免遗漏结束日数据。
    当 end_date 为 date 对象时，查询上界使用次日 00:00。
    """
    if not end_date:
        return start_date, end_date
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_date_upper = end_date + timedelta(days=1)
        return start_date, end_date_upper
    return start_date, end_date


class ReportService:
    """Service for generating reports and analyzing data."""
    
    @staticmethod
    def get_sales_by_period(start_date=None, end_date=None, period='day', sale_type=None, warehouse_ids=None):
        """
        Get sales data grouped by the specified period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            period: Grouping period - 'day', 'week', or 'month'
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            warehouse_ids: Optional list of warehouse ids for scope filtering
            
        Returns:
            QuerySet: Sales data grouped by period
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        start_date, end_date_upper = _normalize_date_range(start_date, end_date)

        # Truncate function based on period
        if period == 'day':
            trunc_func = TruncDay('created_at')
        elif period == 'week':
            trunc_func = TruncWeek('created_at')
        elif period == 'month':
            trunc_func = TruncMonth('created_at')
        elif period == 'quarter':
            trunc_func = TruncQuarter('created_at')
        elif period == 'year':
            trunc_func = TruncYear('created_at')
        else:
            trunc_func = TruncDay('created_at')

        # Query sales data
        sales_query = Sale.objects.filter(
            created_at__range=(start_date, end_date_upper)
        )

        if warehouse_ids is not None:
            if warehouse_ids:
                sales_query = sales_query.filter(warehouse_id__in=warehouse_ids)
            else:
                sales_query = sales_query.none()
        
        # Apply sale type filter if specified
        if sale_type and sale_type in ['retail', 'wholesale']:
            sales_query = sales_query.filter(items__sale_type=sale_type).distinct()
        
        sales_data = sales_query.annotate(
            period=trunc_func
        ).values(
            'period'
        ).annotate(
            total_sales=Sum('final_amount'),
            total_cost=Sum(F('items__quantity') * F('items__product__cost')),
            order_count=Count('id', distinct=True),
            item_count=Count('items')
        ).order_by('period')
        
        # Calculate profit（利润率 = 利润 / 销售额）
        for data in sales_data:
            data['profit'] = data['total_sales'] - (data['total_cost'] or 0)
            total_sales_val = data.get('total_sales') or 0
            if total_sales_val and total_sales_val > 0:
                data['profit_margin'] = (data['profit'] / total_sales_val) * 100
            else:
                data['profit_margin'] = 0
                
        return sales_data
    
    @staticmethod
    def get_top_selling_products(start_date=None, end_date=None, limit=10, sale_type=None, warehouse_ids=None):
        """
        Get top selling products for the given period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            limit: Number of products to return
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            warehouse_ids: Optional list of warehouse ids for scope filtering
            
        Returns:
            QuerySet: Top selling products
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        start_date, end_date_upper = _normalize_date_range(start_date, end_date)

        items_query = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date_upper)
        )

        if warehouse_ids is not None:
            if warehouse_ids:
                items_query = items_query.filter(sale__warehouse_id__in=warehouse_ids)
            else:
                items_query = items_query.none()

        # Apply sale type filter if specified
        if sale_type and sale_type in ['retail', 'wholesale']:
            items_query = items_query.filter(sale_type=sale_type)

        raw_data = list(items_query.values(
            'product__id',
            'product__name',
            'product__barcode',
            'product__category__name'
        ).annotate(
            total_quantity=Sum('quantity'),
            total_sales=Sum('subtotal'),
            total_cost=Sum(F('quantity') * F('product__cost'))
        ).annotate(
            profit=F('total_sales') - F('total_cost')
        ).order_by('-total_quantity')[:limit])

        # Python 后处理：利润率 = 利润/销售额，避免 total_sales=0 时除零
        for item in raw_data:
            total_sales_val = item.get('total_sales') or 0
            profit = item.get('profit') or 0
            item['profit_margin'] = (profit * 100 / total_sales_val) if total_sales_val else Decimal('0')

        return raw_data
    
    @staticmethod
    def get_inventory_turnover_rate(start_date=None, end_date=None, category=None, warehouse_ids=None):
        """
        Calculate inventory turnover rate for products.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            category: Optional category for filtering products
            warehouse_ids: Optional list of warehouse ids for scope filtering
            
        Returns:
            list: Inventory turnover rates for products
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        # 在规范化前计算 days（用于周转率计算）
        start_for_days = start_date.date() if hasattr(start_date, 'date') else start_date
        end_for_days = end_date.date() if hasattr(end_date, 'date') else end_date
        days = (end_for_days - start_for_days).days or 1

        start_date, end_date_upper = _normalize_date_range(start_date, end_date)

        # 保持历史无作用域模式逻辑不变，避免影响既有报表口径
        if warehouse_ids is None:
            inventory_query = Inventory.objects.select_related('product').all()
            if category:
                inventory_query = inventory_query.filter(product__category=category)

            sales_query = SaleItem.objects.filter(
                sale__created_at__range=(start_date, end_date_upper)
            )
            if category:
                sales_query = sales_query.filter(product__category=category)
            sales_data = sales_query.values('product').annotate(
                total_quantity=Sum('quantity')
            )
            sales_map = {item['product']: item['total_quantity'] for item in sales_data}

            txn_base_query = InventoryTransaction.objects.filter(
                created_at__range=(start_date, end_date_upper),
                warehouse__isnull=True
            )
            if category:
                txn_base_query = txn_base_query.filter(product__category=category)

            txn_sums = txn_base_query.exclude(
                transaction_type='ADJUST'
            ).values('product').annotate(
                total_in=Sum(Case(When(transaction_type='IN', then=F('quantity')), default=Value(0))),
                total_out=Sum(Case(When(transaction_type='OUT', then=F('quantity')), default=Value(0)))
            )
            txn_map = {item['product']: item for item in txn_sums}

            products_with_adjust = set(
                txn_base_query.filter(
                    transaction_type='ADJUST'
                ).values_list('product_id', flat=True).distinct()
            )

            product_turnover = []
            for inv in inventory_query:
                sold_quantity = sales_map.get(inv.product.id, 0)
                current_quantity = inv.quantity
                txn = txn_map.get(inv.product.id, {'total_in': 0, 'total_out': 0})

                if inv.product.id in products_with_adjust:
                    average_inventory = (current_quantity + sold_quantity) / 2
                else:
                    beginning = current_quantity - (txn.get('total_in') or 0) + (txn.get('total_out') or 0)
                    if beginning < 0:
                        average_inventory = (0 + current_quantity) / 2
                    else:
                        average_inventory = (beginning + current_quantity) / 2

                if average_inventory > 0:
                    turnover_rate = (sold_quantity / average_inventory) * (365 / days)
                    turnover_days = 365 / turnover_rate if turnover_rate > 0 else 9999
                else:
                    turnover_rate = 0
                    turnover_days = 9999

                product_turnover.append({
                    'product_id': inv.product.id,
                    'product_name': inv.product.name,
                    'product_code': inv.product.barcode,
                    'category': inv.product.category.name if inv.product.category else '',
                    'current_stock': current_quantity,
                    'sold_quantity': sold_quantity,
                    'avg_stock': average_inventory,
                    'turnover_rate': turnover_rate,
                    'turnover_days': turnover_days,
                })

            product_turnover.sort(key=lambda x: x['turnover_rate'], reverse=True)
            return product_turnover

        if not warehouse_ids:
            return []

        inventory_query = WarehouseInventory.objects.select_related('product').filter(
            warehouse_id__in=warehouse_ids
        )
        sales_query = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date_upper),
            sale__warehouse_id__in=warehouse_ids
        )
        txn_base_query = InventoryTransaction.objects.filter(
            created_at__range=(start_date, end_date_upper),
            warehouse_id__in=warehouse_ids
        )

        if category:
            inventory_query = inventory_query.filter(product__category=category)
            sales_query = sales_query.filter(product__category=category)
            txn_base_query = txn_base_query.filter(product__category=category)

        inventory_data = list(
            inventory_query.values(
                'product_id',
                'product__name',
                'product__barcode',
                'product__category__name',
            ).annotate(current_quantity=Sum('quantity'))
        )
        sales_data = sales_query.values('product').annotate(total_quantity=Sum('quantity'))
        sales_map = {item['product']: item['total_quantity'] for item in sales_data}

        txn_sums = txn_base_query.exclude(
            transaction_type='ADJUST'
        ).values('product').annotate(
            total_in=Sum(Case(When(transaction_type='IN', then=F('quantity')), default=Value(0))),
            total_out=Sum(Case(When(transaction_type='OUT', then=F('quantity')), default=Value(0)))
        )
        txn_map = {item['product']: item for item in txn_sums}
        products_with_adjust = set(
            txn_base_query.filter(
                transaction_type='ADJUST'
            ).values_list('product_id', flat=True).distinct()
        )

        product_turnover = []
        for inv in inventory_data:
            product_id = inv['product_id']
            sold_quantity = sales_map.get(product_id, 0) or 0
            current_quantity = inv['current_quantity'] or 0
            txn = txn_map.get(product_id, {'total_in': 0, 'total_out': 0})

            if product_id in products_with_adjust:
                average_inventory = (current_quantity + sold_quantity) / 2
            else:
                beginning = current_quantity - (txn.get('total_in') or 0) + (txn.get('total_out') or 0)
                if beginning < 0:
                    average_inventory = (0 + current_quantity) / 2
                else:
                    average_inventory = (beginning + current_quantity) / 2

            if average_inventory > 0:
                turnover_rate = (sold_quantity / average_inventory) * (365 / days)
                turnover_days = 365 / turnover_rate if turnover_rate > 0 else 9999
            else:
                turnover_rate = 0
                turnover_days = 9999

            product_turnover.append({
                'product_id': product_id,
                'product_name': inv.get('product__name') or '',
                'product_code': inv.get('product__barcode') or '',
                'category': inv.get('product__category__name') or '',
                'current_stock': current_quantity,
                'sold_quantity': sold_quantity,
                'avg_stock': average_inventory,
                'turnover_rate': turnover_rate,
                'turnover_days': turnover_days,
            })

        product_turnover.sort(key=lambda x: x['turnover_rate'], reverse=True)
        return product_turnover
    
    @staticmethod
    def get_profit_report(start_date=None, end_date=None, sale_type=None, warehouse_ids=None):
        """
        Generate a profit report for the given period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            warehouse_ids: Optional list of warehouse ids for scope filtering
            
        Returns:
            dict: Profit report data
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        start_date, end_date_upper = _normalize_date_range(start_date, end_date)

        # Sales data
        sales_query = Sale.objects.filter(
            created_at__range=(start_date, end_date_upper)
        )

        if warehouse_ids is not None:
            if warehouse_ids:
                sales_query = sales_query.filter(warehouse_id__in=warehouse_ids)
            else:
                sales_query = sales_query.none()
        
        # Apply sale type filter if specified
        if sale_type and sale_type in ['retail', 'wholesale']:
            sales_query = sales_query.filter(items__sale_type=sale_type).distinct()
        
        # Total sales
        total_sales = sales_query.aggregate(
            total_amount=Sum('total_amount'),
            final_amount=Sum('final_amount'),
            discount_amount=Sum('discount_amount')
        )
        
        # Calculate costs
        sale_items_query = SaleItem.objects.filter(sale__in=sales_query)
        
        # Apply sale type filter to items if specified
        if sale_type and sale_type in ['retail', 'wholesale']:
            sale_items_query = sale_items_query.filter(sale_type=sale_type)
        
        total_cost = sale_items_query.aggregate(
            cost=Sum(F('quantity') * F('product__cost'))
        )['cost'] or 0
        
        # Calculate profit
        gross_profit = (total_sales['final_amount'] or 0) - total_cost
        
        # Calculate by category
        category_data = list(sale_items_query.values(
            'product__category__name'
        ).annotate(
            sales=Sum('subtotal'),
            cost=Sum(F('quantity') * F('product__cost')),
            quantity=Sum('quantity')
        ).annotate(
            profit=F('sales') - F('cost')
        ).order_by('-profit'))
        
        # 利润率 = 利润/销售额，Python 后处理避免除零
        for d in category_data:
            sales_val = d.get('sales') or 0
            profit_val = d.get('profit') or 0
            d['profit_margin'] = (profit_val * 100 / sales_val) if sales_val else 0
        
        # 汇总利润率 = 利润/销售额
        total_sales_amount = total_sales['final_amount'] or 0
        profit_margin = (gross_profit / total_sales_amount) * 100 if total_sales_amount else 0
            
        return {
            'start_date': start_date,
            'end_date': end_date,
            'total_sales': total_sales['final_amount'] or 0,
            'total_cost': total_cost,
            'gross_profit': gross_profit,
            'profit_margin': profit_margin,
            'discount_amount': total_sales['discount_amount'] or 0,
            'order_count': sales_query.count(),
            'item_count': sale_items_query.count(),
            'category_data': category_data
        }

    @staticmethod
    def get_member_analysis(start_date=None, end_date=None):
        """
        会员分析功能已移除，返回空数据。
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            dict: 空会员分析数据
        """
        return {
            'level_distribution': [],
            'top_members': [],
            'new_members': 0,
            'active_members': 0,
            'total_members': 0,
            'activity_rate': 0,
            'member_sales': {'total_amount': 0, 'total_count': 0},
            'non_member_sales': {'total_amount': 0, 'total_count': 0},
            'member_avg_order': 0,
            'non_member_avg_order': 0,
        }

    @staticmethod
    def get_recharge_report(start_date=None, end_date=None):
        """
        会员充值功能已移除，返回空数据。
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            
        Returns:
            dict: 空充值报告数据
        """
        return {
            'summary': {
                'total_recharge_amount': 0,
                'total_recharge_count': 0,
                'total_actual_amount': 0,
                'avg_recharge_amount': 0,
                'recharged_member_count': 0
            },
            'daily_recharge': [],
            'payment_stats': [],
            'top_members': []
        }

    @staticmethod
    def get_operation_logs(start_date=None, end_date=None):
        """
        Get operation logs for the given period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            
        Returns:
            QuerySet: Operation logs
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=7)
        if not end_date:
            end_date = timezone.now()
        
        # 结束日期+1天以包含当天的记录
        end_date_inclusive = end_date + timedelta(days=1)
        
        # 获取日志记录
        logs = OperationLog.objects.filter(
            timestamp__range=(start_date, end_date_inclusive)
        ).select_related('operator', 'related_content_type').order_by('-timestamp')
        
        # 按操作类型统计
        operation_type_stats = OperationLog.objects.filter(
            timestamp__range=(start_date, end_date_inclusive)
        ).values('operation_type').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # 按操作员统计
        operator_stats = OperationLog.objects.filter(
            timestamp__range=(start_date, end_date_inclusive)
        ).values('operator__username').annotate(
            count=Count('id')
        ).order_by('-count')
        
        return {
            'logs': logs,
            'operation_type_stats': operation_type_stats,
            'operator_stats': operator_stats
        }
    
    @staticmethod
    def get_sales_by_type(start_date=None, end_date=None):
        """
        按销售方式分组统计销售数据
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            
        Returns:
            dict: Sales data grouped by sale type
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
        
        # 按销售方式统计
        sales_by_type = list(SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date)
        ).values('sale_type').annotate(
            total_sales=Sum('subtotal'),
            total_quantity=Sum('quantity'),
            total_cost=Sum(F('quantity') * F('product__cost')),
            order_count=Count('sale', distinct=True)
        ).annotate(
            profit=F('total_sales') - F('total_cost')
        ))
        
        # 利润率 = 利润/销售额，Python 后处理避免除零
        for item in sales_by_type:
            total_sales_val = item.get('total_sales') or 0
            profit_val = item.get('profit') or 0
            item['profit_margin'] = (profit_val * 100 / total_sales_val) if total_sales_val else 0
        
        return sales_by_type
    
    @staticmethod
    def get_sales_type_comparison(start_date=None, end_date=None):
        """
        对比零售和批发的销售数据
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            
        Returns:
            dict: Comparison data between retail and wholesale
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
        
        # 零售数据
        retail_data = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date),
            sale_type='retail'
        ).aggregate(
            total_sales=Sum('subtotal'),
            total_quantity=Sum('quantity'),
            total_cost=Sum(F('quantity') * F('product__cost')),
            order_count=Count('sale', distinct=True)
        )
        
        # 批发数据
        wholesale_data = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date),
            sale_type='wholesale'
        ).aggregate(
            total_sales=Sum('subtotal'),
            total_quantity=Sum('quantity'),
            total_cost=Sum(F('quantity') * F('product__cost')),
            order_count=Count('sale', distinct=True)
        )
        
        # 计算利润
        retail_profit = (retail_data['total_sales'] or 0) - (retail_data['total_cost'] or 0)
        wholesale_profit = (wholesale_data['total_sales'] or 0) - (wholesale_data['total_cost'] or 0)
        
        # 利润率 = 利润/销售额
        retail_margin = 0
        if retail_data['total_sales'] and retail_data['total_sales'] > 0:
            retail_margin = (retail_profit / retail_data['total_sales']) * 100
        
        wholesale_margin = 0
        if wholesale_data['total_sales'] and wholesale_data['total_sales'] > 0:
            wholesale_margin = (wholesale_profit / wholesale_data['total_sales']) * 100
        
        return {
            'retail': {
                'total_sales': retail_data['total_sales'] or 0,
                'total_quantity': retail_data['total_quantity'] or 0,
                'total_cost': retail_data['total_cost'] or 0,
                'profit': retail_profit,
                'profit_margin': retail_margin,
                'order_count': retail_data['order_count'] or 0
            },
            'wholesale': {
                'total_sales': wholesale_data['total_sales'] or 0,
                'total_quantity': wholesale_data['total_quantity'] or 0,
                'total_cost': wholesale_data['total_cost'] or 0,
                'profit': wholesale_profit,
                'profit_margin': wholesale_margin,
                'order_count': wholesale_data['order_count'] or 0
            }
        } 
