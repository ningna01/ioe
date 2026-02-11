from django.db import models
from django.contrib.auth.models import User

from .product import Product
from .warehouse import Warehouse


class InventoryTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('IN', '入库'),
        ('OUT', '出库'),
        ('ADJUST', '调整'),
    ]

    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='商品')
    warehouse = models.ForeignKey(
        Warehouse, 
        on_delete=models.PROTECT, 
        verbose_name='仓库',
        null=True, 
        blank=True,
        related_name='transactions'
    )
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, verbose_name='交易类型')
    quantity = models.IntegerField(verbose_name='数量')
    operator = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name='操作员')
    notes = models.TextField(blank=True, verbose_name='备注')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    
    class Meta:
        verbose_name = '库存交易记录'
        verbose_name_plural = '库存交易记录'
    
    def __str__(self):
        warehouse_name = self.warehouse.name if self.warehouse else '未绑定仓库'
        return f'{self.product.name} - {self.get_transaction_type_display()} - {self.quantity} ({warehouse_name})'


# 添加库存工具函数
def check_inventory(product, quantity, warehouse=None):
    """检查库存是否足够"""
    from inventory.services.warehouse_inventory_service import WarehouseInventoryService

    return WarehouseInventoryService.check_stock(
        product=product,
        quantity=quantity,
        warehouse=warehouse,
    )


def update_inventory(product, quantity, transaction_type, operator, warehouse=None, notes=''):
    """更新库存并记录交易"""
    from inventory.services.warehouse_inventory_service import WarehouseInventoryService

    try:
        inventory, transaction = WarehouseInventoryService.update_stock(
            product=product,
            quantity=quantity,
            transaction_type=transaction_type,
            operator=operator,
            warehouse=warehouse,
            notes=notes,
        )
        return True, inventory, transaction
    except Exception as e:
        return False, None, str(e)


class StockAlert(models.Model):
    """库存预警模型"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name='商品')
    alert_type = models.CharField(
        max_length=20, 
        choices=[
            ('low_stock', '低库存'),
            ('expiring', '即将过期'),
            ('overstock', '库存过量')
        ],
        verbose_name='预警类型'
    )
    is_active = models.BooleanField(default=True, verbose_name='是否激活')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name='解决时间')
    
    class Meta:
        verbose_name = '库存预警'
        verbose_name_plural = '库存预警'
        
    def __str__(self):
        return f'{self.product.name} - {self.get_alert_type_display()}' 
