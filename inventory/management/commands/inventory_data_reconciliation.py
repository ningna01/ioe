"""
Inventory data reconciliation command.
Builds a warehouse/global inventory consistency report for Step8 governance.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone

from inventory.models import (
    Inventory,
    InventoryCheck,
    InventoryTransaction,
    Product,
    Sale,
    WarehouseInventory,
)


def build_inventory_reconciliation_report(sample_size=20):
    """Build reconciliation report between Inventory and WarehouseInventory."""
    global_rows = list(
        Inventory.objects.values(
            'product_id',
            'quantity',
            'warning_level',
        )
    )
    warehouse_rows = list(
        WarehouseInventory.objects.values('product_id').annotate(
            total_quantity=Sum('quantity'),
            warehouse_count=Count('id'),
        )
    )
    warehouse_detail_rows = list(
        WarehouseInventory.objects.values(
            'product_id',
            'warehouse_id',
            'warehouse__name',
            'quantity',
        )
    )

    global_map = {
        row['product_id']: {
            'global_quantity': int(row['quantity'] or 0),
            'warning_level': int(row['warning_level'] or 0),
        }
        for row in global_rows
    }
    warehouse_map = {
        row['product_id']: {
            'warehouse_total_quantity': int(row['total_quantity'] or 0),
            'warehouse_count': int(row['warehouse_count'] or 0),
        }
        for row in warehouse_rows
    }
    warehouse_details_map = {}
    for row in warehouse_detail_rows:
        warehouse_details_map.setdefault(row['product_id'], []).append({
            'warehouse_id': row['warehouse_id'],
            'warehouse_name': row['warehouse__name'],
            'quantity': int(row['quantity'] or 0),
        })

    product_ids = sorted(set(global_map) | set(warehouse_map))
    product_names = {
        row['id']: row['name']
        for row in Product.objects.filter(id__in=product_ids).values('id', 'name')
    }

    matched_count = 0
    mismatched_products = []
    missing_global_inventory_products = []
    missing_warehouse_inventory_products = []

    for product_id in product_ids:
        product_name = product_names.get(product_id, f'#{product_id}')
        global_data = global_map.get(product_id)
        warehouse_data = warehouse_map.get(product_id)

        if global_data and warehouse_data:
            difference = warehouse_data['warehouse_total_quantity'] - global_data['global_quantity']
            if difference == 0:
                matched_count += 1
                continue

            mismatched_products.append({
                'product_id': product_id,
                'product_name': product_name,
                'global_quantity': global_data['global_quantity'],
                'warehouse_total_quantity': warehouse_data['warehouse_total_quantity'],
                'difference': difference,
                'warehouse_breakdown': warehouse_details_map.get(product_id, []),
                'suggested_action': 'manual_reconcile_quantity',
            })
            continue

        if warehouse_data and not global_data:
            missing_global_inventory_products.append({
                'product_id': product_id,
                'product_name': product_name,
                'global_quantity': None,
                'warehouse_total_quantity': warehouse_data['warehouse_total_quantity'],
                'difference': warehouse_data['warehouse_total_quantity'],
                'warehouse_breakdown': warehouse_details_map.get(product_id, []),
                'suggested_action': 'auto_create_global_inventory_profile',
            })
            continue

        if global_data and not warehouse_data:
            missing_warehouse_inventory_products.append({
                'product_id': product_id,
                'product_name': product_name,
                'global_quantity': global_data['global_quantity'],
                'warehouse_total_quantity': 0,
                'difference': -global_data['global_quantity'],
                'warehouse_breakdown': [],
                'suggested_action': 'manual_backfill_warehouse_inventory',
            })

    sale_without_warehouse_count = Sale.objects.filter(warehouse__isnull=True).count()
    inventory_check_without_warehouse_count = InventoryCheck.objects.filter(warehouse__isnull=True).count()
    transaction_without_warehouse_count = InventoryTransaction.objects.filter(warehouse__isnull=True).count()

    report = {
        'generated_at': timezone.now().isoformat(),
        'sample_size': sample_size,
        'summary': {
            'product_scope_count': len(product_ids),
            'matched_count': matched_count,
            'mismatched_count': len(mismatched_products),
            'missing_global_inventory_count': len(missing_global_inventory_products),
            'missing_warehouse_inventory_count': len(missing_warehouse_inventory_products),
            'sale_without_warehouse_count': sale_without_warehouse_count,
            'inventory_check_without_warehouse_count': inventory_check_without_warehouse_count,
            'transaction_without_warehouse_count': transaction_without_warehouse_count,
        },
        'classification': {
            'auto_fix_candidates': {
                'missing_global_inventory_profiles': len(missing_global_inventory_products),
            },
            'manual_review_required': {
                'quantity_mismatches': len(mismatched_products),
                'missing_warehouse_inventory': len(missing_warehouse_inventory_products),
            },
            'legacy_scope_gaps': {
                'sale_without_warehouse': sale_without_warehouse_count,
                'inventory_check_without_warehouse': inventory_check_without_warehouse_count,
                'transaction_without_warehouse': transaction_without_warehouse_count,
            },
        },
        'samples': {
            'mismatched_products': mismatched_products[:sample_size],
            'missing_global_inventory_products': missing_global_inventory_products[:sample_size],
            'missing_warehouse_inventory_products': missing_warehouse_inventory_products[:sample_size],
        },
    }
    return report


class Command(BaseCommand):
    help = 'Generate inventory reconciliation report for warehouse/global inventory consistency.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sample-size',
            type=int,
            default=20,
            help='Number of sample rows in each mismatch category (default: 20).',
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
            int(manual_review['quantity_mismatches'])
            + int(manual_review['missing_warehouse_inventory'])
        )

        self.stdout.write(self.style.SUCCESS('Inventory reconciliation report generated.'))
        self.stdout.write(f"Products in scope: {summary['product_scope_count']}")
        self.stdout.write(f"Matched: {summary['matched_count']}")
        self.stdout.write(f"Mismatched: {summary['mismatched_count']}")
        self.stdout.write(f"Missing global inventory: {summary['missing_global_inventory_count']}")
        self.stdout.write(f"Missing warehouse inventory: {summary['missing_warehouse_inventory_count']}")
        self.stdout.write(
            "Legacy scope gaps: "
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
