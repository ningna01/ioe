"""
业务服务层包
提供各种业务逻辑处理服务
"""

# 导入所有服务模块，使它们可以通过inventory.services访问
from . import product_service
# from . import member_service
from . import export_service
from . import report_service
from . import inventory_check_service
from . import backup_service
from . import inventory_service
from . import warehouse_scope_service
from . import warehouse_inventory_service
from . import stock_scope_service

# 导出服务模块，方便直接访问
__all__ = [
    'product_service',
    # 'member_service',  # 已移除会员服务模块
    'export_service',
    'report_service',
    'inventory_check_service',
    'backup_service',
    'inventory_service',
    'warehouse_scope_service',
    'warehouse_inventory_service',
    'stock_scope_service',
]
