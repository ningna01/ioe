"""
Report generation and data analysis services.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, F, Q, Avg, ExpressionWrapper, FloatField, DecimalField
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.utils import timezone

from inventory.models import Product, Inventory, Sale, SaleItem, InventoryTransaction, OperationLog
from inventory.utils.date_utils import get_period_boundaries

class ReportService:
    """Service for generating reports and analyzing data."""
    
    @staticmethod
    def get_sales_by_period(start_date=None, end_date=None, period='day', sale_type=None):
        """
        Get sales data grouped by the specified period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            period: Grouping period - 'day', 'week', or 'month'
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            
        Returns:
            QuerySet: Sales data grouped by period
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
            
        # Truncate function based on period
        if period == 'day':
            trunc_func = TruncDay('created_at')
        elif period == 'week':
            trunc_func = TruncWeek('created_at')
        elif period == 'month':
            trunc_func = TruncMonth('created_at')
        else:
            trunc_func = TruncDay('created_at')
            
        # Query sales data
        sales_query = Sale.objects.filter(
            created_at__range=(start_date, end_date)
        )
        
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
        
        # Calculate profit
        for data in sales_data:
            data['profit'] = data['total_sales'] - (data['total_cost'] or 0)
            if data['total_cost'] and data['total_cost'] > 0:
                data['profit_margin'] = (data['profit'] / data['total_cost']) * 100
            else:
                data['profit_margin'] = 0
                
        return sales_data
    
    @staticmethod
    def get_top_selling_products(start_date=None, end_date=None, limit=10, sale_type=None):
        """
        Get top selling products for the given period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            limit: Number of products to return
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            
        Returns:
            QuerySet: Top selling products
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
            
        items_query = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date)
        )
        
        # Apply sale type filter if specified
        if sale_type and sale_type in ['retail', 'wholesale']:
            items_query = items_query.filter(sale_type=sale_type)
            
        return items_query.values(
            'product__id',
            'product__name',
            'product__barcode',
            'product__category__name'
        ).annotate(
            total_quantity=Sum('quantity'),
            total_sales=Sum('subtotal'),
            total_cost=Sum(F('quantity') * F('product__cost'))
        ).annotate(
            profit=F('total_sales') - F('total_cost'),
            profit_margin=ExpressionWrapper(
                F('profit') * 100 / F('total_cost'),
                output_field=DecimalField()
            )
        ).order_by('-total_quantity')[:limit]
    
    @staticmethod
    def get_inventory_turnover_rate(start_date=None, end_date=None, category=None):
        """
        Calculate inventory turnover rate for products.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            category: Optional category for filtering products
            
        Returns:
            list: Inventory turnover rates for products
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
        
        # Time period in days
        days = (end_date - start_date).days or 1  # Avoid division by zero
        
        # Get current inventory levels
        inventory_query = Inventory.objects.select_related('product').all()
        
        # Filter by category if specified
        if category:
            inventory_query = inventory_query.filter(product__category=category)
            
        inventory_data = inventory_query
        
        # Get sales within period
        sales_query = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date)
        )
        
        # Apply category filter if needed
        if category:
            sales_query = sales_query.filter(product__category=category)
            
        sales_data = sales_query.values('product').annotate(
            total_quantity=Sum('quantity')
        )
        
        # Create a map for quick lookup
        sales_map = {item['product']: item['total_quantity'] for item in sales_data}
        
        # Calculate turnover for each product
        product_turnover = []
        for inv in inventory_data:
            sold_quantity = sales_map.get(inv.product.id, 0)
            current_quantity = inv.quantity
            
            # Calculate average inventory (simple approximation)
            # For better accuracy, we would need historical inventory records
            average_inventory = (current_quantity + sold_quantity) / 2
            
            # Calculate turnover rate (annualized)
            if average_inventory > 0:
                turnover_rate = (sold_quantity / average_inventory) * (365 / days)
                turnover_days = 365 / turnover_rate if turnover_rate > 0 else float('inf')
            else:
                turnover_rate = 0
                turnover_days = float('inf')
                
            product_turnover.append({
                'product_id': inv.product.id,
                'product_name': inv.product.name,
                'product_code': inv.product.barcode,
                'category': inv.product.category.name,
                'current_stock': current_quantity,
                'sold_quantity': sold_quantity,
                'avg_stock': average_inventory,
                'turnover_rate': turnover_rate,
                'turnover_days': turnover_days
            })
            
        # Sort by turnover rate (descending)
        product_turnover.sort(key=lambda x: x['turnover_rate'], reverse=True)
            
        return product_turnover
    
    @staticmethod
    def get_profit_report(start_date=None, end_date=None, sale_type=None):
        """
        Generate a profit report for the given period.
        
        Args:
            start_date: Optional start date for filtering
            end_date: Optional end date for filtering
            sale_type: Optional sale type filter - 'retail', 'wholesale', or None for all
            
        Returns:
            dict: Profit report data
        """
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
            
        # Sales data
        sales_query = Sale.objects.filter(
            created_at__range=(start_date, end_date)
        )
        
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
        category_data = sale_items_query.values(
            'product__category__name'
        ).annotate(
            sales=Sum('subtotal'),
            cost=Sum(F('quantity') * F('product__cost')),
            quantity=Sum('quantity')
        ).annotate(
            profit=F('sales') - F('cost'),
            profit_margin=ExpressionWrapper(
                F('profit') * 100 / F('cost'),
                output_field=DecimalField()
            )
        ).order_by('-profit')
        
        # Profit margin
        profit_margin = 0
        if total_cost > 0:
            profit_margin = (gross_profit / total_cost) * 100
            
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
            'category_data': list(category_data)
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
        sales_by_type = SaleItem.objects.filter(
            sale__created_at__range=(start_date, end_date)
        ).values('sale_type').annotate(
            total_sales=Sum('subtotal'),
            total_quantity=Sum('quantity'),
            total_cost=Sum(F('quantity') * F('product__cost')),
            order_count=Count('sale', distinct=True)
        ).annotate(
            profit=F('total_sales') - F('total_cost'),
            profit_margin=ExpressionWrapper(
                F('profit') * 100 / F('total_cost'),
                output_field=DecimalField()
            )
        )
        
        return list(sales_by_type)
    
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
        
        retail_margin = 0
        if retail_data['total_cost'] and retail_data['total_cost'] > 0:
            retail_margin = (retail_profit / retail_data['total_cost']) * 100
        
        wholesale_margin = 0
        if wholesale_data['total_cost'] and wholesale_data['total_cost'] > 0:
            wholesale_margin = (wholesale_profit / wholesale_data['total_cost']) * 100
        
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