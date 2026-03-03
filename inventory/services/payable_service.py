from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from inventory.models import DebtOrder, OperationLog


class PayableService:
    """应付款业务服务。"""

    INVENTORY_PAYABLE_SOURCE_TYPES = ('INVENTORY_IN', 'INVENTORY_IMPORT')

    @staticmethod
    def create_payable_order(
        *,
        supplier,
        amount,
        created_by,
        warehouse=None,
        source_type='MANUAL',
        source_id=None,
        settlement_mode='CREDIT_PAYABLE',
        remark='',
    ):
        if supplier is None:
            raise ValueError('创建应付款单失败：供货商不能为空')

        amount_decimal = Decimal(str(amount or '0'))
        if amount_decimal <= 0:
            raise ValueError('创建应付款单失败：应付款金额必须大于 0')

        order = DebtOrder.objects.create(
            supplier=supplier,
            amount=amount_decimal,
            status='OPEN',
            warehouse=warehouse,
            remark=(remark or '').strip(),
            created_by=created_by,
            source_type=source_type,
            source_id=source_id,
            settlement_mode=settlement_mode,
        )

        OperationLog.objects.create(
            operator=created_by,
            operation_type='OTHER',
            details=(
                f'创建应付款订单 #{order.id}，供货商: {order.supplier.name}，'
                f'金额: {order.amount}，仓库: {order.warehouse.name if order.warehouse else "未指定"}，'
                f'来源: {source_type}'
            ),
            related_object_id=order.id,
            related_content_type=ContentType.objects.get_for_model(DebtOrder),
        )
        return order

    @staticmethod
    def soft_delete_payable_order(*, order, operator, reason=''):
        if order.is_deleted:
            raise ValueError('应付款订单已删除，请勿重复操作')

        reason_text = (reason or '').strip() or '未填写'
        order.status = 'CANCELLED'
        order.is_deleted = True
        order.deleted_at = timezone.now()
        order.deleted_by = operator
        order.deleted_reason = reason_text
        order.save(update_fields=[
            'status',
            'is_deleted',
            'deleted_at',
            'deleted_by',
            'deleted_reason',
            'updated_at',
        ])

        OperationLog.objects.create(
            operator=operator,
            operation_type='OTHER',
            details=(
                f'软删除应付款订单 #{order.id}，供货商: {order.supplier.name}，'
                f'金额: {order.amount}，原因: {reason_text}'
            ),
            related_object_id=order.id,
            related_content_type=ContentType.objects.get_for_model(DebtOrder),
        )

        return order

    @staticmethod
    def create_settled_offset_order(
        *,
        order,
        operator,
        source_transaction_id,
        reason='',
    ):
        """
        Create a settled offset payable order for an already-settled source order.

        Returns:
            (offset_order, created: bool)
        """
        existing_offset = DebtOrder.objects.filter(offset_of=order).first()
        if existing_offset is not None:
            return existing_offset, False

        amount_decimal = Decimal(str(order.amount or '0'))
        offset_amount = -abs(amount_decimal)
        reason_text = (reason or '').strip() or '未填写'

        offset_order = DebtOrder.objects.create(
            supplier=order.supplier,
            amount=offset_amount,
            status='SETTLED',
            settlement_mode=order.settlement_mode,
            source_type='INVENTORY_VOID_OFFSET',
            source_id=source_transaction_id,
            offset_of=order,
            warehouse=order.warehouse,
            remark=(
                f'入库记录作废冲销应付: source_order_id={order.id}; '
                f'source_transaction_id={source_transaction_id}; reason={reason_text}'
            ),
            created_by=operator,
        )

        OperationLog.objects.create(
            operator=operator,
            operation_type='OTHER',
            details=(
                f'创建应付款冲销订单 #{offset_order.id}，原订单 #{order.id}，'
                f'金额: {offset_order.amount}，来源交易: {source_transaction_id}'
            ),
            related_object_id=offset_order.id,
            related_content_type=ContentType.objects.get_for_model(DebtOrder),
        )
        return offset_order, True

    @staticmethod
    def handle_inventory_void_payables(*, source_transaction_id, operator, reason=''):
        """
        Auto process payable orders linked to a voided stock-in transaction.
        """
        summary = {
            'soft_deleted_order_ids': [],
            'offset_created_order_ids': [],
            'skipped_order_ids': [],
        }

        related_orders = DebtOrder.objects.select_for_update().select_related(
            'supplier',
            'warehouse',
        ).filter(
            source_type__in=PayableService.INVENTORY_PAYABLE_SOURCE_TYPES,
            source_id=source_transaction_id,
        )

        for order in related_orders:
            if order.is_deleted or order.status == 'CANCELLED':
                summary['skipped_order_ids'].append(order.id)
                continue

            if order.status == 'OPEN':
                PayableService.soft_delete_payable_order(
                    order=order,
                    operator=operator,
                    reason=f'入库记录作废自动作废（source_transaction_id={source_transaction_id}）; {(reason or "").strip()}',
                )
                summary['soft_deleted_order_ids'].append(order.id)
                continue

            if order.status == 'SETTLED':
                offset_order, created = PayableService.create_settled_offset_order(
                    order=order,
                    operator=operator,
                    source_transaction_id=source_transaction_id,
                    reason=reason,
                )
                if created:
                    summary['offset_created_order_ids'].append(offset_order.id)
                else:
                    summary['skipped_order_ids'].append(order.id)
                continue

            summary['skipped_order_ids'].append(order.id)

        return summary
