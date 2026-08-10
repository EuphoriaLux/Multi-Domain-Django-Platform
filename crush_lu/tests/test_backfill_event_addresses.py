"""
Tests for the one-time legacy-address backfill.

The parser's job is to be *confident or silent*. Most of these tests assert the
happy path; the ones that matter most assert what it refuses to do, because a
fabricated house number on a public national listing sends people to the wrong
door, while a blank one merely sends them to the right street.

The failure cases below are the ones recorded against the original echo.lu
address parser: a venue name on the first line, and a venue name that starts
with digits.
"""

import pytest
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.management.commands.backfill_event_addresses import (
    CHECK_NUMBER,
    CHECK_STREET,
    EXTRA_TEXT,
    NO_NUMBER,
    NO_POSTCODE,
    NO_STREET,
    NO_TOWN,
    OK,
    TOO_LONG,
    WRITABLE_STATUSES,
    parse_legacy_address,
)


class TestParser:
    def test_venue_name_on_the_first_line(self):
        """The original bug: line 0 was assumed to be the street."""
        fields, status, _ = parse_legacy_address(
            "Café Konrad\n7, rue du Nord\nL-2229 Luxembourg", "Café Konrad"
        )
        assert status == OK
        assert fields == {
            "address_street": "rue du Nord",
            "address_number": "7",
            "address_postcode": "2229",
            "address_town": "Luxembourg",
        }

    def test_venue_name_starting_with_digits(self):
        """The other original bug: '1535' was read as the postcode.

        The real postcode has no 'L-' here, so this only works because the scan
        runs backwards.
        """
        fields, status, _ = parse_legacy_address(
            "1535 Creative Hub\n45 rue Emile Mark\n4620 Differdange",
            "1535 Creative Hub",
        )
        assert status == OK
        assert fields["address_postcode"] == "4620"
        assert fields["address_street"] == "rue Emile Mark"
        assert fields["address_number"] == "45"
        assert fields["address_town"] == "Differdange"

    def test_house_number_range(self):
        fields, status, _ = parse_legacy_address(
            "Brasserie Guillaume\n12-14 place Guillaume II\nL-1648 Luxembourg",
            "Brasserie Guillaume",
        )
        assert status == OK
        assert fields["address_number"] == "12-14"
        assert fields["address_street"] == "place Guillaume II"

    def test_a_trailing_house_number_is_split_but_never_called_clean(self):
        """ "rue de la Tour Jacob 145" and "Rue de l'An 40" are the same shape.

        One ends in a house number, the other in part of the street's name, and
        nothing in the text tells them apart -- only a street register could.
        The split is offered for a human to confirm, but never reported clean
        and never written, because a fabricated one sends a national listing to
        the wrong door while the audit says everything is fine.
        """
        fields, status, _ = parse_legacy_address(
            "Melusina\nrue de la Tour Jacob 145\nL-1831 Luxembourg", "Melusina"
        )
        assert status == CHECK_NUMBER
        assert status not in WRITABLE_STATUSES
        assert fields["address_number"] == "145"
        assert fields["address_street"] == "rue de la Tour Jacob"

    def test_a_street_name_ending_in_digits_is_not_written(self):
        _fields, status, _ = parse_legacy_address(
            "Rue de l'An 40\nL-1234 Luxembourg", "X"
        )
        assert status not in WRITABLE_STATUSES

    def test_a_number_on_its_own_line_does_not_smuggle_the_venue_into_street(self):
        """Regression on the fix above, not the original bug.

        Rejoining a lone number with the line above turns "Café Konrad / 7"
        into "7, Café Konrad", which no longer matches the venue name — so the
        guard that refuses venue-as-street missed it and the parser reported a
        clean street="Café Konrad", number="7". The guard now runs again after
        the number comes off.
        """
        fields, status, _ = parse_legacy_address(
            "Café Konrad\n7\nL-2229 Luxembourg", "Café Konrad"
        )
        assert status == NO_STREET
        assert "address_street" not in fields
        assert "address_number" not in fields

    def test_a_house_number_on_its_own_line_rejoins_its_street(self):
        """ "Rue Test / 7 / L-1234 Luxembourg" must not glue 7 to the postcode."""
        fields, status, _ = parse_legacy_address("Rue Test\n7\nL-1234 Luxembourg", "X")
        assert status == OK
        assert fields["address_street"] == "Rue Test"
        assert fields["address_number"] == "7"
        assert fields["address_town"] == "Luxembourg"

    def test_a_bare_number_in_a_trailing_line_is_not_the_postcode(self):
        """Regression: the fallback took the LAST segment containing 4 digits.

        "Depuis 1920" then beat the real "4620 Differdange", yielding
        postcode=1920, town="Depuis", street="Differdange", number="4620" --
        complete garbage. The fallback now requires the digits to START the
        segment, which is the shape a real postcode+town line takes.
        """
        fields, status, _ = parse_legacy_address(
            "45 rue Emile Mark\n4620 Differdange\nDepuis 1920", "X"
        )
        assert fields["address_postcode"] == "4620"
        assert fields["address_town"] == "Differdange"
        assert fields["address_street"] == "rue Emile Mark"
        assert status == EXTRA_TEXT  # "Depuis 1920" is still unexplained
        assert status not in WRITABLE_STATUSES

    def test_length_is_checked_before_the_needs_a_human_statuses(self):
        """An oversized value must not escape under EXTRA_TEXT and hit the DB."""
        _fields, status, _ = parse_legacy_address(
            "Door 4\n" + "9" * 30 + " rue Longue\nL-2229 Luxembourg", "X"
        )
        assert status == TOO_LONG

    def test_single_line_address(self):
        fields, status, _ = parse_legacy_address("Place de la Gare, L-1616 Luxembourg")
        assert status == NO_NUMBER
        assert fields["address_street"] == "Place de la Gare"
        assert "address_number" not in fields
        assert fields["address_postcode"] == "1616"

    def test_a_venue_with_no_number_gets_none_invented(self):
        """A street word carries a line that has no house number."""
        fields, status, _ = parse_legacy_address("Place de la Gare, L-1616 Luxembourg")
        assert status == NO_NUMBER
        assert "address_number" not in fields
        assert fields["address_street"] == "Place de la Gare"

    def test_a_line_that_is_neither_numbered_nor_street_like_needs_a_human(self):
        """ "Parking Heringermill" is a place, not a street.

        With no house number and no street word there is nothing to confirm it
        belongs in `street`, so it is reported rather than written -- the same
        rule that stops "Rear entrance" being published as a street name.
        """
        _fields, status, _ = parse_legacy_address(
            "Parking Heringermill, L-6360 Beaufort"
        )
        assert status == CHECK_STREET
        assert status not in WRITABLE_STATUSES

    def test_an_access_instruction_is_not_accepted_as_a_street(self):
        fields, status, _ = parse_legacy_address(
            "Café Konrad\nRear entrance\nL-2229 Luxembourg", "Café Konrad"
        )
        assert status == CHECK_STREET
        assert status not in WRITABLE_STATUSES
        assert fields["address_town"] == "Luxembourg"

    def test_a_luxembourgish_prepositional_street_is_recognised(self):
        """ "Op der Haart" has no generic noun but is a real street name."""
        _fields, status, _ = parse_legacy_address("Op der Haart\nL-9999 Wiltz", "X")
        assert status == NO_NUMBER

    @pytest.mark.parametrize("suffix", ["12 bis", "12bis", "12 ter", "12ter"])
    def test_an_ordinal_house_number_suffix_stays_with_the_number(self, suffix):
        """Both spellings, spaced and not.

        Without the suffix, "12 bis rue du Nord" split into number 12 and a
        street called "bis rue du Nord". The space-less "12bis" then defeated
        that first fix: it matched no number at all, and the street-word check
        waved the whole line through as a clean street.
        """
        fields, status, _ = parse_legacy_address(
            f"{suffix} rue du Nord\nL-2229 Luxembourg", "X"
        )
        assert status == OK
        assert fields["address_number"] == suffix
        assert fields["address_street"] == "rue du Nord"

    def test_an_oversized_town_is_caught_on_the_no_street_path_too(self):
        """The length check has to guard every exit, not just the full one.

        `address_town` is set well before the street logic runs, so a NO_STREET
        return carried an oversized town straight past the check -- and Postgres
        rejecting one value aborts the whole backfill mid-run.
        """
        _fields, status, _ = parse_legacy_address("L-2229 " + "A" * 150, "X")
        assert status == TOO_LONG
        assert status not in WRITABLE_STATUSES

    def test_no_postcode_writes_nothing(self):
        """Without an anchor there is no way to tell prose from a street."""
        fields, status, _ = parse_legacy_address("Behind the old brewery")
        assert status == NO_POSTCODE
        assert fields == {}

    def test_blank_input(self):
        fields, status, _ = parse_legacy_address("")
        assert status == NO_POSTCODE
        assert fields == {}

    def test_postcode_without_a_town_keeps_only_the_postcode(self):
        fields, status, _ = parse_legacy_address("Some hall\nL-2229 Hall 3")
        assert status == NO_TOWN
        assert fields == {"address_postcode": "2229"}

    def test_postcode_line_first_leaves_no_street(self):
        fields, status, _ = parse_legacy_address("L-2229 Luxembourg")
        assert status == NO_STREET
        assert fields == {"address_postcode": "2229", "address_town": "Luxembourg"}

    def test_unexplained_extra_lines_are_reported_not_dropped(self):
        fields, status, note = parse_legacy_address(
            "Door 4\nBack entrance\n7 rue du Nord\nL-2229 Luxembourg",
            "Café Konrad",
        )
        assert status == EXTRA_TEXT
        assert "Door 4" in note and "Back entrance" in note
        assert fields["address_street"] == "rue du Nord"

    def test_the_venue_name_itself_is_not_reported_as_extra(self):
        _fields, status, _note = parse_legacy_address(
            "Café Konrad\n7 rue du Nord\nL-2229 Luxembourg", "Café Konrad"
        )
        assert status == OK

    def test_the_venue_name_is_never_taken_for_the_street(self):
        """Regression: a venue name directly above the postcode.

        With no dedicated street line, the segment above the postcode IS the
        venue. Feeding it to the house-number parser produced
        street="Creative Hub", number="1535" -- a fabricated address that
        reported OK and passed --audit.
        """
        fields, status, note = parse_legacy_address(
            "1535 Creative Hub\nL-4620 Differdange", "1535 Creative Hub"
        )
        assert status == NO_STREET
        assert "address_street" not in fields
        assert "address_number" not in fields
        assert fields["address_town"] == "Differdange"
        assert note == "1535 Creative Hub"

    def test_text_after_the_postcode_is_reported_not_dropped(self):
        """Access instructions live below the locality line."""
        fields, status, note = parse_legacy_address(
            "7 rue du Nord\nL-2229 Luxembourg\nRear entrance", "Café Konrad"
        )
        assert status == EXTRA_TEXT
        assert note == "Rear entrance"
        assert fields["address_street"] == "rue du Nord"

    def test_a_component_too_long_for_its_column_is_reported(self):
        """Postgres would abort the whole run; report the row instead."""
        fields, status, note = parse_legacy_address(
            "9" * 30 + " rue Longue\nL-2229 Luxembourg", "X"
        )
        assert status == TOO_LONG
        assert "number=" in note
        assert len(fields["address_number"]) > 20


@pytest.fixture
def legacy_event(db):
    return MeetupEvent.objects.create(
        title="Speed Dating",
        description="An evening.",
        event_type="speed_dating",
        location="Café Konrad",
        address="Café Konrad\n7, rue du Nord\nL-2229 Luxembourg",
        canton="Luxembourg",
        date_time=timezone.now() + timedelta(days=7),
        registration_deadline=timezone.now() + timedelta(days=5),
    )


@pytest.mark.django_db
class TestCommand:
    def test_dry_run_writes_nothing(self, legacy_event):
        call_command("backfill_event_addresses", "--dry-run", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == ""

    def test_backfill_populates_and_leaves_legacy_text_intact(self, legacy_event):
        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "rue du Nord"
        assert legacy_event.address_number == "7"
        assert legacy_event.address_postcode == "2229"
        assert legacy_event.address_town == "Luxembourg"
        # Non-destructive: the source text is the only copy of anything the
        # parser could not place, so it is never rewritten.
        assert legacy_event.address.startswith("Café Konrad")

    def test_rerun_is_a_no_op(self, legacy_event):
        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        legacy_event.address_street = "hand-corrected street"
        legacy_event.save(update_fields=["address_street"])

        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "hand-corrected street"

    def test_force_reparses(self, legacy_event):
        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        legacy_event.address_street = "hand-corrected street"
        legacy_event.save(update_fields=["address_street"])

        call_command("backfill_event_addresses", "--force", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "rue du Nord"

    def test_audit_fails_while_a_published_event_is_incomplete(self, legacy_event):
        legacy_event.is_published = True
        legacy_event.save(update_fields=["is_published"])

        with pytest.raises(CommandError):
            call_command("backfill_event_addresses", "--audit", stdout=StringIO())

    def test_audit_passes_once_everything_is_filled(self, legacy_event):
        legacy_event.is_published = True
        legacy_event.save(update_fields=["is_published"])
        call_command("backfill_event_addresses", stdout=StringIO())

        out = StringIO()
        call_command("backfill_event_addresses", "--audit", stdout=out)
        assert "complete structured address" in out.getvalue()

    def test_the_audit_names_the_missing_fields(self, legacy_event):
        """ "incomplete address" alone means opening every event to find out."""
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            is_published=True, canton=""
        )
        out = StringIO()
        with pytest.raises(CommandError):
            call_command("backfill_event_addresses", "--audit", stdout=out)

        report = out.getvalue()
        assert "canton" in report
        assert "street" in report

    def test_audit_flags_a_canton_outside_the_list(self, legacy_event):
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(canton="Beaufort")

        with pytest.raises(CommandError):
            call_command("backfill_event_addresses", "--audit", stdout=StringIO())

    def test_audit_rejects_a_present_but_invalid_postcode(self, legacy_event):
        """Presence is not validity.

        A row holding "ABCD" is skipped by the normal backfill (it already has
        structured data) and used to satisfy --audit, so the gate reported
        all-clear on exactly the row the validation it gates would refuse.
        """
        call_command("backfill_event_addresses", stdout=StringIO())
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            is_published=True, address_postcode="ABCD"
        )

        with pytest.raises(CommandError):
            call_command("backfill_event_addresses", "--audit", stdout=StringIO())

    def test_the_backfill_does_not_fan_out_wallet_refreshes(self, legacy_event):
        """The address columns are in _TICKET_PAYLOAD_BASE_FIELDS.

        Saving them per event would push an Apple Wallet refresh to every pass
        holder — across a back catalogue, an APNs storm to publish an address
        that renders the same as the legacy text it came from.
        """
        with patch("crush_lu.signals.refresh_apple_tickets_on_event_change") as refresh:
            call_command("backfill_event_addresses", stdout=StringIO())

        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "rue du Nord"
        refresh.assert_not_called()

    def test_audit_flags_a_published_event_with_no_canton(self, legacy_event):
        """`clean()` has required a canton on published events all along.

        Skipping blanks let --audit report all-clear on a row the very
        validation it gates would reject.
        """
        call_command("backfill_event_addresses", stdout=StringIO())
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            canton="", is_published=True
        )

        with pytest.raises(CommandError):
            call_command("backfill_event_addresses", "--audit", stdout=StringIO())

    def test_an_unpublished_event_may_have_no_canton(self, legacy_event):
        call_command("backfill_event_addresses", stdout=StringIO())
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            canton="", is_published=False
        )

        out = StringIO()
        call_command("backfill_event_addresses", "--audit", stdout=out)
        assert "complete structured address" in out.getvalue()

    def test_a_row_needing_a_human_is_not_written(self, legacy_event):
        """EXTRA_TEXT used to write, which hid the dropped line from --audit."""
        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            address="7 rue du Nord\nL-2229 Luxembourg\nRear entrance"
        )
        call_command("backfill_event_addresses", stdout=StringIO())

        legacy_event.refresh_from_db()
        assert legacy_event.address_street == ""
        assert legacy_event.full_address.endswith("Rear entrance")

    def test_force_clears_a_component_the_reparse_says_is_gone(self, legacy_event):
        """A stale house number must not survive a clean reparse.

        Writing only the parsed keys left the old number in place while the
        command reported OK and --audit passed.
        """
        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_number == "7"

        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            address="Place de la Gare\nL-1616 Luxembourg"
        )
        call_command("backfill_event_addresses", "--force", stdout=StringIO())

        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "Place de la Gare"
        assert legacy_event.address_number == ""

    def test_force_never_mixes_a_new_parse_with_old_components(self, legacy_event):
        """A --force reparse owns the whole address, partial or not.

        Writing only the parsed keys left a fresh postcode sitting beside a
        street from the previous run -- an address that never existed in any
        single source.
        """
        call_command("backfill_event_addresses", stdout=StringIO())
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == "rue du Nord"

        MeetupEvent.objects.filter(pk=legacy_event.pk).update(
            address="L-4620 Differdange"
        )
        call_command("backfill_event_addresses", "--force", stdout=StringIO())

        legacy_event.refresh_from_db()
        assert legacy_event.address_postcode == "4620"
        assert legacy_event.address_town == "Differdange"
        assert legacy_event.address_street == ""
        assert legacy_event.address_number == ""

    def test_event_id_zero_does_not_backfill_everything(self, legacy_event):
        """Truthiness would let `--event-id 0` fall through to every event."""
        with pytest.raises(CommandError):
            call_command(
                "backfill_event_addresses", "--event-id", "0", stdout=StringIO()
            )
        legacy_event.refresh_from_db()
        assert legacy_event.address_street == ""
