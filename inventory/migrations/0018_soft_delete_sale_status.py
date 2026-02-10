from django.db import migrations, models


def convert_cancelled_to_deleted(apps, schema_editor):
    Sale = apps.get_model('inventory', 'Sale')
    Sale.objects.filter(status='CANCELLED').update(status='DELETED')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0017_backfill_sale_cancelled_status'),
    ]

    operations = [
        migrations.RunPython(convert_cancelled_to_deleted, noop),
        migrations.AlterField(
            model_name='sale',
            name='status',
            field=models.CharField(
                choices=[('COMPLETED', '已完成'), ('DELETED', '已删除')],
                default='COMPLETED',
                max_length=20,
                verbose_name='状态',
            ),
        ),
    ]
