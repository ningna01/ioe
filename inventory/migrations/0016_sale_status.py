from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0015_migrate_inventory_to_warehouse'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='status',
            field=models.CharField(
                choices=[('COMPLETED', '已完成'), ('CANCELLED', '已取消')],
                default='COMPLETED',
                max_length=20,
                verbose_name='状态',
            ),
        ),
    ]
