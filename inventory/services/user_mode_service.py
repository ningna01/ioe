"""
用户体验模式服务。
集中定义“销售员专注模式”等入口行为判定，避免逻辑散落。
"""
from inventory.models import UserWarehouseAccess


def aggregate_active_permission_bits(user):
    """聚合用户所有激活仓库授权的权限位。"""
    if not user or not user.is_authenticated:
        return 0

    permission_bits = 0
    for bits in UserWarehouseAccess.objects.filter(
        user=user,
        is_active=True,
        warehouse__is_active=True,
    ).values_list('permission_bits', flat=True):
        permission_bits |= int(bits or 0)
    return permission_bits


def is_sales_focus_user(user):
    """
    销售员专注模式判定：
    1) 非超级管理员；
    2) 至少具备 sale + view；
    3) 不具备其他仓库权限位（入库/出库/盘点/调整/商品管理/报表）。
    """
    if not user or not user.is_authenticated or user.is_superuser:
        return False

    bits = aggregate_active_permission_bits(user)

    required_bits = (
        UserWarehouseAccess.PERMISSION_VIEW
        | UserWarehouseAccess.PERMISSION_SALE
    )
    disallowed_bits = (
        UserWarehouseAccess.PERMISSION_STOCK_IN
        | UserWarehouseAccess.PERMISSION_STOCK_OUT
        | UserWarehouseAccess.PERMISSION_INVENTORY_CHECK
        | UserWarehouseAccess.PERMISSION_STOCK_ADJUST
        | UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE
        | UserWarehouseAccess.PERMISSION_REPORT_VIEW
    )

    has_required = (bits & required_bits) == required_bits
    has_disallowed = bool(bits & disallowed_bits)
    return has_required and not has_disallowed
