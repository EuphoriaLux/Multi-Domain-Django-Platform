"""The ticket preview's CSS silently assumes the Python column count.

`base.html` sizes the on-screen thermal ticket with a container-query
`calc()` whose divisor encodes "48 columns of monospace fit the paper".
Nothing in the CSS can read `Paper.MM80`, so the two are coupled by comment
only. This test is that coupling: change one and it fails, which is the
whole point — a silent divergence makes the preview stop matching what the
printer actually emits.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from power_up.atmos.printing.layout import Paper

# 48 columns at ~0.6em advance = 28.8em; the divisor carries slack for monos
# wider than 0.6em. Derived from Paper.MM80, not independent of it.
EXPECTED_COLUMNS = 48
EXPECTED_DIVISOR = "29.5"


def _base_template() -> str:
    for directory in [Path(settings.BASE_DIR) / "power_up" / "templates"]:
        candidate = directory / "atmos" / "base.html"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError("could not locate atmos/base.html")


class TicketCssCouplingTests(SimpleTestCase):
    def test_python_column_count_is_what_the_css_assumes(self):
        self.assertEqual(
            Paper.MM80.columns,
            EXPECTED_COLUMNS,
            "Paper.MM80 changed. The ticket preview in atmos/base.html sizes "
            "itself for %s columns via a hardcoded calc() divisor and cannot "
            "read this enum — update the divisor and this test together."
            % EXPECTED_COLUMNS,
        )

    def test_css_divisor_still_present(self):
        css = _base_template()
        match = re.search(r"calc\(\(100cqi - .*?\) / ([0-9.]+)\)", css)
        self.assertIsNotNone(
            match, "the ticket font-size calc() is gone or was reshaped"
        )
        self.assertEqual(
            match.group(1),
            EXPECTED_DIVISOR,
            "the ticket calc() divisor changed; confirm it still fits "
            "%s columns and update EXPECTED_DIVISOR." % EXPECTED_COLUMNS,
        )

    def test_calc_subtracts_the_padding_variable_not_a_literal(self):
        """Padding and the calc must stay derived from one custom property."""
        css = _base_template()
        self.assertIn("--ticket-pad-x:", css)
        self.assertIn("calc((100cqi - (var(--ticket-pad-x) * 2))", css)
