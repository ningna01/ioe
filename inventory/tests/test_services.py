from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

from inventory.models import (
    Category,
    Product,
    Inventory,
    InventoryTransaction,
    InventoryCheck,
    InventoryCheckItem,
    Sale,
    SaleItem,
)
from inventory.services.inventory_service import InventoryService
from inventory.services.inventory_check_service import InventoryCheckService
from inventory.services.report_service import ReportService
from inventory.exceptions import InsufficientStockError, InventoryValidationError

class InventoryServiceTest(TestCase):
    """测试库存服务"""
    
    def setUp(self):
        # 创建测试用户
        self.user = User.objects.create_user(username='testuser', password='12345')
        
        # 创建测试分类
        self.category = Category.objects.create(name='测试分类')
        
        # 创建测试商品
        self.product = Product.objects.create(
            barcode='1234567890',
            name='测试商品',
            category=self.category,
            price=Decimal('10.00'),
            cost=Decimal('5.00')
        )
        
        # 创建库存记录
        self.inventory = Inventory.objects.create(
            product=self.product,
            quantity=100,
            warning_level=10
        )
    
    def test_check_stock(self):
        """测试库存检查"""
        # 库存充足
        self.assertTrue(InventoryService.check_stock(self.product, 50))
        
        # 库存刚好
        self.assertTrue(InventoryService.check_stock(self.product, 100))
        
        # 库存不足
        self.assertFalse(InventoryService.check_stock(self.product, 150))
    
    def test_update_stock_in(self):
        """测试入库操作"""
        inventory, transaction = InventoryService.update_stock(
            product=self.product,
            quantity=50,
            transaction_type='IN',
            operator=self.user,
            notes='测试入库'
        )
        
        # 验证库存更新
        self.assertEqual(inventory.quantity, 150)  # 100 + 50
        
        # 验证交易记录
        self.assertEqual(transaction.product, self.product)
        self.assertEqual(transaction.transaction_type, 'IN')
        self.assertEqual(transaction.quantity, 50)
        self.assertEqual(transaction.operator, self.user)
        self.assertEqual(transaction.notes, '测试入库')
    
    def test_update_stock_out(self):
        """测试出库操作"""
        inventory, transaction = InventoryService.update_stock(
            product=self.product,
            quantity=30,
            transaction_type='OUT',
            operator=self.user,
            notes='测试出库'
        )
        
        # 验证库存更新
        self.assertEqual(inventory.quantity, 70)  # 100 - 30
        
        # 验证交易记录
        self.assertEqual(transaction.product, self.product)
        self.assertEqual(transaction.transaction_type, 'OUT')
        self.assertEqual(transaction.quantity, 30)
        self.assertEqual(transaction.operator, self.user)
        self.assertEqual(transaction.notes, '测试出库')
    
    def test_update_stock_out_insufficient(self):
        """测试库存不足时的出库操作"""
        with self.assertRaises(InsufficientStockError):
            InventoryService.update_stock(
                product=self.product,
                quantity=150,  # 大于当前库存
                transaction_type='OUT',
                operator=self.user,
                notes='测试出库失败'
            )
    
    def test_update_stock_adjust(self):
        """测试库存调整操作"""
        inventory, transaction = InventoryService.update_stock(
            product=self.product,
            quantity=80,
            transaction_type='ADJUST',
            operator=self.user,
            notes='测试调整'
        )
        
        # 验证库存更新
        self.assertEqual(inventory.quantity, 80)  # 直接设置为80
        
        # 验证交易记录
        self.assertEqual(transaction.product, self.product)
        self.assertEqual(transaction.transaction_type, 'ADJUST')
        self.assertEqual(transaction.quantity, 80)
        self.assertEqual(transaction.operator, self.user)
        self.assertEqual(transaction.notes, '测试调整')
    
    def test_get_low_stock_items(self):
        """测试获取库存预警商品"""
        # 初始状态下没有预警商品
        low_stock_items = InventoryService.get_low_stock_items()
        self.assertEqual(low_stock_items.count(), 0)
        
        # 将库存调整到预警水平
        self.inventory.quantity = 10  # 等于预警水平
        self.inventory.save()
        
        low_stock_items = InventoryService.get_low_stock_items()
        self.assertEqual(low_stock_items.count(), 1)
        self.assertEqual(low_stock_items.first(), self.inventory)
        
        # 将库存调整到低于预警水平
        self.inventory.quantity = 5  # 低于预警水平
        self.inventory.save()
        
        low_stock_items = InventoryService.get_low_stock_items()
        self.assertEqual(low_stock_items.count(), 1)
        self.assertEqual(low_stock_items.first(), self.inventory)

class InventoryCheckServiceTest(TestCase):
    """测试库存盘点服务"""
    
    def setUp(self):
        # 创建测试用户
        self.user = User.objects.create_user(username='testuser', password='12345')
        
        # 创建测试分类
        self.category = Category.objects.create(name='测试分类')
        
        # 创建测试商品
        self.product1 = Product.objects.create(
            barcode='1234567890',
            name='测试商品1',
            category=self.category,
            price=Decimal('10.00'),
            cost=Decimal('5.00')
        )
        
        self.product2 = Product.objects.create(
            barcode='0987654321',
            name='测试商品2',
            category=self.category,
            price=Decimal('20.00'),
            cost=Decimal('10.00')
        )
        
        # 创建库存记录
        self.inventory1 = Inventory.objects.create(
            product=self.product1,
            quantity=100,
            warning_level=10
        )
        
        self.inventory2 = Inventory.objects.create(
            product=self.product2,
            quantity=50,
            warning_level=5
        )
    
    def test_create_inventory_check(self):
        """测试创建库存盘点"""
        inventory_check = InventoryCheckService.create_inventory_check(
            name='测试盘点',
            description='测试盘点描述',
            user=self.user
        )
        
        # 验证盘点单创建
        self.assertEqual(inventory_check.name, '测试盘点')
        self.assertEqual(inventory_check.description, '测试盘点描述')
        self.assertEqual(inventory_check.status, 'draft')
        self.assertEqual(inventory_check.created_by, self.user)
        
        # 验证盘点项创建
        self.assertEqual(inventory_check.items.count(), 2)  # 两个商品
        
        item1 = inventory_check.items.get(product=self.product1)
        self.assertEqual(item1.system_quantity, 100)
        self.assertIsNone(item1.actual_quantity)
        
        item2 = inventory_check.items.get(product=self.product2)
        self.assertEqual(item2.system_quantity, 50)
        self.assertIsNone(item2.actual_quantity)
    
    def test_start_inventory_check(self):
        """测试开始库存盘点"""
        inventory_check = InventoryCheckService.create_inventory_check(
            name='测试盘点',
            description='测试盘点描述',
            user=self.user
        )
        
        # 开始盘点
        updated_check = InventoryCheckService.start_inventory_check(
            inventory_check=inventory_check,
            user=self.user
        )
        
        # 验证状态更新
        self.assertEqual(updated_check.status, 'in_progress')
    
    def test_record_check_item(self):
        """测试记录盘点项"""
        inventory_check = InventoryCheckService.create_inventory_check(
            name='测试盘点',
            description='测试盘点描述',
            user=self.user
        )
        
        # 开始盘点
        inventory_check = InventoryCheckService.start_inventory_check(
            inventory_check=inventory_check,
            user=self.user
        )
        
        # 获取盘点项
        check_item = inventory_check.items.get(product=self.product1)
        
        # 记录盘点结果
        updated_item = InventoryCheckService.record_check_item(
            inventory_check_item=check_item,
            actual_quantity=90,  # 实际数量与系统数量不同
            user=self.user,
            notes='测试盘点记录'
        )
        
        # 验证盘点项更新
        self.assertEqual(updated_item.actual_quantity, 90)
        self.assertEqual(updated_item.notes, '测试盘点记录')
        self.assertEqual(updated_item.checked_by, self.user)
        self.assertIsNotNone(updated_item.checked_at)


class ReportServiceTurnoverTest(TestCase):
    """库存周转率方案 A 测试"""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.category = Category.objects.create(name='测试分类')
        self.product = Product.objects.create(
            barcode='1234567890',
            name='测试商品',
            category=self.category,
            price=Decimal('10.00'),
            cost=Decimal('5.00')
        )
        self.inventory = Inventory.objects.create(
            product=self.product,
            quantity=50,
            warning_level=10
        )

    def test_turnover_scheme_a_with_in_out_transactions(self):
        """方案 A：有 IN/OUT 交易时，基于交易推算期初库存"""
        # 当前库存 50，期间 IN=20, OUT=30 -> 期初 = 50 - 20 + 30 = 60
        InventoryTransaction.objects.create(
            product=self.product,
            transaction_type='IN',
            quantity=20,
            operator=self.user,
            notes='测试入库',
            warehouse=None
        )
        InventoryTransaction.objects.create(
            product=self.product,
            transaction_type='OUT',
            quantity=30,
            operator=self.user,
            notes='测试出库',
            warehouse=None
        )
        # 创建销售记录（销量 10）
        sale = Sale.objects.create(
            total_amount=Decimal('100'),
            discount_amount=Decimal('0'),
            final_amount=Decimal('100'),
            payment_method='cash',
            operator=self.user
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=10,
            price=Decimal('10'),
            actual_price=Decimal('10'),
            subtotal=Decimal('100'),
            sale_type='retail'
        )

        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        result = ReportService.get_inventory_turnover_rate(start_date, end_date)

        data = next((r for r in result if r['product_id'] == self.product.id), None)
        self.assertIsNotNone(data)
        # 期初=60, 期末=50, 平均=(60+50)/2=55
        self.assertAlmostEqual(data['avg_stock'], 55.0, places=2)
        self.assertEqual(data['sold_quantity'], 10)
        self.assertEqual(data['current_stock'], 50)

    def test_turnover_fallback_with_adjust_in_period(self):
        """有 ADJUST 时降级为近似公式：avg = (current + sold) / 2"""
        InventoryTransaction.objects.create(
            product=self.product,
            transaction_type='ADJUST',
            quantity=50,
            operator=self.user,
            notes='盘点调整',
            warehouse=None
        )
        sale = Sale.objects.create(
            total_amount=Decimal('50'),
            discount_amount=Decimal('0'),
            final_amount=Decimal('50'),
            payment_method='cash',
            operator=self.user
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=5,
            price=Decimal('10'),
            actual_price=Decimal('10'),
            subtotal=Decimal('50'),
            sale_type='retail'
        )

        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        result = ReportService.get_inventory_turnover_rate(start_date, end_date)

        data = next((r for r in result if r['product_id'] == self.product.id), None)
        self.assertIsNotNone(data)
        # 降级公式: avg = (50 + 5) / 2 = 27.5
        self.assertAlmostEqual(data['avg_stock'], 27.5, places=2)

    def test_turnover_no_transactions_uses_current(self):
        """无交易时 beginning=current，avg_stock=current"""
        sale = Sale.objects.create(
            total_amount=Decimal('100'),
            discount_amount=Decimal('0'),
            final_amount=Decimal('100'),
            payment_method='cash',
            operator=self.user
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=5,
            price=Decimal('10'),
            actual_price=Decimal('10'),
            subtotal=Decimal('100'),
            sale_type='retail'
        )

        start_date = timezone.now().date() - timedelta(days=30)
        end_date = timezone.now().date()
        result = ReportService.get_inventory_turnover_rate(start_date, end_date)

        data = next((r for r in result if r['product_id'] == self.product.id), None)
        self.assertIsNotNone(data)
        # 无 IN/OUT，beginning=current=50, avg=(50+50)/2=50
        self.assertAlmostEqual(data['avg_stock'], 50.0, places=2)