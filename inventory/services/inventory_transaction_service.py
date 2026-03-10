"""
Inventory transaction service.
Provides reversible void workflow for stock-in transactions.
"""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from inventory.exceptions import AuthorizationError
from inventory.models import (
    InventoryTransaction,
    UserWarehouseAccess,
    update_inventory,
)
from inventory.services.payable_service import PayableService
from inventory.services.warehouse_scope_service import WarehouseScopeService
from inventory.utils.logging import record_operation_log


class InventoryTransactionService:
    """Service for inventory transaction business workflows."""

    @staticmethod
    def _build_reversal_notes(*, source_transaction, reason):
        note_parts = [
            'source=inventory_void_stock_in',
            'intent=void_stock_in',
            f'original_transaction_id={source_transaction.id}',
            f'warehouse_id={source_transaction.warehouse_id}',
            f'product_id={source_transaction.product_id}',
            f'quantity={source_transaction.quantity}',
        ]
        cleaned_reason = (reason or '').strip()
        if cleaned_reason:
            note_parts.append(f'reason={cleaned_reason}')
        return ' | '.join(note_parts)

    @classmethod
    def _create_void_operation_log(
        cls,
        *,
        operator,
        source_transaction,
        reversal_transaction,
        reason,
        payable_summary,
    ):
        cleaned_reason = (reason or '').strip() or '未填写'
        details = (
            f'作废入库交易: 原交易ID={source_transaction.id}; 冲销交易ID={reversal_transaction.id}; '
            f'商品={source_transaction.product.name}; 仓库={source_transaction.warehouse.name if source_transaction.warehouse else "未绑定仓库"}; '
            f'数量={source_transaction.quantity}; 原因={cleaned_reason}; '
            f'应付联动: open_soft_deleted={len(payable_summary["soft_deleted_order_ids"])}, '
            f'settled_offset_created={len(payable_summary["offset_created_order_ids"])}, '
            f'skipped={len(payable_summary["skipped_order_ids"])}'
        )
        record_operation_log(
            operator=operator,
            operation_type='INVENTORY',
            details=details,
            related_object_id=source_transaction.id,
            related_content_type=ContentType.objects.get_for_model(InventoryTransaction),
        )

    @classmethod
    def void_stock_in_transaction(cls, *, transaction_id, operator, reason=''):
        """
        Void a stock-in transaction and reverse stock movement.

        Returns:
            (success: bool, message: str, payload: dict|None)
        """
        cleaned_reason = (reason or '').strip()
        try:
            with transaction.atomic():
                source_transaction = InventoryTransaction.objects.select_for_update().select_related(
                    'product',
                    'warehouse',
                ).filter(id=transaction_id).first()
                if source_transaction is None:
                    return False, '未找到指定的入库记录', None

                if source_transaction.transaction_type != 'IN':
                    return False, '仅支持作废入库（IN）记录', None

                if source_transaction.reversal_of_id is not None:
                    return False, '冲销交易不支持再次作废', None

                if source_transaction.is_voided:
                    return False, '该入库记录已作废，请勿重复操作', {
                        'source_transaction': source_transaction,
                    }

                if source_transaction.warehouse is None:
                    return False, '该入库记录未绑定仓库，无法作废', None

                WarehouseScopeService.ensure_warehouse_permission(
                    user=operator,
                    warehouse=source_transaction.warehouse,
                    required_permission=UserWarehouseAccess.PERMISSION_STOCK_ADJUST,
                    error_message='您无权作废该仓库入库记录',
                )

                success, inventory_obj, stock_result = update_inventory(
                    product=source_transaction.product,
                    warehouse=source_transaction.warehouse,
                    quantity=source_transaction.quantity,
                    transaction_type='OUT',
                    operator=operator,
                    notes=cls._build_reversal_notes(
                        source_transaction=source_transaction,
                        reason=cleaned_reason,
                    ),
                )
                if not success:
                    return False, f'库存回滚失败: {stock_result}', None

                reversal_transaction = stock_result
                reversal_transaction.reversal_of = source_transaction
                reversal_transaction.save(update_fields=['reversal_of'])

                source_transaction.is_voided = True
                source_transaction.voided_at = timezone.now()
                source_transaction.voided_by = operator
                source_transaction.void_reason = cleaned_reason
                source_transaction.save(update_fields=[
                    'is_voided',
                    'voided_at',
                    'voided_by',
                    'void_reason',
                ])

                payable_summary = PayableService.handle_inventory_void_payables(
                    source_transaction_id=source_transaction.id,
                    operator=operator,
                    reason=cleaned_reason,
                )

                cls._create_void_operation_log(
                    operator=operator,
                    source_transaction=source_transaction,
                    reversal_transaction=reversal_transaction,
                    reason=cleaned_reason,
                    payable_summary=payable_summary,
                )

                return True, '入库记录作废成功，库存已自动回滚', {
                    'source_transaction': source_transaction,
                    'reversal_transaction': reversal_transaction,
                    'inventory': inventory_obj,
                    'payable_summary': payable_summary,
                }
        except AuthorizationError as exc:
            return False, exc.message, None
        except Exception as exc:
            return False, f'作废失败: {exc}', None
