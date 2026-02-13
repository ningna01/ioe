from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0021_delete_inventory'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='deposit_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='定金金额'),
        ),
        migrations.AlterField(
            model_name='sale',
            name='payment_method',
            field=models.CharField(
                choices=[
                    ('cash', '现金'),
                    ('card', '银行卡'),
                    ('balance', '账户余额'),
                    ('mixed', '混合支付'),
                    ('other', '其他'),
                ],
                default='cash',
                max_length=20,
                verbose_name='支付方式',
            ),
        ),
        migrations.AlterField(
            model_name='sale',
            name='status',
            field=models.CharField(
                choices=[
                    ('COMPLETED', '已完成'),
                    ('UNSETTLED', '未结算'),
                    ('DELETED', '已删除'),
                ],
                default='COMPLETED',
                max_length=20,
                verbose_name='状态',
            ),
        ),
    ]
