from django.db import models
from django.contrib.auth.models import User

from .warehouse import Warehouse


class DebtOrder(models.Model):
    """欠款订单（独立于销售订单）。"""

    STATUS_CHOICES = [
        ('OPEN', '未结清'),
        ('SETTLED', '已结清'),
        ('CANCELLED', '已取消'),
    ]

    payee_name = models.CharField(max_length=120, verbose_name='收款人')
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='应付款')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN', verbose_name='状态')
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='debt_orders',
        verbose_name='关联仓库',
    )
    remark = models.TextField(blank=True, verbose_name='备注')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='debt_orders_created',
        verbose_name='创建人',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        verbose_name = '欠款订单'
        verbose_name_plural = '欠款订单'
        ordering = ['-created_at']

    def __str__(self):
        return f'欠款单 #{self.id} - {self.payee_name} - {self.amount}'
