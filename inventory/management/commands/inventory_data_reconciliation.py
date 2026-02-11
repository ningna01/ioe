"""
Warehouse inventory integrity reconciliation command.
Builds a consistency report for the WarehouseInventory-only model.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from inventory.models import (
    InventoryCheck,
    InventoryTransaction,
    Product,
    Sale,
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

    def handle(self, *args, **options):
        sample_size = max(1, int(options['sample_size']))
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
