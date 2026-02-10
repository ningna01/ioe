from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Permission, Group
from decimal import Decimal

from inventory.models import (
    Category, 
    Product, 
    Inventory, 
    InventoryTransaction,
    # Member,
    # MemberLevel,
    Sale,
    SaleItem
)

class ViewTestCase(TestCase):
    """视图测试的基类"""
    
    def setUp(self):
        # 创建测试用户
        self.user = User.objects.create_user(
            username='testuser', 
            password='12345',
            email='test@example.com'
        )
        
        # 创建测试管理员
        self.admin = User.objects.create_user(
            username='admin', 
            password='admin123',
            email='admin@example.com',
            is_staff=True
        )
        
        # 创建客户端
        self.client = Client()
        
        # 创建测试分类
        self.category = Category.objects.create(
            name='测试分类',
            description='测试分类描述'
        )
        
        # 创建测试商品
        self.product = Product.objects.create(
            barcode='1234567890',
            name='测试商品',
            category=self.category,
            description='测试商品描述',
            price=Decimal('10.00'),
            cost=Decimal('5.00')
        )
        
        # 创建库存记录
        self.inventory = Inventory.objects.create(
            product=self.product,
            quantity=100,
            warning_level=10
        )
        
        # # 创建会员等级
        # self.member_level = MemberLevel.objects.create(
        #     name='普通会员',
        #     discount=95,  # 95%
        #     points_threshold=0,
        #     color='#FF5733'
        # )
        # 
        # # 创建会员
        # self.member = Member.objects.create(
        #     name='测试会员',
        #     phone='13800138000',
        #     level=self.member_level,
        #     balance=Decimal('100.00'),
        #     points=0
        # )

class ProductViewTest(ViewTestCase):
    """测试商品相关视图"""
    
    def test_product_list_view(self):
        """测试商品列表视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问商品列表页面
        response = self.client.get(reverse('product_list'))
        
        # 验证响应
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/product_list.html')
        self.assertContains(response, '测试商品')
        
    def test_product_create_view(self):
        """测试创建商品视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问创建商品页面
        response = self.client.get(reverse('product_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/product_form.html')
        
        # 提交创建商品表单
        product_data = {
            'barcode': '9876543210',
            'name': '新测试商品',
            'category': self.category.id,
            'description': '新测试商品描述',
            'price': '15.00',
            'cost': '7.50'
        }
        
        response = self.client.post(reverse('product_create'), product_data)
        
        # 验证重定向
        self.assertRedirects(response, reverse('product_list'))
        
        # 验证商品创建
        self.assertTrue(Product.objects.filter(barcode='9876543210').exists())

class InventoryViewTest(ViewTestCase):
    """测试库存相关视图"""
    
    def test_inventory_list_view(self):
        """测试库存列表视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问库存列表页面
        response = self.client.get(reverse('inventory_list'))
        
        # 验证响应
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/inventory_list.html')
        self.assertContains(response, '测试商品')
        
    def test_inventory_transaction_create_view(self):
        """测试创建库存交易视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问创建库存交易页面
        response = self.client.get(reverse('inventory_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/inventory_form.html')
        
        # 提交创建库存交易表单
        transaction_data = {
            'product': self.product.id,
            'quantity': '50',
            'notes': '测试入库'
        }
        
        response = self.client.post(reverse('inventory_create'), transaction_data)
        
        # 验证重定向
        self.assertRedirects(response, reverse('inventory_list'))
        
        # 验证库存交易创建和库存更新
        self.assertTrue(InventoryTransaction.objects.filter(product=self.product, quantity=50).exists())
        
        # 刷新库存对象
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 150)  # 100 + 50

class SaleViewTest(ViewTestCase):
    """测试销售相关视图"""

    def _create_sale_with_item(self, quantity=5):
        sale = Sale.objects.create(
            operator=self.user,
            total_amount=Decimal('0.00'),
            discount_amount=Decimal('0.00'),
            final_amount=Decimal('0.00'),
            payment_method='cash'
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            price=self.product.price,
            actual_price=self.product.price,
            subtotal=self.product.price * quantity,
            sale_type='retail'
        )
        return sale, item
    
    def test_sale_list_view(self):
        """测试销售列表视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问销售列表页面
        response = self.client.get(reverse('sale_list'))
        
        # 验证响应
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/sale_list.html')
        
    def test_sale_create_view(self):
        """测试创建销售视图"""
        # 登录
        self.client.login(username='testuser', password='12345')
        
        # 访问创建销售页面
        response = self.client.get(reverse('sale_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/sale_form.html')
        
        # 提交创建销售表单
        sale_data = {
            'payment_method': 'cash',
            'products[0][id]': self.product.id,
            'products[0][quantity]': 5,
            'products[0][price]': str(self.product.price),
            'products[0][sale_type]': 'retail',
            'total_amount': '50.00',
            'discount_amount': '0.00',
            'final_amount': '50.00'
        }
        
        response = self.client.post(reverse('sale_create'), sale_data)
        
        # 验证创建销售单
        sale = Sale.objects.first()
        self.assertIsNotNone(sale)
        
        # 验证重定向
        self.assertEqual(response.status_code, 302)

    def test_sale_detail_repairs_inconsistent_amount(self):
        """测试销售详情页面会修复销售金额不一致问题"""
        self.client.login(username='testuser', password='12345')
        sale, _ = self._create_sale_with_item(quantity=5)

        Sale.objects.filter(id=sale.id).update(
            total_amount=Decimal('0.00'),
            discount_amount=Decimal('1.00'),
            final_amount=Decimal('0.00')
        )

        response = self.client.get(reverse('sale_detail', args=[sale.id]))
        self.assertEqual(response.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.total_amount, Decimal('50.00'))
        self.assertEqual(sale.discount_amount, Decimal('1.00'))
        self.assertEqual(sale.final_amount, Decimal('49.00'))

    def test_sale_cancel_page_available(self):
        """测试销售取消页面可正常打开"""
        self.client.login(username='testuser', password='12345')
        sale, _ = self._create_sale_with_item(quantity=2)

        response = self.client.get(reverse('sale_cancel', args=[sale.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'inventory/sale_cancel.html')

    def test_sale_soft_delete_hides_from_default_list_and_preserves_inventory(self):
        """测试销售单软删除后默认隐藏且库存不变"""
        self.client.login(username='testuser', password='12345')
        sale, _ = self._create_sale_with_item(quantity=3)
        transaction_count_before_delete = InventoryTransaction.objects.filter(product=self.product).count()

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 97)

        first_response = self.client.post(reverse('sale_cancel', args=[sale.id]), {'reason': '测试删除'})
        self.assertRedirects(first_response, reverse('sale_list'))

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'DELETED')

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 97)

        second_response = self.client.post(reverse('sale_cancel', args=[sale.id]), {'reason': '重复删除'})
        self.assertRedirects(second_response, reverse('sale_detail', args=[sale.id]))

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 97)

        detail_response = self.client.get(reverse('sale_detail', args=[sale.id]))
        self.assertContains(detail_response, '已删除')

        default_list_response = self.client.get(reverse('sale_list'))
        self.assertNotContains(default_list_response, f'订单 #{sale.id}')
        self.assertEqual(default_list_response.context['today_sales'], Decimal('30.00'))

        deleted_list_response = self.client.get(reverse('sale_list'), {'sale_type': 'deleted'})
        self.assertContains(deleted_list_response, f'订单 #{sale.id}')

        transaction_count_after_delete = InventoryTransaction.objects.filter(product=self.product).count()
        self.assertEqual(transaction_count_after_delete, transaction_count_before_delete)

    # def test_member_sale_view(self):
    #     """测试会员销售视图"""
    #     # 登录
    #     self.client.login(username='testuser', password='12345')
    #     
    #     # 访问创建销售页面
    #     response = self.client.get(reverse('sale_create'))
    #     self.assertEqual(response.status_code, 200)
    #     
    #     # 提交创建销售表单（带会员）
    #     sale_data = {
    #         'payment_method': 'cash',
    #         'member': self.member.id
    #     }
    #     
    #     response = self.client.post(reverse('sale_create'), sale_data)
    #     
    #     # 验证创建销售单
    #     self.assertTrue(Sale.objects.filter(member=self.member).exists())
    #     sale = Sale.objects.filter(member=self.member).first()
    #     
    #     # 验证重定向到销售项创建页面
    #     self.assertRedirects(response, reverse('sale_item_create', args=[sale.id]))
    #     
    #     # 验证会员积分增加
    #     self.member.refresh_from_db()
    #     self.assertGreater(self.member.points, 0)
