"""
Warehouse scope service.
Provides unified warehouse visibility and authorization checks.
"""
from inventory.exceptions import AuthorizationError
from inventory.models import UserWarehouseAccess, Warehouse


class WarehouseScopeService:
    """Service layer for user warehouse scope and access checks."""

    @staticmethod
    def is_admin_user(user):
        return bool(user and user.is_authenticated and user.is_superuser)

    @classmethod
    def get_accessible_warehouses(cls, user):
        if cls.is_admin_user(user):
            return Warehouse.objects.filter(is_active=True).order_by('name')
        if not user or not user.is_authenticated:
            return Warehouse.objects.none()
        return Warehouse.objects.filter(
            is_active=True,
            user_accesses__user=user,
            user_accesses__is_active=True,
        ).distinct().order_by('name')

    @classmethod
    def get_accessible_warehouse_ids(cls, user):
        return list(
            cls.get_accessible_warehouses(user).values_list('id', flat=True)
        )

    @classmethod
    def get_default_warehouse(cls, user):
        if cls.is_admin_user(user):
            return Warehouse.objects.filter(is_default=True, is_active=True).first()
        if not user or not user.is_authenticated:
            return None

        default_access = UserWarehouseAccess.objects.select_related('warehouse').filter(
            user=user,
            is_active=True,
            is_default=True,
            warehouse__is_active=True,
        ).first()
        if default_access:
            return default_access.warehouse

        return cls.get_accessible_warehouses(user).first()

    @classmethod
    def can_access_warehouse(cls, user, warehouse):
        if warehouse is None:
            return False
        if cls.is_admin_user(user):
            return bool(warehouse.is_active)
        if not user or not user.is_authenticated:
            return False
        return UserWarehouseAccess.objects.filter(
            user=user,
            warehouse=warehouse,
            is_active=True,
            warehouse__is_active=True,
        ).exists()

    @classmethod
    def resolve_warehouse_selection(cls, user, warehouse_param, include_all_option=True):
        """
        Resolve warehouse filter selection for views.

        Returns a dict:
            - warehouses: queryset of accessible warehouses
            - selected_warehouse: selected Warehouse object or None
            - selected_warehouse_value: selected value for template select input
            - warehouse_ids: None for admin-all, [] for no-access, or [id,...]
            - scope_label: human readable scope text
        """
        warehouses = cls.get_accessible_warehouses(user)
        selected_warehouse = None
        selected_warehouse_value = 'all' if include_all_option else ''

        normalized_param = (warehouse_param or '').strip()
        if normalized_param and not (include_all_option and normalized_param == 'all'):
            try:
                selected_warehouse = warehouses.get(id=int(normalized_param))
                selected_warehouse_value = str(selected_warehouse.id)
            except (ValueError, TypeError, Warehouse.DoesNotExist):
                selected_warehouse = None
                selected_warehouse_value = 'all' if include_all_option else ''

        if selected_warehouse is not None:
            warehouse_ids = [selected_warehouse.id]
            scope_label = selected_warehouse.name
        elif cls.is_admin_user(user):
            warehouse_ids = None
            scope_label = '全部仓库'
        else:
            warehouse_ids = list(warehouses.values_list('id', flat=True))
            scope_label = '全部可见仓库'

        return {
            'warehouses': warehouses,
            'selected_warehouse': selected_warehouse,
            'selected_warehouse_value': selected_warehouse_value,
            'warehouse_ids': warehouse_ids,
            'scope_label': scope_label,
        }

    @classmethod
    def filter_sales_queryset(cls, user, queryset):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(user)
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def ensure_sale_access(cls, user, sale):
        if cls.is_admin_user(user):
            return
        if sale.warehouse_id is None:
            raise AuthorizationError('销售单未绑定仓库，无法确认访问权限', code='warehouse_scope_denied')
        if not cls.can_access_warehouse(user, sale.warehouse):
            raise AuthorizationError('您无权访问该仓库销售数据', code='warehouse_scope_denied')

    @classmethod
    def filter_warehouse_inventory_queryset(cls, user, queryset):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(user)
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def filter_inventory_transactions_queryset(cls, user, queryset):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(user)
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def filter_inventory_checks_queryset(cls, user, queryset):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(user)
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def ensure_inventory_check_access(cls, user, inventory_check):
        if cls.is_admin_user(user):
            return
        if inventory_check.warehouse_id is None:
            raise AuthorizationError('盘点单未绑定仓库，无法确认访问权限', code='warehouse_scope_denied')
        if not cls.can_access_warehouse(user, inventory_check.warehouse):
            raise AuthorizationError('您无权访问该仓库盘点数据', code='warehouse_scope_denied')
