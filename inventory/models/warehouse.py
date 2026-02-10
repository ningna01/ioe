"""
仓库管理模型
用于支持多仓库入库功能，记录仓库信息和每个商品在各个仓库的库存
"""

from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator, RegexValidator
from django.utils.text import capfirst


class Warehouse(models.Model):
    """
    仓库模型
    用于管理仓库的基础信息和状态
    """
    # 仓库编码验证器：只能包含字母、数字和下划线
    code_validator = RegexValidator(
        regex=r'^[a-zA-Z0-9_]+$',
        message='仓库编码只能包含字母、数字和下划线'
    )

    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name=capfirst('仓库名称'),
        help_text='仓库的唯一名称'
    )
    
    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name=capfirst('仓库编码'),
        validators=[MinLengthValidator(1), code_validator],
        help_text='仓库的唯一编码，用于程序内部标识'
    )
    
    address = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=capfirst('地址'),
        help_text='仓库的详细地址'
    )
    
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name=capfirst('联系电话'),
        help_text='仓库的联系电话'
    )
    
    contact_person = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=capfirst('联系人'),
        help_text='仓库的联系人姓名'
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name=capfirst('是否启用'),
        help_text='控制仓库是否参与库存业务逻辑'
    )
    
    is_default = models.BooleanField(
        default=False,
        verbose_name=capfirst('是否默认仓库'),
        help_text='标识系统中的默认仓库'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=capfirst('创建时间'),
        help_text='自动记录仓库的创建时间'
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=capfirst('更新时间'),
        help_text='自动记录仓库的最后更新时间'
    )

    class Meta:
        verbose_name = capfirst('仓库')
        verbose_name_plural = capfirst('仓库')
        ordering = ['name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """重写save方法，确保只有一个默认仓库"""
        if self.is_default:
            # 将其他仓库的默认标识设为False
            Warehouse.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def inventory_count(self):
        """获取仓库中的商品种类数量"""
        return self.inventories.filter(quantity__gt=0).count()

    @property
    def total_quantity(self):
        """获取仓库中的商品总数量"""
        return self.inventories.aggregate(total=models.Sum('quantity'))['total'] or 0


class UserWarehouseAccess(models.Model):
    """
    用户仓库授权关系模型
    用于记录用户可访问仓库、默认仓库与权限位
    """
    # 权限位定义
    PERMISSION_VIEW = 1
    PERMISSION_SALE = 2
    PERMISSION_STOCK_IN = 4
    PERMISSION_STOCK_OUT = 8
    PERMISSION_INVENTORY_CHECK = 16
    DEFAULT_PERMISSION_BITS = (
        PERMISSION_VIEW
        | PERMISSION_SALE
        | PERMISSION_STOCK_IN
        | PERMISSION_STOCK_OUT
        | PERMISSION_INVENTORY_CHECK
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='warehouse_accesses',
        verbose_name=capfirst('用户')
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name='user_accesses',
        verbose_name=capfirst('仓库')
    )

    is_default = models.BooleanField(
        default=False,
        verbose_name=capfirst('默认仓库'),
        help_text='标识该用户的默认仓库'
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=capfirst('是否启用'),
        help_text='禁用后该授权关系不参与权限判断'
    )

    permission_bits = models.PositiveIntegerField(
        default=DEFAULT_PERMISSION_BITS,
        verbose_name=capfirst('权限位'),
        help_text='按位存储仓库权限，例如查看、销售、入库、出库、盘点'
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=capfirst('创建时间')
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=capfirst('更新时间')
    )

    class Meta:
        verbose_name = capfirst('用户仓库授权')
        verbose_name_plural = capfirst('用户仓库授权')
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'warehouse'],
                name='uniq_user_warehouse_access'
            ),
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(is_default=True, is_active=True),
                name='uniq_active_default_warehouse_per_user'
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['warehouse', 'is_active']),
        ]

    def __str__(self):
        default_label = ' (默认)' if self.is_default else ''
        return f'{self.user.username} -> {self.warehouse.name}{default_label}'

    def save(self, *args, **kwargs):
        # 保证同一用户只存在一个激活的默认仓
        if self.is_default and self.is_active:
            UserWarehouseAccess.objects.filter(
                user=self.user,
                is_default=True,
                is_active=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def has_permission(self, permission_bit):
        """判断是否拥有指定权限位"""
        return bool(self.permission_bits & permission_bit)


class WarehouseInventory(models.Model):
    """
    仓库库存模型
    用于记录每个商品在各个仓库的库存情况
    """
    product = models.ForeignKey(
        'Product',
        on_delete=models.PROTECT,
        related_name='warehouse_inventories',
        verbose_name=capfirst('商品')
    )
    
    warehouse = models.ForeignKey(
        'Warehouse',
        on_delete=models.PROTECT,
        related_name='inventories',
        verbose_name=capfirst('仓库')
    )
    
    quantity = models.IntegerField(
        default=0,
        verbose_name=capfirst('库存数量'),
        help_text='当前仓库中该商品的实际库存数量'
    )
    
    warning_level = models.IntegerField(
        default=10,
        verbose_name=capfirst('预警数量'),
        help_text='库存预警阈值，当数量低于此值时触发预警'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=capfirst('创建时间'),
        help_text='自动记录库存记录的创建时间'
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=capfirst('更新时间'),
        help_text='自动记录库存的最后更新时间'
    )

    class Meta:
        verbose_name = capfirst('仓库库存')
        verbose_name_plural = capfirst('仓库库存')
        unique_together = ['product', 'warehouse']
        indexes = [
            models.Index(fields=['product', 'warehouse']),
            models.Index(fields=['warehouse', 'quantity']),
        ]

    def __str__(self):
        return f'{self.product.name} - {self.warehouse.name} - {self.quantity}'

    @property
    def is_low_stock(self):
        """判断当前库存是否低于预警水平"""
        return self.quantity <= self.warning_level

    def clean(self):
        """数据验证"""
        from django.core.exceptions import ValidationError
        if self.quantity < 0:
            raise ValidationError({'quantity': '库存数量不能为负数'})
        if self.warning_level < 0:
            raise ValidationError({'warning_level': '预警数量不能为负数'})

    def save(self, *args, **kwargs):
        """保存前进行验证"""
        self.clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create(cls, product, warehouse):
        """获取或创建仓库库存记录"""
        obj, created = cls.objects.get_or_create(
            product=product,
            warehouse=warehouse,
            defaults={'quantity': 0, 'warning_level': 10}
        )
        return obj
