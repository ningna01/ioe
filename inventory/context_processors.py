"""
Template context processors for permission-aware navigation rendering.
"""
from inventory.models import UserWarehouseAccess
from inventory.services.user_mode_service import is_sales_focus_user


def _aggregate_active_permission_bits(user):
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


def navigation_permissions(request):
    nav_permissions = {
        'show_product': False,
        'show_category': False,
        'show_inventory': False,
        'show_inventory_check': False,
        'show_sales': False,
        'show_reports': False,
        'show_warehouse': False,
        'sales_focus_mode': False,
    }

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'nav_permissions': nav_permissions}

    if user.is_superuser:
        for key in nav_permissions:
            if key != 'sales_focus_mode':
                nav_permissions[key] = True
        return {'nav_permissions': nav_permissions}

    aggregated_bits = _aggregate_active_permission_bits(user)
    has_bit = lambda bit: bool(aggregated_bits & bit)

    nav_permissions['show_inventory'] = has_bit(UserWarehouseAccess.PERMISSION_VIEW)
    nav_permissions['show_sales'] = has_bit(UserWarehouseAccess.PERMISSION_SALE)
    nav_permissions['show_inventory_check'] = has_bit(UserWarehouseAccess.PERMISSION_INVENTORY_CHECK)
    nav_permissions['show_product'] = has_bit(UserWarehouseAccess.PERMISSION_PRODUCT_MANAGE)
    nav_permissions['show_category'] = nav_permissions['show_product']
    nav_permissions['show_warehouse'] = nav_permissions['show_product']
    nav_permissions['show_reports'] = (
        has_bit(UserWarehouseAccess.PERMISSION_REPORT_VIEW)
        and user.has_perm('inventory.view_reports')
    )
    nav_permissions['sales_focus_mode'] = is_sales_focus_user(user)

    return {'nav_permissions': nav_permissions}
