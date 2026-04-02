# Generated migration to handle finops models moved to power_up app
# This migration only updates Django's internal state - no actual database changes
# The tables (finops_hub_*) remain in place and are now managed by power_up.finops.models

from django.db import migrations


class Migration(migrations.Migration):
    """
    Handle the move of FinOps models from entreprinder to power_up.finops.

    The models CostExport, CostRecord, and CostAggregation were originally
    created by migration 0004 in the entreprinder app. They have since been
    moved to power_up/finops/models.py.

    This migration uses SeparateDatabaseAndState to:
    - Remove models from Django's state tracking for entreprinder app
    - NOT delete the actual database tables (they're still in use by power_up.finops)

    The db_table meta option in power_up.finops.models ensures the same tables
    (finops_hub_costexport, finops_hub_costrecord, finops_hub_costaggregation)
    continue to be used.
    """

    dependencies = [
        ('entreprinder', '0004_pixelcanvas_costaggregation_costexport_pixelhistory_and_more'),
    ]

    operations = [
        # Remove CostAggregation from entreprinder's Django state
        # (table finops_hub_costaggregation stays, now managed by power_up.finops)
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='CostAggregation'),
            ],
            database_operations=[],  # No database changes
        ),

        # Remove CostRecord from entreprinder's Django state
        # (table finops_hub_costrecord stays, now managed by power_up.finops)
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name='costrecord',
                    name='cost_export',
                ),
                migrations.DeleteModel(name='CostRecord'),
            ],
            database_operations=[],  # No database changes
        ),

        # Remove CostExport from entreprinder's Django state
        # (table finops_hub_costexport stays, now managed by power_up.finops)
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='CostExport'),
            ],
            database_operations=[],  # No database changes
        ),
    ]
