"""
Stock scope service.
Provides warehouse-aware stock lookup helpers for read paths.
"""
from django.db.models import Sum

from inventory.models import Inventory, Warehouse, WarehouseInventory
from inventory.services.warehouse_scope_service import WarehouseScopeService


class StockScopeService:
    """Read-side stock helper with warehouse scope support and legacy fallback."""

    @classmethod
    def resolve_request_warehouse_ids(cls, request):
        """Resolve warehouse ids from request context."""
        user = getattr(request, 'user', None)
        raw_param = (request.GET.get('warehouse', 'all') or '').strip()

        if user and user.is_authenticated:
            selection = WarehouseScopeService.resolve_warehouse_selection(
                user=user,
                warehouse_param=raw_param,
                include_all_option=True,
            )
            return selection['warehouse_ids']

        if raw_param and raw_param != 'all':
            try:
                warehouse_id = int(raw_param)
            except (TypeError, ValueError):
                return []
            if Warehouse.objects.filter(id=warehouse_id, is_active=True).exists():
                return [warehouse_id]
            return []
        return None

    @staticmethod
    def get_product_stock(product, warehouse_ids=None):
        """Get stock for a single product in warehouse scope."""
        query = WarehouseInventory.objects.filter(product=product, warehouse__is_active=True)
        if warehouse_ids is not None:
            if not warehouse_ids:
                return 0
            query = query.filter(warehouse_id__in=warehouse_ids)

        scoped_total = query.aggregate(total=Sum('quantity'))['total']
        if scoped_total is not None:
            return int(scoped_total)

        # Legacy fallback is only for all-warehouse mode.
        if warehouse_ids is None:
            inventory = Inventory.objects.filter(product=product).only('quantity').first()
            if inventory:
                return int(inventory.quantity)
        return 0

    @classmethod
    def get_bulk_product_stock_map(cls, products, warehouse_ids=None):
        """Get stock map for multiple products in warehouse scope."""
        product_ids = list(products.values_list('id', flat=True))
        if not product_ids:
            return {}

        query = WarehouseInventory.objects.filter(
            product_id__in=product_ids,
            warehouse__is_active=True,
        )
        if warehouse_ids is not None:
            if not warehouse_ids:
                return {product_id: 0 for product_id in product_ids}
            query = query.filter(warehouse_id__in=warehouse_ids)

        stock_map = {
            row['product_id']: int(row['total_quantity'] or 0)
            for row in query.values('product_id').annotate(total_quantity=Sum('quantity'))
        }

        if warehouse_ids is None:
            missing_ids = [product_id for product_id in product_ids if product_id not in stock_map]
            if missing_ids:
                for inventory in Inventory.objects.filter(product_id__in=missing_ids).only('product_id', 'quantity'):
                    stock_map[inventory.product_id] = int(inventory.quantity)

        for product_id in product_ids:
            stock_map.setdefault(product_id, 0)
        return stock_map
