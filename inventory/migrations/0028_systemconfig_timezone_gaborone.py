from django.db import migrations, models


def backfill_systemconfig_timezone(apps, schema_editor):
    SystemConfig = apps.get_model('inventory', 'SystemConfig')
    SystemConfig.objects.update(timezone='Africa/Gaborone')


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0027_debtorder_offset_of_inventorytransaction_is_voided_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemconfig',
            name='timezone',
            field=models.CharField(default='Africa/Gaborone', max_length=50, verbose_name='时区'),
        ),
        migrations.RunPython(backfill_systemconfig_timezone, migrations.RunPython.noop),
    ]
