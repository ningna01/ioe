"""
Warehouse inventory integrity reconciliation command.
Builds a consistency report for the WarehouseInventory-only model.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from inventory.models import (
    InventoryCheck,
    InventoryTransaction,
    Product,
    Sale,
    Warehouse,
    WarehouseInventory,
)


def build_inventory_reconciliation_report(sample_size=20):
    """Build integrity report under WarehouseInventory-only stock model."""
    duplicate_profiles = list(
        WarehouseInventory.objects.values(
            'product_id',
            'product__name',
            'warehouse_id',
            'warehouse__name',
        ).annotate(row_count=Count('id')).filter(row_count__gt=1)
    )

    negative_quantity_rows = list(
        WarehouseInventory.objects.filter(quantity__lt=0).values(
            'id',
            'product_id',
            'product__name',
            'warehouse_id',
            'warehouse__name',
            'quantity',
            'warning_level',
        ).order_by('quantity')
    )

    negative_warning_level_rows = list(
        WarehouseInventory.objects.filter(warning_level__lt=0).values(
            'id',
            'product_id',
            'product__name',
            'warehouse_id',
            'warehouse__name',
            'quantity',
            'warning_level',
        ).order_by('warning_level')
    )

    warning_level_conflicts = list(
        WarehouseInventory.objects.values('product_id', 'product__name').annotate(
            distinct_warning_levels=Count('warning_level', distinct=True)
        ).filter(distinct_warning_levels__gt=1)
    )

    products_without_warehouse_inventory = list(
        Product.objects.filter(is_active=True, warehouse_inventories__isnull=True).values(
            'id',
            'name',
            'barcode',
        )
    )

    sale_without_warehouse_count = Sale.objects.filter(warehouse__isnull=True).count()
    inventory_check_without_warehouse_count = InventoryCheck.objects.filter(warehouse__isnull=True).count()
    transaction_without_warehouse_count = InventoryTransaction.objects.filter(warehouse__isnull=True).count()

    report = {
        'generated_at': timezone.now().isoformat(),
        'sample_size': sample_size,
        'summary': {
            'product_scope_count': Product.objects.filter(is_active=True).count(),
            'warehouse_inventory_row_count': WarehouseInventory.objects.count(),
            'duplicate_profile_count': len(duplicate_profiles),
            'negative_quantity_count': len(negative_quantity_rows),
            'negative_warning_level_count': len(negative_warning_level_rows),
            'warning_level_conflict_count': len(warning_level_conflicts),
            'products_without_warehouse_inventory_count': len(products_without_warehouse_inventory),
            'sale_without_warehouse_count': sale_without_warehouse_count,
            'inventory_check_without_warehouse_count': inventory_check_without_warehouse_count,
            'transaction_without_warehouse_count': transaction_without_warehouse_count,
        },
        'classification': {
            'manual_review_required': {
                'duplicate_profiles': len(duplicate_profiles),
                'negative_quantities': len(negative_quantity_rows),
                'negative_warning_levels': len(negative_warning_level_rows),
                'products_without_warehouse_inventory': len(products_without_warehouse_inventory),
            },
            'warning_only': {
                'warning_level_conflicts': len(warning_level_conflicts),
            },
            'legacy_scope_gaps': {
                'sale_without_warehouse': sale_without_warehouse_count,
                'inventory_check_without_warehouse': inventory_check_without_warehouse_count,
                'transaction_without_warehouse': transaction_without_warehouse_count,
            },
        },
        'samples': {
            'duplicate_profiles': duplicate_profiles[:sample_size],
            'negative_quantity_rows': negative_quantity_rows[:sample_size],
            'negative_warning_level_rows': negative_warning_level_rows[:sample_size],
            'warning_level_conflicts': warning_level_conflicts[:sample_size],
            'products_without_warehouse_inventory': products_without_warehouse_inventory[:sample_size],
        },
    }
    return report


def _resolve_repair_warehouse():
    """Resolve a warehouse for alignment fixes, creating one only when no warehouse exists."""
    warehouse = Warehouse.objects.filter(is_default=True, is_active=True).first()
    if warehouse:
        return warehouse, 'active_default'

    warehouse = Warehouse.objects.filter(is_active=True).first()
    if warehouse:
        if not warehouse.is_default:
            warehouse.is_default = True
            warehouse.save(update_fields=['is_default'])
        return warehouse, 'promoted_active'

    warehouse = Warehouse.objects.order_by('id').first()
    if warehouse:
        update_fields = []
        if not warehouse.is_active:
            warehouse.is_active = True
            update_fields.append('is_active')
        if not warehouse.is_default:
            warehouse.is_default = True
            update_fields.append('is_default')
        if update_fields:
            warehouse.save(update_fields=update_fields)
        return warehouse, 'reactivated_existing'

    base_code = 'SYSTEM_DEFAULT_WH'
    candidate_code = base_code
    suffix = 1
    while Warehouse.objects.filter(code=candidate_code).exists():
        suffix += 1
        candidate_code = f'{base_code}_{suffix}'
    warehouse = Warehouse.objects.create(
        name='系统默认仓库',
        code=candidate_code,
        is_active=True,
        is_default=True,
    )
    return warehouse, 'created_system_default'


def apply_inventory_alignment_fixes():
    """
    Auto-fix warehouse alignment gaps:
    - create missing WarehouseInventory profiles for active products
    - backfill null warehouse on Sale / InventoryCheck / InventoryTransaction
    """
    with transaction.atomic():
        target_warehouse, warehouse_strategy = _resolve_repair_warehouse()

        # 1) 补齐活跃商品仓库库存档案
        missing_profile_product_ids = list(
            Product.objects.filter(is_active=True, warehouse_inventories__isnull=True)
            .values_list('id', flat=True)
        )
        created_profile_count = 0
        if missing_profile_product_ids:
            existing_product_ids = set(
                WarehouseInventory.objects.filter(
                    warehouse=target_warehouse,
                    product_id__in=missing_profile_product_ids,
                ).values_list('product_id', flat=True)
            )
            new_rows = [
                WarehouseInventory(
                    product_id=product_id,
                    warehouse=target_warehouse,
                    quantity=0,
                    warning_level=10,
                )
                for product_id in missing_profile_product_ids
                if product_id not in existing_product_ids
            ]
            if new_rows:
                WarehouseInventory.objects.bulk_create(new_rows)
                created_profile_count = len(new_rows)

        # 2) 回填 null warehouse 的交易记录前，确保目标仓有对应商品档案
        transaction_product_ids = set(
            InventoryTransaction.objects.filter(warehouse__isnull=True).values_list('product_id', flat=True)
        )
        created_profile_for_transaction_count = 0
        if transaction_product_ids:
            existing_tx_profile_product_ids = set(
                WarehouseInventory.objects.filter(
                    warehouse=target_warehouse,
                    product_id__in=transaction_product_ids,
                ).values_list('product_id', flat=True)
            )
            tx_new_rows = [
                WarehouseInventory(
                    product_id=product_id,
                    warehouse=target_warehouse,
                    quantity=0,
                    warning_level=10,
                )
                for product_id in transaction_product_ids
                if product_id not in existing_tx_profile_product_ids
            ]
            if tx_new_rows:
                WarehouseInventory.objects.bulk_create(tx_new_rows)
                created_profile_for_transaction_count = len(tx_new_rows)

        sale_backfilled_count = Sale.objects.filter(warehouse__isnull=True).update(warehouse=target_warehouse)
        inventory_check_backfilled_count = InventoryCheck.objects.filter(warehouse__isnull=True).update(
            warehouse=target_warehouse
        )
        transaction_backfilled_count = InventoryTransaction.objects.filter(warehouse__isnull=True).update(
            warehouse=target_warehouse
        )

    return {
        'warehouse_id': target_warehouse.id,
        'warehouse_name': target_warehouse.name,
        'warehouse_strategy': warehouse_strategy,
        'created_profile_count': created_profile_count,
        'created_profile_for_transaction_count': created_profile_for_transaction_count,
        'sale_backfilled_count': sale_backfilled_count,
        'inventory_check_backfilled_count': inventory_check_backfilled_count,
        'transaction_backfilled_count': transaction_backfilled_count,
    }


class Command(BaseCommand):
    help = 'Generate warehouse-inventory integrity report for release gate checks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sample-size',
            type=int,
            default=20,
            help='Number of sample rows in each category (default: 20).',
        )
        parser.add_argument(
            '--output',
            type=str,
            default='',
            help='Write full JSON report to file path.',
        )
        parser.add_argument(
            '--fail-on-critical',
            action='store_true',
            help='Return non-zero when manual review items are detected.',
        )
        parser.add_argument(
            '--apply-fixes',
            action='store_true',
            help='Apply auto-fixes for null-warehouse rows and missing warehouse inventory profiles.',
        )

    def handle(self, *args, **options):
        sample_size = max(1, int(options['sample_size']))
        auto_fix_summary = None

        if options['apply_fixes']:
            before_report = build_inventory_reconciliation_report(sample_size=sample_size)
            auto_fix_summary = apply_inventory_alignment_fixes()
            report = build_inventory_reconciliation_report(sample_size=sample_size)
            report['auto_fix'] = {
                'applied': True,
                'before_summary': before_report['summary'],
                'changes': auto_fix_summary,
            }
        else:
            report = build_inventory_reconciliation_report(sample_size=sample_size)

        summary = report['summary']
        manual_review = report['classification']['manual_review_required']
        critical_count = (
            int(manual_review['duplicate_profiles'])
            + int(manual_review['negative_quantities'])
            + int(manual_review['negative_warning_levels'])
            + int(manual_review['products_without_warehouse_inventory'])
        )

        self.stdout.write(self.style.SUCCESS('Warehouse inventory reconciliation report generated.'))
        self.stdout.write(f"Active products in scope: {summary['product_scope_count']}")
        self.stdout.write(f"Warehouse inventory rows: {summary['warehouse_inventory_row_count']}")
        self.stdout.write(f"Duplicate profiles: {summary['duplicate_profile_count']}")
        self.stdout.write(f"Negative quantities: {summary['negative_quantity_count']}")
        self.stdout.write(f"Negative warning levels: {summary['negative_warning_level_count']}")
        self.stdout.write(f"Warning-level conflicts: {summary['warning_level_conflict_count']}")
        self.stdout.write(
            f"Products without warehouse inventory: {summary['products_without_warehouse_inventory_count']}"
        )
        self.stdout.write(
            'Legacy scope gaps: '
            f"sale_without_warehouse={summary['sale_without_warehouse_count']}, "
            f"inventory_check_without_warehouse={summary['inventory_check_without_warehouse_count']}, "
            f"transaction_without_warehouse={summary['transaction_without_warehouse_count']}"
        )
        if auto_fix_summary:
            self.stdout.write(self.style.SUCCESS('Auto-fix applied with warehouse alignment updates:'))
            self.stdout.write(
                f"  warehouse={auto_fix_summary['warehouse_name']}({auto_fix_summary['warehouse_id']}) "
                f"strategy={auto_fix_summary['warehouse_strategy']}"
            )
            self.stdout.write(
                f"  created_profiles={auto_fix_summary['created_profile_count']} "
                f"created_profiles_for_transaction={auto_fix_summary['created_profile_for_transaction_count']}"
            )
            self.stdout.write(
                f"  backfilled: sales={auto_fix_summary['sale_backfilled_count']}, "
                f"inventory_checks={auto_fix_summary['inventory_check_backfilled_count']}, "
                f"transactions={auto_fix_summary['transaction_backfilled_count']}"
            )

        output_path = (options['output'] or '').strip()
        if output_path:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            self.stdout.write(self.style.SUCCESS(f'Report written to: {target}'))

        if options['fail_on_critical'] and critical_count > 0:
            raise CommandError(
                f'Critical reconciliation items detected: {critical_count}. '
                'See report for manual review details.'
            )
