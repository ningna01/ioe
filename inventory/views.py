"""
Legacy compatibility facade for split view modules.
"""

from inventory.views.core import index, reports_index
from inventory.views.inventory import (
    inventory_adjust,
    inventory_in,
    inventory_list,
    inventory_out,
    inventory_transaction_create,
    inventory_transaction_list,
    inventory_update_warning_level,
)
from inventory.views.product import (
    product_by_barcode,
    product_create,
    product_delete,
    product_detail,
    product_export,
    product_import,
    product_list,
    product_update,
)
from inventory.views.sales import (
    sale_cancel,
    sale_complete,
    sale_create,
    sale_delete_item,
    sale_detail,
    sale_item_create,
    sale_list,
)

__all__ = [
    'index',
    'reports_index',
    'inventory_adjust',
    'inventory_in',
    'inventory_list',
    'inventory_out',
    'inventory_transaction_create',
    'inventory_transaction_list',
    'inventory_update_warning_level',
    'product_by_barcode',
    'product_create',
    'product_delete',
    'product_detail',
    'product_export',
    'product_import',
    'product_list',
    'product_update',
    'sale_cancel',
    'sale_complete',
    'sale_create',
    'sale_delete_item',
    'sale_detail',
    'sale_item_create',
    'sale_list',
]
