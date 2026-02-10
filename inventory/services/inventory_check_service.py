"""
Inventory check services.
"""
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Product,
    InventoryCheck,
    InventoryCheckItem,
    Warehouse,
    WarehouseInventory,
    update_inventory,
)
from inventory.exceptions import InventoryValidationError
from inventory.utils.logging import log_exception, log_action


class InventoryCheckService:
    """Service for inventory checking operations."""

    @staticmethod
    def _resolve_check_warehouse(inventory_check):
        warehouse = inventory_check.warehouse
        if warehouse is None:
            raise InventoryValidationError("盘点单未绑定仓库，无法执行该操作")
        if not warehouse.is_active:
            raise InventoryValidationError(f"盘点仓库已禁用: {warehouse.name}")
        return warehouse

    @staticmethod
    def _build_adjust_notes(inventory_check, item):
        return (
            f"source=inventory_check_approve | intent=inventory_check_adjust | "
            f"check_id={inventory_check.id} | warehouse_id={inventory_check.warehouse_id} | "
            f"product_id={item.product_id} | system={item.system_quantity} | "
            f"actual={item.actual_quantity} | delta={item.difference:+d}"
        )

    @staticmethod
    @log_exception
    def create_inventory_check(name, description, user, category=None, warehouse=None):
        """
        Create a new inventory check.

        Args:
            name: The name of the inventory check.
            description: Description of the check.
            user: The user creating the check.
            category: Optional category filter for the check.
            warehouse: Optional warehouse scope; fallback to active default warehouse.

        Returns:
            InventoryCheck: The created inventory check.
        """
        with transaction.atomic():
            if warehouse is None:
                warehouse = Warehouse.objects.filter(is_default=True, is_active=True).first()
            if warehouse is None:
                raise InventoryValidationError("未找到可用的默认仓库，无法创建盘点单")
            if not warehouse.is_active:
                raise InventoryValidationError(f"盘点仓库已禁用: {warehouse.name}")

            inventory_check = InventoryCheck.objects.create(
                name=name,
                description=description,
                status='draft',
                created_by=user,
                warehouse=warehouse,
            )

            products_query = Product.objects.filter(is_active=True)
            if category:
                products_query = products_query.filter(category=category)

            items = []
            for product in products_query:
                warehouse_inventory = WarehouseInventory.objects.filter(
                    product=product,
                    warehouse=warehouse,
                ).only('quantity').first()
                system_quantity = warehouse_inventory.quantity if warehouse_inventory else 0
                items.append(
                    InventoryCheckItem(
                        inventory_check=inventory_check,
                        product=product,
                        system_quantity=system_quantity,
                    )
                )

            if items:
                InventoryCheckItem.objects.bulk_create(items)

            log_action(
                user=user,
                operation_type='INVENTORY_CHECK',
                details=(
                    f"创建库存盘点: check_id={inventory_check.id}; name={inventory_check.name}; "
                    f"warehouse={warehouse.name}; source=inventory_check_create"
                ),
                related_object=inventory_check,
            )

            return inventory_check

    @staticmethod
    @log_exception
    def start_inventory_check(inventory_check, user):
        """
        Start an inventory check.

        Args:
            inventory_check: The inventory check to start.
            user: The user starting the check.

        Returns:
            InventoryCheck: The updated inventory check.
        """
        warehouse = InventoryCheckService._resolve_check_warehouse(inventory_check)
        if inventory_check.status != 'draft':
            raise InventoryValidationError("只有草稿状态的盘点单可以开始盘点")

        inventory_check.status = 'in_progress'
        inventory_check.save(update_fields=['status'])

        log_action(
            user=user,
            operation_type='INVENTORY_CHECK',
            details=(
                f"开始库存盘点: check_id={inventory_check.id}; name={inventory_check.name}; "
                f"warehouse={warehouse.name}; source=inventory_check_start"
            ),
            related_object=inventory_check,
        )

        return inventory_check

    @staticmethod
    @log_exception
    @transaction.atomic
    def record_check_item(inventory_check_item, actual_quantity, user, notes=""):
        """
        Record the actual quantity for an inventory check item.

        Args:
            inventory_check_item: The item to update.
            actual_quantity: The actual quantity counted.
            user: The user recording the check.
            notes: Optional notes about the check.

        Returns:
            InventoryCheckItem: The updated inventory check item.
        """
        inventory_check = inventory_check_item.inventory_check
        warehouse = InventoryCheckService._resolve_check_warehouse(inventory_check)
        if inventory_check.status != 'in_progress':
            raise InventoryValidationError("只有进行中的盘点单可以记录盘点结果")
        if actual_quantity < 0:
            raise InventoryValidationError("实际数量不能为负数")

        inventory_check_item.actual_quantity = actual_quantity
        inventory_check_item.notes = notes
        inventory_check_item.checked_by = user
        inventory_check_item.checked_at = timezone.now()
        inventory_check_item.save()

        cleaned_notes = (notes or '').strip()
        log_action(
            user=user,
            operation_type='INVENTORY_CHECK',
            details=(
                f"记录盘点项: check_id={inventory_check.id}; warehouse={warehouse.name}; "
                f"product={inventory_check_item.product.name}; actual={actual_quantity}; "
                f"difference={inventory_check_item.difference:+d}; source=inventory_check_item_update"
                + (f"; note={cleaned_notes}" if cleaned_notes else "")
            ),
            related_object=inventory_check_item,
        )

        return inventory_check_item

    @staticmethod
    @log_exception
    @transaction.atomic
    def complete_inventory_check(inventory_check, user):
        """
        Complete an inventory check.

        Args:
            inventory_check: The inventory check to complete.
            user: The user completing the check.

        Returns:
            InventoryCheck: The updated inventory check.
        """
        warehouse = InventoryCheckService._resolve_check_warehouse(inventory_check)
        if inventory_check.status not in ('in_progress', 'approved'):
            raise InventoryValidationError("只有进行中或已审核的盘点单可以标记为完成")

        if inventory_check.status == 'in_progress':
            unchecked_items = inventory_check.items.filter(actual_quantity__isnull=True).count()
            if unchecked_items > 0:
                raise InventoryValidationError(f"还有 {unchecked_items} 个商品未盘点完成")

        inventory_check.status = 'completed'
        inventory_check.completed_at = timezone.now()
        inventory_check.save(update_fields=['status', 'completed_at'])

        log_action(
            user=user,
            operation_type='INVENTORY_CHECK',
            details=(
                f"完成库存盘点: check_id={inventory_check.id}; name={inventory_check.name}; "
                f"warehouse={warehouse.name}; source=inventory_check_complete"
            ),
            related_object=inventory_check,
        )

        return inventory_check

    @staticmethod
    @log_exception
    @transaction.atomic
    def approve_inventory_check(inventory_check, user, adjust_inventory=False):
        """
        Approve an inventory check and optionally adjust inventory.

        Args:
            inventory_check: The inventory check to approve.
            user: The user approving the check.
            adjust_inventory: Whether to adjust stock levels to match actual counts.

        Returns:
            InventoryCheck: The updated inventory check.
        """
        warehouse = InventoryCheckService._resolve_check_warehouse(inventory_check)
        if inventory_check.status != 'completed':
            raise InventoryValidationError("只有已完成的盘点单可以审核")

        if adjust_inventory:
            items_to_adjust = inventory_check.items.filter(
                difference__isnull=False
            ).exclude(difference=0).select_related('product')

            for item in items_to_adjust:
                notes = InventoryCheckService._build_adjust_notes(inventory_check, item)
                success, inventory_obj, stock_result = update_inventory(
                    product=item.product,
                    warehouse=warehouse,
                    quantity=item.difference,
                    transaction_type='ADJUST',
                    operator=user,
                    notes=notes,
                )
                if not success:
                    raise InventoryValidationError(
                        f"盘点库存调整失败: 商品={item.product.name}; 仓库={warehouse.name}; 原因={stock_result}"
                    )

                transaction_obj = stock_result
                log_action(
                    user=user,
                    operation_type='INVENTORY_CHECK',
                    details=(
                        f"盘点库存调整: check_id={inventory_check.id}; warehouse={warehouse.name}; "
                        f"product={item.product.name}; system={item.system_quantity}; "
                        f"actual={item.actual_quantity}; delta={item.difference:+d}; "
                        f"current={inventory_obj.quantity}; tx_id={transaction_obj.id}; "
                        f"source=inventory_check_approve"
                    ),
                    related_object=transaction_obj,
                )

        inventory_check.status = 'approved'
        inventory_check.approved_by = user
        inventory_check.approved_at = timezone.now()
        inventory_check.save(update_fields=['status', 'approved_by', 'approved_at'])

        log_action(
            user=user,
            operation_type='INVENTORY_CHECK',
            details=(
                f"审核库存盘点: check_id={inventory_check.id}; name={inventory_check.name}; "
                f"warehouse={warehouse.name}; adjust={'yes' if adjust_inventory else 'no'}; "
                f"source=inventory_check_approve"
            ),
            related_object=inventory_check,
        )

        return inventory_check

    @staticmethod
    @log_exception
    def cancel_inventory_check(inventory_check, user):
        """
        Cancel an inventory check.

        Args:
            inventory_check: The inventory check to cancel.
            user: The user cancelling the check.

        Returns:
            InventoryCheck: The updated inventory check.
        """
        warehouse = InventoryCheckService._resolve_check_warehouse(inventory_check)
        if inventory_check.status in ('approved', 'cancelled'):
            raise InventoryValidationError("已审核或已取消的盘点单不能取消")

        inventory_check.status = 'cancelled'
        inventory_check.save(update_fields=['status'])

        log_action(
            user=user,
            operation_type='INVENTORY_CHECK',
            details=(
                f"取消库存盘点: check_id={inventory_check.id}; name={inventory_check.name}; "
                f"warehouse={warehouse.name}; source=inventory_check_cancel"
            ),
            related_object=inventory_check,
        )

        return inventory_check

    @staticmethod
    @log_exception
    def get_inventory_check_summary(inventory_check):
        """
        Get a summary of the inventory check.

        Args:
            inventory_check: The inventory check to summarize.

        Returns:
            dict: Summary information.
        """
        items = inventory_check.items.all()
        total_items = items.count()
        checked_items = items.filter(actual_quantity__isnull=False).count()
        items_with_discrepancy = items.filter(difference__isnull=False).exclude(difference=0).count()
        system_value = sum(item.system_quantity * item.product.cost for item in items)
        actual_value = sum(
            (item.actual_quantity or 0) * item.product.cost
            for item in items.filter(actual_quantity__isnull=False)
        )

        return {
            'total_items': total_items,
            'checked_items': checked_items,
            'pending_items': total_items - checked_items,
            'items_with_discrepancy': items_with_discrepancy,
            'system_value': system_value,
            'actual_value': actual_value,
            'value_difference': actual_value - system_value,
        }
