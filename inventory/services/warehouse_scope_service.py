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
    def _normalize_permission_bit(cls, required_permission):
        if required_permission in (None, ''):
            return None
        if isinstance(required_permission, int):
            return required_permission if required_permission > 0 else None
        if isinstance(required_permission, str):
            normalized = required_permission.strip().lower()
            return UserWarehouseAccess.PERMISSION_CODE_TO_BIT.get(normalized)
        return None

    @classmethod
    def _get_user_access_queryset(cls, user):
        if not user or not user.is_authenticated:
            return UserWarehouseAccess.objects.none()
        return UserWarehouseAccess.objects.filter(
            is_active=True,
            user=user,
            warehouse__is_active=True,
        ).select_related('warehouse')

    @classmethod
    def has_any_warehouse_permission(cls, user, required_permission=None):
        """Check whether user has at least one active warehouse grant for permission."""
        if cls.is_admin_user(user):
            return True
        if not user or not user.is_authenticated:
            return False

        permission_bit = cls._normalize_permission_bit(required_permission)
        access_queryset = cls._get_user_access_queryset(user).values_list('permission_bits', flat=True)
        if permission_bit is None:
            return access_queryset.exists()
        return any((permission_bits or 0) & permission_bit for permission_bits in access_queryset)

    @classmethod
    def ensure_any_warehouse_permission(
        cls,
        user,
        required_permission=None,
        error_message=None,
        code='warehouse_scope_denied',
    ):
        """
        Ensure user has at least one active warehouse grant for permission.
        Useful for module-level guards that are not bound to a single warehouse.
        """
        if cls.is_admin_user(user):
            return
        if not user or not user.is_authenticated:
            raise AuthorizationError(
                error_message or '您尚未登录，无法访问仓库数据',
                code=code,
            )
        if cls.has_any_warehouse_permission(user, required_permission=required_permission):
            return
        raise AuthorizationError(
            error_message or '您无权执行该操作',
            code='warehouse_action_denied' if required_permission else code,
        )

    @classmethod
    def get_accessible_warehouses(cls, user, required_permission=None):
        if cls.is_admin_user(user):
            return Warehouse.objects.filter(is_active=True).order_by('name')
        if not user or not user.is_authenticated:
            return Warehouse.objects.none()

        permission_bit = cls._normalize_permission_bit(required_permission)
        if permission_bit is None:
            return Warehouse.objects.filter(
                is_active=True,
                user_accesses__user=user,
                user_accesses__is_active=True,
            ).distinct().order_by('name')

        access_queryset = cls._get_user_access_queryset(user).only('warehouse_id', 'permission_bits')
        allowed_ids = [
            access.warehouse_id
            for access in access_queryset
            if access.has_permission(permission_bit)
        ]
        if not allowed_ids:
            return Warehouse.objects.none()
        return Warehouse.objects.filter(is_active=True, id__in=allowed_ids).order_by('name')

    @classmethod
    def get_accessible_warehouse_ids(cls, user, required_permission=None):
        return list(
            cls.get_accessible_warehouses(
                user,
                required_permission=required_permission,
            ).values_list('id', flat=True)
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
    def get_user_warehouse_access(cls, user, warehouse):
        if warehouse is None:
            return None
        if not user or not user.is_authenticated or cls.is_admin_user(user):
            return None
        return cls._get_user_access_queryset(user).filter(warehouse=warehouse).first()

    @classmethod
    def can_access_warehouse(cls, user, warehouse, required_permission=None):
        if warehouse is None:
            return False
        if cls.is_admin_user(user):
            return bool(warehouse.is_active)
        if not user or not user.is_authenticated:
            return False

        access = cls.get_user_warehouse_access(user, warehouse)
        if access is None:
            return False

        permission_bit = cls._normalize_permission_bit(required_permission)
        if permission_bit is None:
            return True
        return access.has_permission(permission_bit)

    @classmethod
    def ensure_warehouse_permission(
        cls,
        user,
        warehouse,
        required_permission=None,
        error_message=None,
        code='warehouse_scope_denied',
    ):
        if warehouse is None:
            raise AuthorizationError(
                error_message or '目标仓库不存在或未指定',
                code=code,
            )
        if not warehouse.is_active:
            raise AuthorizationError(
                error_message or '目标仓库已禁用，无法访问',
                code=code,
            )
        if cls.is_admin_user(user):
            return
        if not user or not user.is_authenticated:
            raise AuthorizationError(
                error_message or '您尚未登录，无法访问仓库数据',
                code=code,
            )

        access = cls.get_user_warehouse_access(user, warehouse)
        if access is None:
            raise AuthorizationError(
                error_message or '您无权访问该仓库数据',
                code=code,
            )

        permission_bit = cls._normalize_permission_bit(required_permission)
        if permission_bit is None:
            return
        if not access.has_permission(permission_bit):
            raise AuthorizationError(
                error_message or '您无权在该仓库执行该操作',
                code='warehouse_action_denied',
            )

    @classmethod
    def ensure_warehouse_access(
        cls,
        user,
        warehouse,
        error_message=None,
        code='warehouse_scope_denied',
    ):
        cls.ensure_warehouse_permission(
            user=user,
            warehouse=warehouse,
            required_permission=None,
            error_message=error_message,
            code=code,
        )

    @classmethod
    def resolve_warehouse_selection(
        cls,
        user,
        warehouse_param,
        include_all_option=True,
        required_permission=None,
    ):
        """
        Resolve warehouse filter selection for views.

        Returns a dict:
            - warehouses: queryset of accessible warehouses
            - selected_warehouse: selected Warehouse object or None
            - selected_warehouse_value: selected value for template select input
            - warehouse_ids: None for admin-all, [] for no-access, or [id,...]
            - scope_label: human readable scope text
        """
        warehouses = cls.get_accessible_warehouses(
            user,
            required_permission=required_permission,
        )
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
    def filter_sales_queryset(cls, user, queryset, required_permission=None):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(
            user,
            required_permission=required_permission,
        )
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def ensure_sale_access(cls, user, sale):
        if sale.warehouse_id is None:
            raise AuthorizationError('销售单未绑定仓库，无法确认访问权限', code='warehouse_scope_denied')
        cls.ensure_warehouse_permission(
            user=user,
            warehouse=sale.warehouse,
            required_permission=UserWarehouseAccess.PERMISSION_SALE,
            error_message='您无权访问该仓库销售数据',
            code='warehouse_scope_denied',
        )

    @classmethod
    def filter_warehouse_inventory_queryset(cls, user, queryset, required_permission=None):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(
            user,
            required_permission=required_permission,
        )
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def filter_inventory_transactions_queryset(cls, user, queryset, required_permission=None):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(
            user,
            required_permission=required_permission,
        )
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def filter_inventory_checks_queryset(cls, user, queryset, required_permission=None):
        if cls.is_admin_user(user):
            return queryset
        warehouse_ids = cls.get_accessible_warehouse_ids(
            user,
            required_permission=required_permission,
        )
        if not warehouse_ids:
            return queryset.none()
        return queryset.filter(warehouse_id__in=warehouse_ids)

    @classmethod
    def ensure_inventory_check_access(cls, user, inventory_check):
        if inventory_check.warehouse_id is None:
            raise AuthorizationError('盘点单未绑定仓库，无法确认访问权限', code='warehouse_scope_denied')
        cls.ensure_warehouse_permission(
            user=user,
            warehouse=inventory_check.warehouse,
            required_permission=UserWarehouseAccess.PERMISSION_INVENTORY_CHECK,
            error_message='您无权访问该仓库盘点数据',
            code='warehouse_scope_denied',
        )
