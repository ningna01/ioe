from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0022_sale_unsettled_deposit'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sale',
            name='status',
            field=models.CharField(
                choices=[
                    ('COMPLETED', '已完成'),
                    ('UNSETTLED', '未结算'),
                    ('ABANDONED', '已放弃'),
                    ('DELETED', '已删除'),
                ],
                default='COMPLETED',
                max_length=20,
                verbose_name='状态',
            ),
        ),
    ]
