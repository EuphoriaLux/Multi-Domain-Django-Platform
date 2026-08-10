"""Tests for the canton alias normalisation in migration 0224.

0222 keyed its alias table on whitespace-collapsed text, so the punctuated
spellings real data actually holds -- "Luxembourg - City", "Luxembourg-City",
"Lux" -- never matched and were silently left alone.
"""

import importlib

import pytest


class TestCantonAliases:
    @pytest.mark.parametrize(
        "typed,expected",
        [
            # The shapes staging actually held. 0222 keyed on whitespace-
            # collapsed text, so the punctuated spellings never matched and it
            # silently left them alone.
            ("Luxembourg - City", "Luxembourg"),
            ("Luxembourg-City", "Luxembourg"),
            ("Lux", "Luxembourg"),
            ("luxembourg", "Luxembourg"),
            ("Esch/Alzette", "Esch-sur-Alzette"),
            ("Beaufort", "Echternach"),
        ],
    )
    def test_canton_aliases_survive_punctuation(self, typed, expected):
        import importlib

        migration = importlib.import_module(
            "crush_lu.migrations.0224_normalize_event_canton_again"
        )
        by_key = {migration._key(name): name for name in migration.CANONICAL}
        key = migration._key(typed)
        assert (by_key.get(key) or migration.ALIASES.get(key)) == expected
