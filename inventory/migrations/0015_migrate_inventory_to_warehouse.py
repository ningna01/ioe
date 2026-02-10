# 数据迁移：将旧表 Inventory 的库存复制到 WarehouseInventory（默认仓库）
# 执行时机：在入库入口改为 inventory_in 之前运行，保证历史数据在仓库管理中可见

from django.db import migrations


def migrate_inventory_to_warehouse(apps, schema_editor):
    """将 Inventory 中有库存的商品迁移到默认仓库的 WarehouseInventory"""
    Inventory = apps.get_model('inventory', 'Inventory')
    WarehouseInventory = apps.get_model('inventory', 'WarehouseInventory')
    Warehouse = apps.get_model('inventory', 'Warehouse')

    default_warehouse = Warehouse.objects.filter(is_default=True).first()
    if not default_warehouse:
        return

    for inv in Inventory.objects.filter(quantity__gt=0):
        wi, created = WarehouseInventory.objects.get_or_create(
            product=inv.product,
            warehouse=default_warehouse,
            defaults={'quantity': 0, 'warning_level': inv.warning_level or 10}
        )
        if created or wi.quantity == 0:
            wi.quantity = inv.quantity
        else:
            wi.quantity += inv.quantity
        wi.warning_level = inv.warning_level or 10
        wi.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0014_warehouse_init'),
    ]

    operations = [
        migrations.RunPython(migrate_inventory_to_warehouse, noop),
    ]
