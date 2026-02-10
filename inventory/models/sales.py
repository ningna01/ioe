from django.db import models
from django.contrib.auth.models import User

from .product import Product
from .warehouse import Warehouse
# from .member import Member


class Sale(models.Model):
    """
    销售单模型
    """
    STATUS_CHOICES = [
        ('COMPLETED', '已完成'),
        ('DELETED', '已删除'),
    ]

    PAYMENT_METHODS = [
        ('cash', '现金'),
        ('wechat', '微信'),
        ('alipay', '支付宝'),
        ('card', '银行卡'),
        ('balance', '账户余额'),
        ('mixed', '混合支付'),
        ('other', '其他')
    ]
    
    # 注意：会员系统已移除，此字段保留历史数据但不再使用
    # 将ForeignKey改为IntegerField以保留member_id历史数据
    member_id = models.IntegerField(null=True, blank=True, verbose_name='会员ID', default=None)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='总金额')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='折扣金额')
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='实付金额')
    points_earned = models.IntegerField(default=0, verbose_name='获得积分')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='cash', verbose_name='支付方式')
    balance_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name='余额支付')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    operator = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='操作员')
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sales',
        verbose_name='仓库'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='COMPLETED', verbose_name='状态')
    remark = models.TextField(blank=True, verbose_name='备注')

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())

    def get_sale_type(self):
        """获取销售单的销售方式（销售单只有一种销售方式）"""
        first_item = self.items.first()
        if first_item:
            return first_item.sale_type
        return None

    def get_sale_type_display(self):
        """获取销售方式的显示文本"""
        sale_type = self.get_sale_type()
        if sale_type == 'retail':
            return '零售'
        elif sale_type == 'wholesale':
            return '批发'
        return ''

    def update_total_amount(self):
        self.total_amount = sum(item.subtotal for item in self.items.all())
        return self.total_amount
    
    def save(self, *args, **kwargs):
        # 确保total_amount不为None且为有效值
        if self.total_amount is None:
            self.total_amount = 0
        
        if self.total_amount < self.discount_amount:
            self.discount_amount = self.total_amount
        
        self.final_amount = self.total_amount - self.discount_amount
        super().save(*args, **kwargs)
        
    class Meta:
        verbose_name = '销售单'
        verbose_name_plural = '销售单'

    def __str__(self):
        return f'销售单 #{self.id} - {self.created_at.strftime("%Y-%m-%d %H:%M")}'


class SaleItem(models.Model):
    SALE_TYPE_CHOICES = [
        ('retail', '零售'),
        ('wholesale', '批发'),
    ]
    
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name='items', verbose_name='销售单')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='商品')
    quantity = models.IntegerField(verbose_name='数量')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='标准售价')
    actual_price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='实际售价')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='小计')
    sale_type = models.CharField(max_length=20, choices=SALE_TYPE_CHOICES, default='retail', verbose_name='销售方式')
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.quantity <= 0:
            raise ValidationError('数量必须大于0')
    
    def save(self, *args, **kwargs):
        sync_sale_totals = kwargs.pop('sync_sale_totals', True)

        # 如果实际价格没有设置，默认使用标准价格
        if self.actual_price is None:
            self.actual_price = self.price
            
        # 计算小计
        self.subtotal = self.quantity * self.actual_price
        
        # 保存SaleItem
        super().save(*args, **kwargs)

        # 销售明细模型不再隐式写库存；库存写入口统一由服务层显式触发。
        if sync_sale_totals:
            self.sale.update_total_amount()
            self.sale.save()
    
    class Meta:
        verbose_name = '销售明细'
        verbose_name_plural = '销售明细'
    
    def __str__(self):
        return f'{self.product.name} x {self.quantity}' 
