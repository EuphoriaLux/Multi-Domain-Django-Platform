"""Expose FinOps import_cost_data command via the entreprinder app.

The actual implementation lives under entreprinder.finops, but that package
isn't installed as a separate Django app. By importing and subclassing the
FinOps command here, Django's command discovery (which looks only at
INSTALLED_APPS) can find it in production deployments.
"""

from entreprinder.finops.management.commands.import_cost_data import Command as FinOpsImportCostDataCommand


class Command(FinOpsImportCostDataCommand):
    """Alias for the FinOps import_cost_data command."""
