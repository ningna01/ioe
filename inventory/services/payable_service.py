from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from inventory.models import DebtOrder, OperationLog


class PayableService:
    """应付款业务服务。"""

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
