"""Order.progress is the single source for the guest-facing rail.

order_status.html renders both the segments and their labels from this one
list. Before it existed, each row restated the same four comparisons by hand,
so a change to the sequence had to land in eight places and the two rows could
disagree about which stage was current. These cases pin the mapping.
"""

from django.test import SimpleTestCase

from power_up.atmos.models import Order


class OrderProgressTests(SimpleTestCase):
    def _states(self, status):
        return [stage["state"] for stage in Order(status=status).progress]

    def test_placed(self):
        self.assertEqual(self._states("placed"), ["now", "", "", ""])

    def test_accepted(self):
        self.assertEqual(self._states("accepted"), ["done", "now", "", ""])

    def test_preparing(self):
        self.assertEqual(self._states("preparing"), ["done", "done", "now", ""])

    def test_served_marks_the_last_stage_end_not_now(self):
        self.assertEqual(self._states("served"), ["done", "done", "done", "end"])

    def test_cancelled_is_not_on_the_rail(self):
        """The template swaps the rail for a notice, but the property must not
        claim a stage is current for a status that never reaches one."""
        self.assertEqual(self._states("cancelled"), ["", "", "", ""])

    def test_labels_are_guest_copy_and_stable(self):
        labels = [stage["label"] for stage in Order(status="placed").progress]
        self.assertEqual(labels, ["Placed", "Accepted", "Mixing", "Served"])

    def test_every_rail_stage_is_a_real_order_status(self):
        valid = {name for name, _ in Order.STATUS_CHOICES}
        for name, _label in Order.PROGRESS_STAGES:
            self.assertIn(name, valid)
