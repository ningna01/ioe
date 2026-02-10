from django.db import migrations


def backfill_sale_status_from_logs(apps, schema_editor):
    Sale = apps.get_model('inventory', 'Sale')
    OperationLog = apps.get_model('inventory', 'OperationLog')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    sale_ct = ContentType.objects.filter(app_label='inventory', model='sale').first()
    if not sale_ct:
        return

    for sale in Sale.objects.filter(status='COMPLETED').iterator():
        cancelled = OperationLog.objects.filter(
            operation_type='SALE',
            related_object_id=sale.id,
            related_content_type_id=sale_ct.id,
            details__startswith=f'取消销售单 #{sale.id}'
        ).exists()
        if cancelled:
            sale.status = 'CANCELLED'
            sale.save(update_fields=['status'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0016_sale_status'),
    ]

    operations = [
        migrations.RunPython(backfill_sale_status_from_logs, noop),
    ]
