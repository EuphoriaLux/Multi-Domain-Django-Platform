from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finops', '0006_increase_anomaly_deviation_precision'),
    ]

    operations = [
        migrations.CreateModel(
            name='CostBudget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('dimension_type', models.CharField(
                    choices=[
                        ('overall', 'Overall'),
                        ('subscription', 'Subscription'),
                        ('service', 'Service'),
                        ('resource_group', 'Resource Group'),
                    ],
                    default='overall',
                    max_length=30,
                )),
                ('dimension_value', models.CharField(blank=True, default='', help_text='Leave blank for overall budget', max_length=200)),
                ('monthly_budget', models.DecimalField(decimal_places=2, max_digits=12)),
                ('alert_threshold', models.IntegerField(default=80, help_text='Alert when utilization exceeds this percentage')),
                ('currency', models.CharField(default='EUR', max_length=10)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'finops_hub_costbudget',
                'ordering': ['name'],
            },
        ),
    ]
