# 从各模块导入所有模型

# 产品相关模型
from .product import Product, Category, Color, Size, Store, ProductImage, ProductBatch, Supplier

# 库存相关模型
from .inventory import (
    InventoryTransaction,
    check_inventory, update_inventory, StockAlert
)

# 仓库相关模型
from .warehouse import Warehouse, WarehouseInventory, UserWarehouseAccess

# 库存盘点相关模型
from .inventory_check import InventoryCheck, InventoryCheckItem

# 会员相关模型
# from .member import Member, MemberLevel, RechargeRecord, MemberTransaction

# 销售相关模型
from .sales import Sale, SaleItem

# 通用模型
from .common import OperationLog, SystemConfig

# 导出所有模型，使它们可以通过inventory.models访问
__all__ = [
    # 产品模型
    'Product', 'Category', 'Color', 'Size', 'Store', 'ProductImage', 'ProductBatch', 'Supplier',
    
    # 库存模型
    'InventoryTransaction', 'check_inventory',
    'update_inventory', 'StockAlert',
    
    # 仓库模型
    'Warehouse', 'WarehouseInventory', 'UserWarehouseAccess',
    
    # 库存盘点模型
    'InventoryCheck', 'InventoryCheckItem',
    
    # 销售模型
    'Sale', 'SaleItem',
    
    # 通用模型
    'OperationLog', 'SystemConfig',
] 
