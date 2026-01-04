# entreprinder/finops/models.py
"""
FinOps Hub Models - DEPRECATED - Use power_up.finops.models instead

These models have been migrated to power_up/finops/models.py.
This file re-exports them for backwards compatibility with existing code.

All new code should import from power_up.finops.models directly.
"""

# Re-export models from power_up for backwards compatibility
from power_up.finops.models import (
    CostExport,
    CostRecord,
    CostAggregation,
)

__all__ = ['CostExport', 'CostRecord', 'CostAggregation']
