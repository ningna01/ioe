from decimal import Decimal
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Category,
    InventoryTransaction,
    Product,
    Sale,
    SaleItem,
    Warehouse,
    WarehouseInventory,
    update_inventory,
)


class Command(BaseCommand):
    help = '为当前仓库库存模型生成示例数据（WarehouseInventory-only）'

    def add_arguments(self, parser):
        parser.add_argument('--categories', type=int, default=8, help='生成的商品分类数量')
        parser.add_argument('--products', type=int, default=60, help='生成的商品数量')
        parser.add_argument('--sales', type=int, default=40, help='生成的销售记录数量')
        parser.add_argument('--clean', action='store_true', help='是否在生成前清除现有数据')

    def handle(self, *args, **options):
        num_categories = options['categories']
        num_products = options['products']
        num_sales = options['sales']
        clean = options['clean']

        if clean:
            self.clean_database()
            self.stdout.write(self.style.SUCCESS('已清除现有示例数据'))

        with transaction.atomic():
            admin_user, _ = User.objects.get_or_create(
                username='admin',
                defaults={
                    'is_staff': True,
                    'is_superuser': True,
                    'email': 'admin@example.com',
                }
            )
            if not admin_user.has_usable_password():
                admin_user.set_password('admin')
                admin_user.save(update_fields=['password'])

            warehouse = self.ensure_default_warehouse()
            categories = self.create_categories(num_categories)
            products = self.create_products(categories, num_products, admin_user, warehouse)
            sales = self.create_sales(products, num_sales, admin_user, warehouse)

            self.stdout.write(self.style.SUCCESS(f'已创建分类: {len(categories)}'))
            self.stdout.write(self.style.SUCCESS(f'已创建商品: {len(products)}'))
            self.stdout.write(self.style.SUCCESS(f'已创建销售记录: {len(sales)}'))
            self.stdout.write(self.style.SUCCESS('示例数据生成完成'))

    def clean_database(self):
        SaleItem.objects.all().delete()
        Sale.objects.all().delete()
        InventoryTransaction.objects.all().delete()
        WarehouseInventory.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()

    def ensure_default_warehouse(self):
        warehouse = Warehouse.objects.filter(is_default=True, is_active=True).first()
        if warehouse:
            return warehouse
        warehouse = Warehouse.objects.filter(is_active=True).first()
        if warehouse:
            if not warehouse.is_default:
                warehouse.is_default = True
                warehouse.save(update_fields=['is_default'])
            return warehouse
        return Warehouse.objects.create(
            name='默认仓库',
            code='DEFAULT-WH',
            is_active=True,
            is_default=True,
        )

    def create_categories(self, num_categories):
        category_names = [
            '服装', '鞋帽', '箱包', '配饰', '家居', '清洁', '食品', '数码',
            '办公', '玩具', '母婴', '运动',
        ]
        categories = []
        for i in range(min(num_categories, len(category_names))):
            category, _ = Category.objects.get_or_create(
                name=category_names[i],
                defaults={'description': f'{category_names[i]}分类', 'is_active': True},
            )
            categories.append(category)
        return categories

    def create_products(self, categories, num_products, operator, warehouse):
        products = []
        for i in range(num_products):
            category = random.choice(categories)
            price = Decimal(str(round(random.uniform(20, 500), 2)))
            cost = (price * Decimal(str(round(random.uniform(0.45, 0.8), 2)))).quantize(Decimal('0.01'))

            product = Product.objects.create(
                barcode=f'69{random.randint(1000000000, 9999999999)}',
                name=f'{category.name}商品{i + 1}',
                category=category,
                price=price,
                cost=cost,
                wholesale_price=(price * Decimal('0.85')).quantize(Decimal('0.01')),
                specification=random.choice(['标准', '加大', '礼盒', '经济装']),
                manufacturer=random.choice(['供应商A', '供应商B', '供应商C']),
                color=random.choice(['黑色', '白色', '蓝色', '红色']),
                size=random.choice(['S', 'M', 'L', 'XL', '160L']),
                is_active=True,
            )

            warning_level = random.randint(5, 15)
            WarehouseInventory.objects.get_or_create(
                product=product,
                warehouse=warehouse,
                defaults={
                    'quantity': 0,
                    'warning_level': warning_level,
                },
            )

            initial_quantity = random.randint(20, 120)
            if initial_quantity > 0:
                success, _, stock_result = update_inventory(
                    product=product,
                    warehouse=warehouse,
                    quantity=initial_quantity,
                    transaction_type='IN',
                    operator=operator,
                    notes='样例数据初始化库存',
                )
                if not success:
                    raise ValueError(f'初始化库存失败: {stock_result}')

            products.append(product)
        return products

    def create_sales(self, products, num_sales, operator, warehouse):
        sales = []
        for _ in range(num_sales):
            sale = Sale.objects.create(
                total_amount=Decimal('0.00'),
                discount_amount=Decimal('0.00'),
                final_amount=Decimal('0.00'),
                payment_method=random.choice(['cash', 'wechat', 'alipay', 'card']),
                operator=operator,
                warehouse=warehouse,
                status='COMPLETED',
                remark='系统生成销售记录',
            )

            selected_products = random.sample(products, k=min(random.randint(1, 4), len(products)))
            total_amount = Decimal('0.00')

            for product in selected_products:
                quantity = random.randint(1, 3)
                success, _, stock_result = update_inventory(
                    product=product,
                    warehouse=warehouse,
                    quantity=quantity,
                    transaction_type='OUT',
                    operator=operator,
                    notes=f'样例销售扣减: sale_id={sale.id}',
                )
                if not success:
                    continue

                sale_type = random.choice(['retail', 'wholesale'])
                unit_price = product.price if sale_type == 'retail' else (product.wholesale_price or product.price)
                subtotal = (unit_price * quantity).quantize(Decimal('0.01'))

                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=quantity,
                    price=unit_price,
                    actual_price=unit_price,
                    subtotal=subtotal,
                    sale_type=sale_type,
                )
                total_amount += subtotal

            sale.total_amount = total_amount
            sale.discount_amount = Decimal('0.00')
            sale.final_amount = total_amount
            sale.created_at = timezone.now()
            sale.save(update_fields=['total_amount', 'discount_amount', 'final_amount', 'created_at'])

            sales.append(sale)
        return sales
