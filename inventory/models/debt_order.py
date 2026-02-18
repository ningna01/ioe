from django.db import models
from django.contrib.auth.models import User

from .product import Supplier
from .warehouse import Warehouse


class DebtOrder(models.Model):
    """应付款订单。"""

    STATUS_CHOICES = [
        ('OPEN', '未结清'),
        ('SETTLED', '已结清'),
        ('CANCELLED', '已取消'),
    ]

    SETTLEMENT_MODE_CHOICES = [
        ('CASH_SETTLED', '现金结清'),
        ('CREDIT_PAYABLE', '挂账应付'),
    ]

    SOURCE_TYPE_CHOICES = [
        ('MANUAL', '手工创建'),
        ('PRODUCT_CREATE', '商品建档'),
        ('PRODUCT_IMPORT', '商品导入'),
        ('INVENTORY_IN', '手工入库'),
        ('INVENTORY_IMPORT', '批量入库'),
    ]

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name='debt_orders',
        verbose_name='供货商',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='应付款')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN', verbose_name='状态')
    settlement_mode = models.CharField(
        max_length=20,
        choices=SETTLEMENT_MODE_CHOICES,
        default='CREDIT_PAYABLE',
        verbose_name='结算方式',
    )
    source_type = models.CharField(
        max_length=30,
        choices=SOURCE_TYPE_CHOICES,
        default='MANUAL',
        verbose_name='来源类型',
    )
    source_id = models.PositiveIntegerField(null=True, blank=True, verbose_name='来源对象ID')
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='debt_orders',
        verbose_name='关联仓库',
    )
    remark = models.TextField(blank=True, verbose_name='备注')
    is_deleted = models.BooleanField(default=False, verbose_name='是否删除')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='删除时间')
    deleted_reason = models.TextField(blank=True, verbose_name='删除原因')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='debt_orders_created',
        verbose_name='创建人',
    )
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='debt_orders_deleted',
        verbose_name='删除人',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        verbose_name = '应付款订单'
        verbose_name_plural = '应付款订单'
        ordering = ['-created_at']

    def __str__(self):
        return f'应付款单 #{self.id} - {self.supplier.name} - {self.amount}'
