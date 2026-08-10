"""
Tests for the structured venue address on MeetupEvent.

The address used to be one hand-typed TextField that echo.lu's payload builder
split back apart with a regex. These tests cover the fields that replaced it:

- `full_address` composition, and its fallback to the legacy free text
- Luxembourg postcode normalization ("L-2229" -> "2229")
- the admin form accepting a typed "L-" prefix (a validation-ordering trap)
- the schema.org PostalAddress mapping, which used to publish the venue *name*
  as the town
"""

import pytest
from datetime import timedelta

from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.models.events import normalize_lu_postcode
from crush_lu.views_events import _postal_address


def make_event(**overrides):
    """An unsaved event carrying only what these tests read."""
    fields = {
        "title": "Speed Dating",
        "description": "An evening.",
        "event_type": "speed_dating",
        "location": "Café Konrad",
        "address": "",
        "canton": "Luxembourg",
        "date_time": timezone.now() + timedelta(days=7),
        "registration_deadline": timezone.now() + timedelta(days=5),
    }
    fields.update(overrides)
    return MeetupEvent(**fields)


class TestFullAddress:
    """The composed one-liner every ticket, e-mail and calendar entry renders."""

    @pytest.mark.parametrize(
        "fields,expected",
        [
            (
                {
                    "address_street": "rue du Nord",
                    "address_number": "7",
                    "address_postcode": "2229",
                    "address_town": "Luxembourg",
                },
                "7, rue du Nord, L-2229 Luxembourg",
            ),
            (
                {
                    "address_street": "rue Emile Mark",
                    "address_number": "45",
                    "address_postcode": "4620",
                    "address_town": "Differdange",
                },
                "45, rue Emile Mark, L-4620 Differdange",
            ),
            # A venue with no house number must not gain a fabricated one.
            (
                {
                    "address_street": "Place de la Gare",
                    "address_postcode": "1616",
                    "address_town": "Luxembourg",
                },
                "Place de la Gare, L-1616 Luxembourg",
            ),
            # Missing postcode leaves no stray "L-" or double separator.
            (
                {
                    "address_street": "rue du Nord",
                    "address_number": "7",
                    "address_town": "Luxembourg",
                },
                "7, rue du Nord, Luxembourg",
            ),
            # Town but no street: still renders, no leading comma.
            (
                {"address_postcode": "2229", "address_town": "Luxembourg"},
                "L-2229 Luxembourg",
            ),
        ],
    )
    def test_composition(self, fields, expected):
        assert make_event(**fields).full_address == expected

    def test_falls_back_to_legacy_free_text(self):
        """Un-backfilled rows keep rendering exactly what they render today."""
        event = make_event(address="7, rue du Nord\nL-2229 Luxembourg")
        assert event.full_address == "7, rue du Nord\nL-2229 Luxembourg"

    def test_postcode_alone_does_not_shadow_legacy_text(self):
        """A half-parsed row must not replace a richer legacy string.

        If the fallback triggered on "no field set at all", a backfill that
        recovered only the postcode would publish a bare "L-2229" in place of
        the full address we already hold.
        """
        event = make_event(
            address="Behind the old brewery, L-2229",
            address_postcode="2229",
        )
        assert event.full_address == "Behind the old brewery, L-2229"

    def test_empty_is_empty_string(self):
        """Templates guard on truthiness before printing an address label."""
        assert make_event().full_address == ""


class TestPostcodeNormalization:
    @pytest.mark.parametrize(
        "typed", ["L-2229", "L2229", "l - 2229", "2229", "  2229  ", "l-2229"]
    )
    def test_accepted_spellings_reduce_to_four_digits(self, typed):
        assert normalize_lu_postcode(typed) == "2229"

    @pytest.mark.parametrize("typed", ["", None])
    def test_blank_stays_blank(self, typed):
        assert normalize_lu_postcode(typed) == ""

    @pytest.mark.parametrize("typed", ["ABCD", "22", "222900", "L-22"])
    def test_a_typo_is_passed_through_for_the_validator_to_report(self, typed):
        """The helper must never quietly repair a wrong postcode."""
        assert normalize_lu_postcode(typed) == typed.strip()


@pytest.mark.django_db
class TestAdminFormPostcode:
    """The field-length ordering trap.

    Django runs a form field's own validators before `clean_<name>()`. With the
    model's `max_length=4` propagated to the form, typing the natural "L-2229"
    is six characters and would be rejected before the prefix could be
    stripped. The admin form declares a wider field to avoid that.
    """

    def _form_data(self, **overrides):
        start = timezone.now() + timedelta(days=7)
        data = {
            # modeltranslation exposes the bare field alongside the per-language
            # columns, and the bare one is the required original.
            "title": "Speed Dating",
            "title_en": "Speed Dating",
            "description": "An evening.",
            "description_en": "An evening.",
            "event_type": "speed_dating",
            "location": "Café Konrad",
            "canton": "Luxembourg",
            "address_street": "rue du Nord",
            "address_number": "7",
            "address_postcode": "L-2229",
            "address_town": "Luxembourg",
            "date_time": start.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_minutes": 120,
            "registration_deadline": start.strftime("%Y-%m-%d %H:%M:%S"),
            "registration_fee": "0.00",
            "max_participants": 20,
            "reserved_premium_seats": 0,
            "min_age": 18,
            "max_age": 99,
            "registration_audience": "completed",
            "max_sparks_per_event": 3,
            "connection_window_hours": 48,
            "max_cross_gender_connections": 1,
            "max_invited_guests": 20,
        }
        data.update(overrides)
        return data

    def test_typed_prefix_is_accepted_and_stored_bare(self):
        from crush_lu.admin.events import MeetupEventAdminForm

        form = MeetupEventAdminForm(data=self._form_data())
        assert form.is_valid(), form.errors
        assert form.cleaned_data["address_postcode"] == "2229"

    def test_malformed_postcode_is_rejected(self):
        from crush_lu.admin.events import MeetupEventAdminForm

        form = MeetupEventAdminForm(data=self._form_data(address_postcode="22"))
        assert not form.is_valid()
        assert "address_postcode" in form.errors


class TestPostalAddressJsonLd:
    def test_components_map_to_their_own_keys(self):
        event = make_event(
            address_street="rue Emile Mark",
            address_number="45",
            address_postcode="4620",
            address_town="Differdange",
            canton="Esch-sur-Alzette",
        )
        postal = _postal_address(event)
        assert postal["streetAddress"] == "45, rue Emile Mark"
        assert postal["postalCode"] == "4620"
        assert postal["addressRegion"] == "Esch-sur-Alzette"

    def test_locality_is_the_town_not_the_venue_name(self):
        """Regression: `addressLocality` used to be `event.location`.

        That published "Café Konrad" as the town of the event.
        """
        event = make_event(
            location="Café Konrad",
            address_street="rue du Nord",
            address_town="Luxembourg",
        )
        assert _postal_address(event)["addressLocality"] == "Luxembourg"

    def test_legacy_row_still_gets_a_street_address(self):
        event = make_event(address="7, rue du Nord\nL-2229 Luxembourg")
        postal = _postal_address(event)
        assert postal["streetAddress"] == "7, rue du Nord\nL-2229 Luxembourg"
        assert "postalCode" not in postal
