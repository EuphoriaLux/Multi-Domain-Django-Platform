"""Fill the structured address fields from the legacy free-text `address`.

A management command rather than a data migration, deliberately. Migrations run
unattended on deploy; this parses hand-typed text whose output is published on
a national events portal, so it has to be readable before it is trusted,
re-runnable after somebody fixes a row by hand, and safe to leave half-done.
`MeetupEvent.full_address` already falls back to the legacy text, so nothing
breaks while this has not run.

    python manage.py backfill_event_addresses --dry-run   # read this first
    python manage.py backfill_event_addresses
    python manage.py backfill_event_addresses --audit      # what still needs a human

The parser is confident-or-nothing. It never invents a house number and never
guesses a street: a component it cannot identify is left blank and the event is
reported, because a wrong number on a public listing sends people to the wrong
door, while a missing one merely sends them to the right street.
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crush_lu.models import MeetupEvent

# A Luxembourg postcode: four digits, optionally behind the national "L-".
# ASCII digits only -- `\d` matches every Unicode decimal, and "٢٢٢٩" is not a
# Luxembourg postcode however convincingly it renders.
_POSTCODE_RE = re.compile(r"\bL?\s*-?\s*([0-9]{4})\b", re.IGNORECASE)
_PREFIXED_POSTCODE_RE = re.compile(r"\bL\s*-\s*([0-9]{4})\b", re.IGNORECASE)
# The postcode at the START of its segment: the shape of a real "4620
# Differdange" line, as opposed to a number that merely appears somewhere in a
# line of prose.
_LEADING_POSTCODE_RE = re.compile(r"^L?\s*-?\s*([0-9]{4})\b", re.IGNORECASE)

# A house number: a digit run, optionally with a letter, optionally a range, and
# optionally an ordinal suffix -- "12 bis" and "12 ter" are ordinary here, and
# without them "12 bis rue du Nord" splits into number "12" and a street called
# "bis rue du Nord".
# Anchored to one end of the street segment or the other, because Luxembourg
# writes both "12, rue de la Gare" and "rue de la Gare 12".
_NUM = r"[0-9]+\s*[A-Za-z]?(?:\s*[-/]\s*[0-9]+\s*[A-Za-z]?)?(?:\s*(?:bis|ter|quater))?"
_LEADING_NUMBER_RE = re.compile(rf"^({_NUM})(?=[,\s])", re.IGNORECASE)
_TRAILING_NUMBER_RE = re.compile(rf"(?<=[,\s])({_NUM})$", re.IGNORECASE)

# What makes a line recognisable as a street. Luxembourg addresses are written
# in French, German and Luxembourgish, often mixed, so this covers all three --
# including the prepositional forms ("Op der Haart", "Am Bongert") that carry no
# generic noun at all.
#
# This is the parser's entire notion of what a street looks like, and it does
# NOT need to be exhaustive. A line it fails to recognise is reported for a
# person to confirm, never written. Being wrong here costs someone a minute in
# the admin; the alternative -- accepting any line above the postcode -- writes
# "Rear entrance" into a national listing as a street name.
_STREET_WORDS = frozenset("""
    rue route avenue boulevard place allee allée chemin impasse quai
    montee montée cite cité esplanade square passage galerie parc val
    coin zone domaine residence résidence rond-point
    strooss strasse straße wee gaass plaz breck haart bierg duerf
    eck bongert millen weg gass
    """.split())
_STREET_PREFIXES = ("op ", "an ", "am ", "um ", "bei ", "beim ", "hannert ")


def _looks_like_a_street(candidate):
    """Whether a line is recognisable as a street name.

    A house number is the strongest signal and is checked by the caller. This
    covers the rest: a generic noun anywhere in the line, or one of the
    prepositional openings Luxembourgish street names use.
    """
    lowered = candidate.casefold()
    if lowered.startswith(_STREET_PREFIXES):
        return True
    return any(word.strip(".,").casefold() in _STREET_WORDS for word in lowered.split())


# Statuses. Only OK and NO_NUMBER are clean outcomes -- a venue with no street
# number is normal, not a failure.
OK = "OK"
NO_NUMBER = "NO_NUMBER"
NO_POSTCODE = "NO_POSTCODE"
NO_TOWN = "NO_TOWN"
NO_STREET = "NO_STREET"
EXTRA_TEXT = "EXTRA_TEXT"
TOO_LONG = "TOO_LONG"
CHECK_NUMBER = "CHECK_NUMBER"
CHECK_STREET = "CHECK_STREET"

CLEAN_STATUSES = {OK, NO_NUMBER}

# Which parses may touch the database. Every one of them writes all four
# components (see the write loop); the question here is only whether a parse is
# trustworthy enough to write at all.
#
# A clean parse is. So are NO_STREET and NO_TOWN: they resolve less, but what
# they resolve is right, and with no street `full_address` keeps falling back
# to the legacy text, so nothing published changes either way.
#
# The rest write NOTHING. EXTRA_TEXT and CHECK_NUMBER both produce a complete
# street/postcode/town -- which is exactly the problem, because writing it hands
# `full_address` over to the structured fields and the unexplained line, or the
# possibly-fabricated house number, silently stops being published. Worse, the
# row then looks complete to `--audit` and nothing ever flags it again.
WRITABLE_STATUSES = CLEAN_STATUSES | {NO_STREET, NO_TOWN}

# Every component the parser can produce, so a --force reparse can clear the
# ones a clean parse says are absent instead of leaving stale values behind.
ADDRESS_FIELDS = (
    "address_street",
    "address_number",
    "address_postcode",
    "address_town",
)


def _same_place(segment, venue_name):
    """Whether a segment is just the venue name we already hold in `location`."""
    return bool(venue_name) and segment.casefold() == venue_name.casefold()


def _too_long_for_column(fields):
    """Report any parsed component the column cannot hold, or "" if all fit."""
    too_long = [
        f"{name.removeprefix('address_')}={value!r}"
        for name, value in fields.items()
        if len(value) > MeetupEvent._meta.get_field(name).max_length
    ]
    return " ".join(too_long)


def _result(fields, status, note):
    """Every exit from the parser goes through here.

    The length check has to guard *all* of them, not just the fully-parsed
    path. `address_town` is set long before the street logic runs, so the
    NO_STREET returns carried a town straight past the check -- and Postgres
    rejecting one oversized value aborts the entire backfill mid-run, leaving
    a half-migrated table behind.
    """
    oversized = _too_long_for_column(fields)
    if oversized:
        return fields, TOO_LONG, oversized
    return fields, status, note


# A segment that is nothing but a house number. Luxembourg's dominant format is
# "7, rue du Nord" -- so splitting on commas alone would tear the number off its
# street, strand it as an unexplained extra line, and lose it.
_BARE_NUMBER_RE = re.compile(r"^\d+\s*[A-Za-z]?(?:\s*[-/]\s*\d+\s*[A-Za-z]?)?$")


def _segments(text):
    """Split free text into address segments, on newlines and then commas."""
    pieces = []
    for line in (text or "").splitlines():
        for piece in line.split(","):
            piece = piece.strip(" \t-")
            if piece:
                pieces.append(piece)

    segments = []
    index = 0
    while index < len(pieces):
        piece = pieces[index]
        # A segment that is only a house number belongs to the street beside
        # it -- but only if the neighbour IS a street. "Rue Test / 7 / L-1234
        # Luxembourg" would otherwise glue the number to the postcode line and
        # lose both the street and the town.
        if (
            _BARE_NUMBER_RE.match(piece)
            and index + 1 < len(pieces)
            and not _POSTCODE_RE.search(pieces[index + 1])
        ):
            segments.append(f"{piece}, {pieces[index + 1]}")
            index += 2
            continue
        if _BARE_NUMBER_RE.match(piece) and segments:
            # Number on its own line, with the street above it instead.
            segments[-1] = f"{piece}, {segments[-1]}"
            index += 1
            continue
        segments.append(piece)
        index += 1
    return segments


def parse_legacy_address(text, venue_name=""):
    """Split hand-typed address text into components.

    Returns ``(fields, status, note)``. ``fields`` only ever contains keys the
    parser is confident about, so a caller can apply it wholesale.

    The postcode is located first because everything else is positioned
    relative to it. An "L-" prefixed match wins anywhere. Failing that, the
    last segment that *starts* with a bare four-digit group wins -- that is the
    shape a real "4620 Differdange" line takes, and requiring it rules out both
    a venue whose name starts with digits ("1535 Creative Hub") and a trailing
    aside that merely contains a number ("Depuis 1920"), either of which would
    otherwise be read as the postcode and collapse the whole address.
    """
    segments = _segments(text)
    if not segments:
        return _result({}, NO_POSTCODE, "empty")

    postcode_index = None
    match = None
    for index, segment in enumerate(segments):
        found = _PREFIXED_POSTCODE_RE.search(segment)
        if found:
            postcode_index, match = index, found
            break
    if match is None:
        for index in range(len(segments) - 1, -1, -1):
            found = _LEADING_POSTCODE_RE.match(segments[index])
            if found:
                postcode_index, match = index, found
                break
    if match is None:
        # Without a postcode there is no anchor, and guessing which line is the
        # street from prose like "Behind the old brewery" is exactly the kind of
        # invention this parser refuses.
        return _result({}, NO_POSTCODE, segments[0])

    fields = {"address_postcode": match.group(1)}

    postcode_segment = segments[postcode_index]
    town = (
        postcode_segment[: match.start()] + " " + postcode_segment[match.end() :]
    ).strip(" ,-")
    if not town or any(char.isdigit() for char in town):
        # "L-2229" alone, or something like "L-2229 Hall 3" that is not a town.
        return _result(fields, NO_TOWN, postcode_segment)
    fields["address_town"] = town

    if postcode_index == 0:
        # Everything sat on one line and the postcode consumed it; there is no
        # separate street segment to read.
        return _result(fields, NO_STREET, postcode_segment)

    street_segment = segments[postcode_index - 1]

    # The segment above the postcode is only the street if it is not the venue
    # name. "1535 Creative Hub\nL-4620 Differdange" has no street line at all,
    # and feeding the venue to the house-number parser yields street="Creative
    # Hub", number="1535" -- the exact shape of wrong address this parser
    # exists to refuse, and it would report OK and sail past --audit.
    #
    # Checked twice, before and after the number comes off, because a house
    # number can arrive attached to the venue from either end: "1535 Creative
    # Hub" only matches before, and "Café Konrad" with a number on its own line
    # (which _segments rejoins into "7, Café Konrad") only matches after.
    if _same_place(street_segment, venue_name):
        return _result(fields, NO_STREET, street_segment)

    number = ""
    ambiguous_number = False
    leading = _LEADING_NUMBER_RE.match(street_segment)
    if leading:
        number = leading.group(1).strip()
        street_segment = street_segment[leading.end() :].strip(" ,")
    else:
        trailing = _TRAILING_NUMBER_RE.search(street_segment)
        if trailing:
            # A number at the END is genuinely ambiguous: "rue de la Tour Jacob
            # 145" is a house number, and Luxembourg's "Rue de l'An 40" is part
            # of the street's name. Nothing in the text distinguishes them, and
            # only a street register could. Split it so a human has something
            # to confirm, but never call it clean -- a fabricated split sends a
            # national listing to the wrong door while reporting OK.
            number = trailing.group(1).strip()
            street_segment = street_segment[: trailing.start()].strip(" ,")
            ambiguous_number = True

    if not street_segment:
        # The segment was nothing but a number. Keep neither: a bare number in
        # `street` is worse than an empty one.
        return _result(fields, NO_STREET, segments[postcode_index - 1])

    if _same_place(street_segment, venue_name):
        # What is left once the number came off is the venue, so the number was
        # never a house number and there is no street here at all.
        return _result(fields, NO_STREET, segments[postcode_index - 1])

    fields["address_street"] = street_segment
    if number:
        fields["address_number"] = number

    # Anything that is not the venue name and did not become a component is
    # unexplained: lines above the street, and anything after the postcode
    # (access instructions like "Rear entrance" live there). Report it rather
    # than dropping it -- once the structured fields take over, whatever is not
    # carried across stops being published.
    leftover = [
        segment
        for index, segment in enumerate(segments)
        if index not in (postcode_index, postcode_index - 1)
        and not _same_place(segment, venue_name)
    ]
    if leftover:
        return _result(fields, EXTRA_TEXT, " | ".join(leftover))

    if ambiguous_number:
        return _result(fields, CHECK_NUMBER, street_segment)

    # A house number is proof enough that this is a street. Without one, the
    # line has to be recognisable as a street on its own -- "Rear entrance" sits
    # exactly where a street would and reads exactly like one to a parser that
    # only checks position.
    if not number and not _looks_like_a_street(street_segment):
        return _result(fields, CHECK_STREET, street_segment)

    return _result(fields, (OK if number else NO_NUMBER), "")


class Command(BaseCommand):
    help = "Fill structured address fields from the legacy free-text address."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report, write nothing.",
        )
        parser.add_argument(
            "--event-id",
            type=int,
            help="Backfill a single event.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-parse events that already have structured data.",
        )
        parser.add_argument(
            "--audit",
            action="store_true",
            help=(
                "Report only: published events whose structured address is "
                "incomplete. Exits non-zero if any are found, so it can gate a "
                "deploy that turns on validation."
            ),
        )

    def handle(self, *args, **options):
        if options["audit"]:
            return self._audit()

        events = MeetupEvent.objects.all().order_by("pk")
        # `is not None`, not truthiness: `--event-id 0` would otherwise fall
        # through and backfill every event, on a command that writes by default.
        if options["event_id"] is not None:
            events = events.filter(pk=options["event_id"])
            if not events.exists():
                raise CommandError(f"No event with id {options['event_id']}.")

        counts = {}
        written = 0
        for event in events:
            already = any(getattr(event, name) for name in ADDRESS_FIELDS)
            if already and not options["force"]:
                continue

            fields, status, note = parse_legacy_address(event.address, event.location)
            counts[status] = counts.get(status, 0) + 1

            if fields and status in WRITABLE_STATUSES and not options["dry_run"]:
                # Always write all four, never just the ones that parsed.
                #
                # A row only reaches here with either no structured data at all
                # (the default path skips rows that have some) or `--force`,
                # which means "reparse from the legacy text". In both cases the
                # parse owns the whole address. Writing only the parsed keys
                # would leave a --force reparse mixing a fresh postcode with a
                # street from a previous run -- an address that never existed
                # in any single source.
                # `queryset.update()`, not `event.save()`, and deliberately.
                #
                # These four columns are in `_TICKET_PAYLOAD_BASE_FIELDS`, so a
                # save fans out an Apple Wallet refresh to every pass holder --
                # and, with echo.lu enabled, an inline sync per event. Over a
                # back catalogue that is an APNs storm and a pile of synchronous
                # HTTP, to publish an address that renders the same as before:
                # this command transcribes what the legacy text already said.
                #
                # Passes pick the structured address up on the event's next real
                # edit, which is also when it might actually have changed.
                with transaction.atomic():
                    MeetupEvent.objects.filter(pk=event.pk).update(
                        **{name: fields.get(name, "") for name in ADDRESS_FIELDS}
                    )
                for name in ADDRESS_FIELDS:
                    setattr(event, name, fields.get(name, ""))
                written += 1

            self._report(event, fields, status, note)

        self.stdout.write("")
        for status, count in sorted(counts.items()):
            self.stdout.write(f"  {status:<12} {count}")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run — nothing was written."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nUpdated {written} event(s)."))

        unresolved = sum(
            count for status, count in counts.items() if status not in CLEAN_STATUSES
        )
        if unresolved:
            self.stdout.write(
                self.style.WARNING(
                    f"{unresolved} event(s) need a human — fix them in the admin, "
                    "then re-run. The legacy text is untouched, so nothing is lost."
                )
            )

    def _report(self, event, fields, status, note):
        style = self.style.SUCCESS if status in CLEAN_STATUSES else self.style.WARNING
        source = " / ".join(_segments(event.address)) or "(blank)"
        self.stdout.write(style(f"[{event.pk}] {status}  {event.title}"))
        self.stdout.write(f"      from: {source}")
        if fields:
            self.stdout.write(
                "      ->    "
                + "  ".join(
                    f"{k.removeprefix('address_')}={v!r}" for k, v in fields.items()
                )
            )
        if note:
            self.stdout.write(f"      note: {note}")

    def _audit(self):
        """What still needs a person, and whether validation can be switched on."""
        # Presence is not enough. A row can hold a truthy but invalid postcode
        # ("ABCD", or Unicode digits) which the model's RegexValidator rejects
        # on the next validated save -- and the normal backfill skips it,
        # because it already has structured data. Checking only truthiness let
        # --audit report all-clear on precisely the rows the validation it
        # gates would refuse.
        incomplete = [
            event
            for event in MeetupEvent.objects.filter(is_published=True).order_by("pk")
            if event.unmet_publish_requirements()
        ]
        valid_cantons = {value for value, _label in MeetupEvent.CANTON_CHOICES}
        # A blank canton counts as stray on a PUBLISHED event: `clean()` has
        # required one there since long before this work, so leaving it out
        # would let the audit report all-clear on a row the very validation it
        # gates would reject. Unpublished events may legitimately have none.
        stray_cantons = list(
            MeetupEvent.objects.exclude(canton__in=valid_cantons)
            .exclude(canton="", is_published=False)
            .values_list("pk", "canton")
            .order_by("pk")
        )

        for event in incomplete:
            self.stdout.write(
                self.style.WARNING(f"[{event.pk}] incomplete address — {event.title}")
            )
        for pk, canton in stray_cantons:
            self.stdout.write(
                self.style.WARNING(f"[{pk}] canton not in the list — {canton!r}")
            )

        if not incomplete and not stray_cantons:
            self.stdout.write(
                self.style.SUCCESS(
                    "Every published event has a complete structured address, and "
                    "every canton is a recognised one."
                )
            )
            return

        raise CommandError(
            f"{len(incomplete)} published event(s) with an incomplete address, "
            f"{len(stray_cantons)} unrecognised canton(s)."
        )
