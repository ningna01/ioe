"""
Warehouse-level inventory write service.

This service is the single write entrance for inventory mutations and provides
transaction + row-level locking guarantees for concurrent updates.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from inventory.models import InventoryTransaction, WarehouseInventory


class WarehouseInventoryService:
    """Unified inventory read/write service with warehouse-only semantics."""

    VALID_TRANSACTION_TYPES = {'IN', 'OUT', 'ADJUST'}

    @classmethod
    def check_stock(cls, product, quantity, warehouse=None):
        """
        Check if stock is sufficient.

        Args:
            product: Product instance.
            quantity: Required stock quantity (positive integer).
            warehouse: Warehouse instance.
        """
        if quantity is None:
            return False
        if quantity <= 0:
            return True
        if warehouse is None:
            return False

        inventory = WarehouseInventory.objects.filter(product=product, warehouse=warehouse).first()
        if inventory is None:
            return False
        return inventory.quantity >= quantity

    @classmethod
    def update_stock(cls, product, quantity, transaction_type, operator, warehouse=None, notes=''):
        """
        Update stock with transaction/row-lock protection and record transaction log.

        Quantity follows current compatibility semantics:
        - IN: positive quantity means increase.
        - OUT: negative quantity means decrease (positive also accepted and normalized).
        - ADJUST: quantity is treated as delta (can be positive or negative).
        """
        cls._validate_inputs(transaction_type=transaction_type, operator=operator, warehouse=warehouse)
        normalized_quantity = cls._normalize_quantity(quantity, transaction_type)

        with transaction.atomic():
            inventory = cls._get_or_create_locked_inventory(product=product, warehouse=warehouse)
            old_quantity = inventory.quantity
            new_quantity = old_quantity + normalized_quantity

            if new_quantity < 0:
                raise ValidationError(
                    f"仓库库存不足: {product.name} ({warehouse.name}), 当前库存: {old_quantity}, 请求数量: {abs(normalized_quantity)}"
                )

            inventory.quantity = new_quantity
            inventory.save(update_fields=['quantity'])

            stock_transaction = InventoryTransaction.objects.create(
                product=product,
                warehouse=warehouse,
                transaction_type=transaction_type,
                quantity=abs(normalized_quantity),
                operator=operator,
                notes=notes
            )

        return inventory, stock_transaction

    @classmethod
    def _validate_inputs(cls, transaction_type, operator, warehouse):
        if transaction_type not in cls.VALID_TRANSACTION_TYPES:
            raise ValidationError("交易类型无效")
        if not isinstance(operator, User):
            raise ValidationError("操作员必须是有效的用户")
        if warehouse is None:
            raise ValidationError("库存操作必须指定仓库")

    @staticmethod
    def _normalize_quantity(quantity, transaction_type):
        if quantity is None:
            raise ValidationError("库存变更数量不能为空")
        if quantity == 0:
            return 0
        if transaction_type == 'IN':
            return abs(quantity)
        if transaction_type == 'OUT':
            return -abs(quantity)
        return quantity

    @staticmethod
    def _get_or_create_locked_inventory(product, warehouse):
        locked_qs = WarehouseInventory.objects.select_for_update()
        try:
            return locked_qs.get(product=product, warehouse=warehouse)
        except WarehouseInventory.DoesNotExist:
            try:
                return WarehouseInventory.objects.create(
                    product=product,
                    warehouse=warehouse,
                    quantity=0,
                    warning_level=10,
                )
            except IntegrityError:
                # Another transaction created the same row concurrently.
                return locked_qs.get(product=product, warehouse=warehouse)
