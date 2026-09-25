"""
Shared lookup for the CLI journey builders (create_wonderland_journey,
create_advent_calendar): pick the SpecialUserExperience a journey goes on
without ever reusing a namesake's LINKED row by name.

A gift claim or a Crush Spark creates a row with ``linked_user`` set and the
member's own first and last name copied onto it. A plain name lookup would
therefore find that row for anyone with the same name, reactivate it and
overwrite its journey - handing an admin-built private journey to the wrong
account and destroying the member's own gift journey on the way. Django skips
this module as a command because its name starts with an underscore.
"""

from django.core.management.base import CommandError

from crush_lu.models import SpecialUserExperience


def add_experience_id_argument(parser):
    parser.add_argument(
        "--experience-id",
        type=int,
        default=None,
        help=(
            "Build the journey on this Special User Experience (any link "
            "state), for example a member's own gift experience. Without it "
            "the command only ever reuses or creates an UNLINKED row with the "
            "given name and never touches a linked one."
        ),
    )


def resolve_experience(
    command, *, first_name, last_name, experience_id, defaults, update_existing
):
    """Return ``(experience, created)`` for a CLI journey builder.

    ``experience_id`` targets exactly that row. Otherwise the UNLINKED row
    with this exact name is reused (or one is created); rows with the same
    name that are linked to an account are listed with the ``--experience-id``
    to pass if the journey is meant for that account, and left untouched.
    ``defaults`` are applied on create and, with ``update_existing``, on the
    reused row too (``update_or_create`` semantics).
    """
    if experience_id is not None:
        try:
            experience = SpecialUserExperience.objects.get(pk=experience_id)
        except SpecialUserExperience.DoesNotExist:
            raise CommandError(
                f"No Special User Experience with id {experience_id}."
            ) from None
        if update_existing:
            _apply(experience, defaults)
        return experience, False

    linked_rows = (
        SpecialUserExperience.objects.filter(
            first_name=first_name, last_name=last_name, linked_user__isnull=False
        )
        .select_related("linked_user")
        .order_by("pk")
    )
    for row in linked_rows:
        user = row.linked_user
        command.stdout.write(
            command.style.WARNING(
                f"[!] Experience #{row.pk} ({first_name} {last_name}) is linked"
                f" to user #{user.pk} <{user.email or user.username}> and is"
                " not touched. If this journey is for that account, rerun with"
                f" --experience-id {row.pk}."
            )
        )

    # unique_name_when_no_linked_user: at most one unlinked row per name.
    experience = SpecialUserExperience.objects.filter(
        first_name=first_name, last_name=last_name, linked_user__isnull=True
    ).first()
    if experience is None:
        experience = SpecialUserExperience.objects.create(
            first_name=first_name, last_name=last_name, **defaults
        )
        return experience, True
    if update_existing:
        _apply(experience, defaults)
    return experience, False


def _apply(experience, defaults):
    for field, value in defaults.items():
        setattr(experience, field, value)
    experience.save()
