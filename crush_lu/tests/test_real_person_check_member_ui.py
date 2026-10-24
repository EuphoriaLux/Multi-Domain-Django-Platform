"""What other members see of a Connect real-person check (PR 4).

The coach-verified badge component and the cards, inbox, chat header and hub
that show it; the trust label; the hub note; and the query cost of all of it.
The badge itself (precedence, validity, photo bookkeeping) is in
``test_real_person_check_badge.py``.

Requests use literal ``/en/`` paths (AGENTS.md: ``reverse()`` resolves the
default urlconf, not the one the host selects). The rows a badge needs are
written directly: cohort and guard rules are the services' business and are
covered by their own tests.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import html as html_lib
import itertools
import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from crush_lu.models import (
    ConnectCycleCard,
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    ConnectWeekSession,
    CrushCoach,
    CrushProfile,
    UserReport,
)
from crush_lu.real_person_check_rules import DECLINE_COOLDOWN
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
    set_photo,
)
from crush_lu.tests.test_connect_chat_flows import _make_open_chat
from crush_lu.tests.test_connect_week_experience import (
    WEEK_HOME_URL,
    WEEK_INBOX_URL,
    WEEK_REVIEW_URL,
    _make_cycle_user,
    _reviewable_session_with_card,
)
from crush_lu.tests.test_crush_connect import (
    HUB_URL,
    _coach_for,
    _login_eligible,
    _make_user,
    _set_gate_questions,
)

pytestmark = pytest.mark.django_db

PROFILE_URL = "/en/crush-connect/profile/"
COACH_PICK_URL = "/en/crush-connect/coach-pick/"

# The badge div exactly as _cycle_card.html and _candidate_card.html rendered
# it before the component existed (HEAD, EN, title and label resolved).
LEGACY_IN_PERSON = """
        <div class="absolute top-3 right-3 z-10">
            <span class="inline-flex items-center gap-1.5 rounded-full bg-black/60 backdrop-blur-md px-2.5 py-1 text-xs font-semibold text-white ring-1 ring-white/20 shadow-sm" title="Photo verified in person by a Crush Coach">
                <svg class="w-3.5 h-3.5 text-emerald-400" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>
                In-person photo verified
            </span>
        </div>
"""

# The wrapper the video and voice pills sit in on the photo overlay.
CHECK_OVERLAY_CLASS = "absolute top-11 right-3 z-10 max-w-[calc(100%_-_1.5rem)]"

LABELS = {
    "en": {
        "in_person": "In-person photo verified",
        "video": "Video-verified by a coach",
        "voice": "Spoke with a Crush coach",
    },
    "de": {
        "in_person": "Foto persönlich verifiziert",
        "video": "Per Video von einem Coach verifiziert",
        "voice": "Hat mit einem Crush Coach gesprochen",
    },
    "fr": {
        "in_person": "Photo vérifiée en personne",
        "video": "Vérifié en vidéo par un coach",
        "voice": "A parlé avec un coach Crush",
    },
}

HUB_NOTE = {
    "en": (
        "To confirm every Connect member is a real person, a Crush coach may text "
        "you and suggest a short video or voice call. It's optional: it adds a "
        "badge to your card and changes nothing else."
    ),
    "de": (
        "Damit wir sicher sind, dass jedes Connect-Mitglied eine echte Person ist, "
        "kann dir ein Crush Coach schreiben und einen kurzen Video- oder "
        "Sprachanruf vorschlagen. Das ist freiwillig: Deine Karte bekommt ein "
        "Abzeichen, sonst ändert sich nichts."
    ),
    "fr": (
        "Pour confirmer que chaque membre de Connect est une personne réelle, un "
        "coach Crush peut vous envoyer un message et vous proposer un court appel "
        "vidéo ou vocal. C'est facultatif : cela ajoute un badge à votre carte et "
        "ne change rien d'autre."
    ),
}

# Every label the new surfaces can print, English: none may leak while off.
NEW_ENGLISH_LABELS = (LABELS["en"]["video"], LABELS["en"]["voice"])


@pytest.fixture(autouse=True)
def _setup(settings):
    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True


@pytest.fixture
def flags_on(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True


@pytest.fixture
def flags_off(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False


def squash(markup):
    """Markup with insignificant whitespace removed (includes add blank lines)."""
    markup = re.sub(r">\s+<", "><", markup)
    return re.sub(r"\s+", " ", markup).strip()


def text_of(response):
    return html_lib.unescape(response.content.decode())


def grant(user, medium):
    """A granted badge for ``user``, written directly."""
    profile = CrushProfile.objects.get(user=user)
    coach = CrushCoach.objects.filter(user__username="rpc_ui_grantor").first()
    coach = coach or make_coach("rpc_ui_grantor")
    key = profile.photo_1.name if medium == "video" else ""
    check = ConnectRealPersonCheck.objects.create(
        profile=profile,
        coach=coach,
        status="passed",
        medium=medium,
        photo_key=key,
        decided_at=timezone.now(),
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile,
        source_check=check,
        medium=medium,
        photo_key=key,
        profile_approved_at=profile.approved_at,
    )


def door_verify(user):
    """The door badge: a coach attested the current photo_1."""
    profile = profile_of(user)
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_verification_key=profile.photo_1.name
    )
    return user


def decorate(user, kind):
    if kind == "in_person":
        door_verify(user)
    elif kind:
        grant(user, kind)
    return user


def component(profile, variant=None, **extra):
    context = {"profile": profile, **extra}
    if variant:
        context["variant"] = variant
    return render_to_string("crush_lu/components/coach_verified_badge.html", context)


def squashed_labels(markup):
    return [label for label in NEW_ENGLISH_LABELS if label in markup]


COACH_REVIEWED_PILL = re.compile(
    r'<div class="absolute (top-\d+) left-3 z-10">\s*'
    r'<span class="inline-flex items-center rounded-full bg-black/70 '
)


def coach_reviewed_rows(markup):
    """Top offset of every "Coach-reviewed photo" pill on a card photo.

    Matched on its classes, not its title: the title is translated.
    """
    return sorted(COACH_REVIEWED_PILL.findall(markup))


# ---------------------------------------------------------------------------
# The component
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags", ["off", "on"])
def test_the_in_person_pill_is_the_markup_it_always_was(settings, flags):
    """Same element, classes, icon and text as HEAD, whitespace aside.

    Not byte-identical: the component's ``{% with %}``/``{% if %}`` wrapper adds
    blank lines and re-indents the div, so the comparison collapses whitespace
    (``squash``) on both sides. Nothing downstream is whitespace-sensitive.
    """
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = flags == "on"
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = flags == "on"
    member = door_verify(make_member("rpc_ui_door"))

    assert squash(component(profile_of(member))) == squash(LEGACY_IN_PERSON)


def test_the_in_person_pill_is_untouched_by_a_granted_badge(flags_on):
    member = make_member("rpc_ui_door_and_video")
    grant(member, "video")
    door_verify(member)

    assert squash(component(profile_of(member))) == squash(LEGACY_IN_PERSON)


def test_no_badge_renders_nothing(flags_on):
    profile = profile_of(make_member("rpc_ui_none"))

    assert component(profile).strip() == ""
    assert (
        component(profile, "inline", connect_real_person_badge_visible=True).strip()
        == ""
    )


@pytest.mark.parametrize("kind", ["video", "voice"])
def test_a_check_badge_renders_its_pill(flags_on, kind):
    member = decorate(make_member(f"rpc_ui_{kind}"), kind)
    body = squash(component(profile_of(member)))

    assert LABELS["en"][kind] in body
    assert "In-person photo verified" not in body
    # A sibling of the in-person pill: the same dark pill, one row below it.
    assert f'class="{CHECK_OVERLAY_CLASS}"' in body
    assert "bg-black/60 backdrop-blur-md" in body
    assert "<svg" in body
    # The tooltip says what the coach did, in English here.
    assert 'title="A Crush coach ' in body


def test_the_two_check_pills_have_their_own_icon_and_tooltip(flags_on):
    video = squash(component(profile_of(decorate(make_member("rpc_ui_v"), "video"))))
    voice = squash(component(profile_of(decorate(make_member("rpc_ui_p"), "voice"))))

    assert "on a live video call" in video and "on a live video call" not in voice
    assert "spoke with this member on a call" in voice
    assert video != voice


@pytest.mark.parametrize("kind", ["video", "voice"])
def test_flags_off_hide_a_granted_badge_in_the_component(flags_off, kind):
    member = decorate(make_member(f"rpc_ui_off_{kind}"), kind)

    assert component(profile_of(member)).strip() == ""


def test_the_badge_flag_alone_shows_nothing_in_the_component(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    member = decorate(make_member("rpc_ui_alone"), "video")

    assert component(profile_of(member)).strip() == ""


@pytest.mark.parametrize("kind", ["in_person", "video", "voice"])
def test_the_inline_variant_needs_the_flag_in_the_context(flags_on, kind):
    profile = profile_of(decorate(make_member(f"rpc_ui_inline_{kind}"), kind))

    assert component(profile, "inline").strip() == ""
    assert (
        component(profile, "inline", connect_real_person_badge_visible=False).strip()
        == ""
    )
    pill = squash(component(profile, "inline", connect_real_person_badge_visible=True))
    assert LABELS["en"][kind] in pill
    assert "absolute" not in pill
    assert "bg-emerald-50" in pill


def test_the_inline_variant_takes_extra_classes(flags_on):
    profile = profile_of(decorate(make_member("rpc_ui_inline_css"), "voice"))
    pill = component(
        profile,
        "inline",
        css_class="self-start",
        connect_real_person_badge_visible=True,
    )

    assert "self-start" in pill


@pytest.mark.parametrize("language", ["en", "de", "fr"])
@pytest.mark.parametrize("kind", ["in_person", "video", "voice"])
def test_every_pill_is_translated(flags_on, language, kind):
    profile = profile_of(decorate(make_member(f"rpc_ui_{language}_{kind}"), kind))
    with translation.override(language):
        overlay = component(profile)
        inline = component(profile, "inline", connect_real_person_badge_visible=True)

    for markup in (overlay, inline):
        assert LABELS[language][kind] in markup
        if language != "en":
            # No English fallback: the label and the tooltip are both translated.
            assert LABELS["en"][kind] not in markup
            title = re.search(r'title="([^"]*)"', markup).group(1)
            assert title and not title.startswith("A Crush")
            assert not title.startswith("Photo verified in person")


# ---------------------------------------------------------------------------
# Week home (_cycle_card)
# ---------------------------------------------------------------------------


def home_world(kinds):
    viewer = _make_cycle_user("rpc_ui_home_viewer")
    targets = []
    for index, kind in enumerate(kinds):
        target = make_member(
            f"rpc_ui_home_{index}", gender="F", preferred_genders=["M"]
        )
        _set_gate_questions(target)
        targets.append(decorate(target, kind))
    return viewer, targets


def logged_in(client, user):
    _login_eligible(client, user)
    return client


def test_week_home_shows_video_and_voice_pills(client, flags_on):
    viewer, _ = home_world(["video", "voice"])
    body = text_of(logged_in(client, viewer).get(WEEK_HOME_URL))

    assert LABELS["en"]["video"] in body
    assert LABELS["en"]["voice"] in body
    assert body.count("data-gate-submit") == 2  # both cards rendered


def test_week_home_shows_the_in_person_pill_with_the_flags_either_way(client, settings):
    viewer, _ = home_world(["in_person", None])
    logged_in(client, viewer)
    for flag in (False, True):
        settings.CONNECT_REAL_PERSON_CHECK_ENABLED = flag
        settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = flag
        body = client.get(WEEK_HOME_URL).content.decode()
        assert squash(LEGACY_IN_PERSON) in squash(body)
        assert body.count("In-person photo verified") == 1


def test_week_home_flags_off_prints_none_of_the_new_markup(client, flags_off):
    viewer, _ = home_world(["video", "voice"])
    body = logged_in(client, viewer).get(WEEK_HOME_URL).content.decode()

    assert body.count("data-gate-submit") == 2
    assert squashed_labels(body) == []
    assert CHECK_OVERLAY_CLASS not in body
    # No badge div of any kind: neither of these members was met at a door.
    assert "absolute top-3 right-3" not in body
    assert "A Crush coach " not in body


def test_week_home_a_replaced_video_photo_shows_the_voice_pill(client, flags_on):
    viewer, (target, _other) = home_world(["video", None])
    set_photo(target, "users/777/photos/replacement.jpg")
    body = text_of(logged_in(client, viewer).get(WEEK_HOME_URL))

    assert LABELS["en"]["voice"] in body
    assert LABELS["en"]["video"] not in body


def test_week_home_is_translated(client, flags_on):
    viewer, _ = home_world(["video", "voice"])
    logged_in(client, viewer)
    for language in ("de", "fr"):
        body = text_of(client.get(f"/{language}/crush-connect/week/"))
        assert LABELS[language]["video"] in body
        assert LABELS[language]["voice"] in body


@pytest.mark.parametrize("flags", ["off", "on"])
@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_the_coach_reviewed_pill_drops_a_row_under_the_in_person_pill(
    client, settings, flags, language
):
    """DE/FR in-person labels ran 25-49 px under the coach-reviewed pill at 375 px.

    The right-hand pill keeps the top row it always had, so the left-hand one
    moves, and only on a card that carries both. Flags do not matter: the door
    badge never did.
    """
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = flags == "on"
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = flags == "on"
    viewer, _ = home_world(["in_person", None])
    body = logged_in(client, viewer).get(f"/{language}/crush-connect/week/")

    assert coach_reviewed_rows(text_of(body)) == ["top-11", "top-3"]


def test_the_coach_reviewed_pill_keeps_its_row_beside_video_and_voice_pills(
    client, flags_on
):
    # Their pills sit on the second row of the right-hand edge; the left-hand
    # pill has the top row to itself, as before.
    viewer, _ = home_world(["video", "voice"])
    body = text_of(logged_in(client, viewer).get(WEEK_HOME_URL))

    assert coach_reviewed_rows(body) == ["top-3", "top-3"]
    assert body.count(CHECK_OVERLAY_CLASS) == 2


def test_week_home_cost_does_not_depend_on_badges(client, settings):
    viewer, targets = home_world([None, None])
    logged_in(client, viewer)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(WEEK_HOME_URL)
        assert response.status_code == 200
        return len(queries), text_of(response)

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    cost()  # generates the cards, warms per-process caches
    off_plain, _ = cost()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    on_plain, _ = cost()
    grant(targets[0], "video")
    grant(targets[1], "voice")
    on_badged, body = cost()

    assert LABELS["en"]["video"] in body and LABELS["en"]["voice"] in body
    assert off_plain == on_plain == on_badged


# ---------------------------------------------------------------------------
# Weekly review (_review_card, inline)
# ---------------------------------------------------------------------------


def review_world(kinds, session=None):
    """A viewer in the 24 h review window with a completed card per kind.

    Pass ``session`` to add more cards to the same review.
    """
    if session is None:
        session = ConnectWeekSession.objects.create(
            user=_make_cycle_user("rpc_ui_review_viewer")
        )
        session.open_weekly_review()
    for kind in kinds:
        index = session.cards.count()
        target = make_member(
            f"rpc_ui_review_{index}", gender="F", preferred_genders=["M"]
        )
        decorate(target, kind)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=index // 3 + 1,
            card_index=index % 3 + 1,
            target_user=target,
            generated_date=timezone.localdate(),
            is_completed=True,
            completed_at=timezone.now(),
        )
    return session.user, session


def test_the_review_shows_inline_pills_in_the_card_body(client, flags_on):
    viewer, _ = review_world(["video", "voice", "in_person", None])
    body = text_of(logged_in(client, viewer).get(WEEK_REVIEW_URL))

    assert body.count(LABELS["en"]["video"]) == 1
    assert body.count(LABELS["en"]["voice"]) == 1
    assert body.count(LABELS["en"]["in_person"]) == 1
    # Inline: not the dark photo overlay, and aligned in the flex column.
    assert "self-start" in body and "bg-emerald-50" in body
    assert "absolute top-3 right-3" not in body


def test_the_review_is_unchanged_with_the_flags_off(client, flags_off):
    """In-person never showed on the review card, so it still must not."""
    viewer, _ = review_world(["video", "voice", "in_person", None])
    body = text_of(logged_in(client, viewer).get(WEEK_REVIEW_URL))

    assert body.count("<article") == 4
    assert squashed_labels(body) == []
    assert LABELS["en"]["in_person"] not in body
    assert "bg-emerald-50" not in body


def test_the_review_is_translated(client, flags_on):
    viewer, _ = review_world(["video"])
    body = text_of(logged_in(client, viewer).get("/de/crush-connect/week/review/"))

    assert LABELS["de"]["video"] in body


def test_review_cost_does_not_grow_with_badged_cards(client, flags_on):
    viewer, session = review_world([None, None])
    logged_in(client, viewer)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(WEEK_REVIEW_URL)
        assert response.status_code == 200
        return len(queries), text_of(response)

    cost()
    small, _ = cost()
    review_world(["video", "voice", "video", "voice"], session=session)
    large, body = cost()

    assert body.count("<article") == 6
    assert body.count(LABELS["en"]["video"]) == 2
    assert body.count(LABELS["en"]["voice"]) == 2
    assert large == small


def test_both_card_queryset_branches_load_every_badge(
    flags_on, django_assert_num_queries
):
    from crush_lu.services.connect_cycle import get_review_cards, visible_cycle_cards

    viewer, session = review_world(["video", "voice", None])

    queryset_branch = get_review_cards(session)  # a QuerySet inside
    list_branch = visible_cycle_cards(list(session.cards.all()), viewer)
    for cards in (queryset_branch, list_branch):
        assert len(cards) == 3
        with django_assert_num_queries(0):
            kinds = sorted(
                card.target_user.crushprofile.real_person_badge for card in cards
            )
        assert kinds == ["", "video", "voice"]


# ---------------------------------------------------------------------------
# Request inbox (_permitted_profile)
# ---------------------------------------------------------------------------

_requester_numbers = itertools.count()


def inbox_world(kinds):
    """A recipient with one pending request from a requester of each kind."""
    from crush_lu.services.connect_cycle import send_weekly_request

    recipient = _make_cycle_user("rpc_ui_recipient")
    requesters = []
    for kind in kinds:
        requester = _make_cycle_user(f"rpc_ui_requester_{next(_requester_numbers)}")
        session, _card = _reviewable_session_with_card(requester, recipient)
        send_weekly_request(session, requester, recipient)
        requesters.append(decorate(requester, kind))
    return recipient, requesters


def test_the_inbox_shows_the_requesters_badge(client, flags_on):
    recipient, _ = inbox_world(["video", "voice", None])
    body = text_of(logged_in(client, recipient).get(WEEK_INBOX_URL))

    assert body.count(LABELS["en"]["video"]) == 1
    assert body.count(LABELS["en"]["voice"]) == 1
    assert body.count("bg-emerald-50") == 2


def test_the_inbox_prints_none_of_it_with_the_flags_off(client, flags_off):
    recipient, requesters = inbox_world(["video", "voice"])
    body = text_of(logged_in(client, recipient).get(WEEK_INBOX_URL))

    assert all(requester.first_name in body for requester in requesters)
    assert squashed_labels(body) == []
    assert "bg-emerald-50" not in body


def test_the_inbox_cost_does_not_depend_on_badges(client, settings):
    recipient, requesters = inbox_world([None, None, None])
    logged_in(client, recipient)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(WEEK_INBOX_URL)
        assert response.status_code == 200
        return len(queries), text_of(response)

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    cost()
    off_plain, _ = cost()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    on_plain, _ = cost()
    for requester, kind in zip(requesters, ["video", "voice", "video"]):
        grant(requester, kind)
    on_badged, body = cost()

    assert body.count(LABELS["en"]["video"]) == 2
    assert body.count(LABELS["en"]["voice"]) == 1
    assert off_plain == on_plain == on_badged


def test_the_inbox_query_loads_every_badge(flags_on, django_assert_num_queries):
    from crush_lu.services.connect_cycle import get_pending_inbox

    recipient, _ = inbox_world(["video", "voice", None])
    requests = get_pending_inbox(recipient)

    assert len(requests) == 3
    with django_assert_num_queries(0):
        kinds = sorted(r.requester.crushprofile.real_person_badge for r in requests)
    assert kinds == ["", "video", "voice"]


# ---------------------------------------------------------------------------
# The member's own preview (_permitted_profile in profile edit)
# ---------------------------------------------------------------------------


def test_the_members_own_preview_shows_their_badge(client, flags_on):
    member = decorate(make_member("rpc_ui_own"), "video")
    body = text_of(logged_in(client, member).get(PROFILE_URL))

    assert LABELS["en"]["video"] in body


def test_the_members_own_preview_shows_none_with_the_flags_off(client, flags_off):
    member = decorate(make_member("rpc_ui_own_off"), "video")
    body = text_of(logged_in(client, member).get(PROFILE_URL))

    assert "Preview your discovery card" in body
    assert squashed_labels(body) == []


# ---------------------------------------------------------------------------
# Coach's Pick and the dev preview (_candidate_card)
# ---------------------------------------------------------------------------


def pick_world(kind):
    from crush_lu.services.crush_connect import propose_coach_pick

    member = _make_user(username="rpc_ui_pick_member", preferred_genders=["F"])
    candidate = make_member("rpc_ui_pick_candidate", gender="F")
    decorate(candidate, kind)
    propose_coach_pick(_coach_for(member), member, candidate)
    return member, candidate


def test_coach_pick_shows_the_candidates_overlay_pill(client, flags_on):
    member, _ = pick_world("voice")
    body = text_of(logged_in(client, member).get(COACH_PICK_URL))

    assert LABELS["en"]["voice"] in body
    assert f'class="{CHECK_OVERLAY_CLASS}"' in body


def test_coach_pick_keeps_the_in_person_pill_with_the_flags_off(client, flags_off):
    member, _ = pick_world("in_person")
    body = logged_in(client, member).get(COACH_PICK_URL).content.decode()

    assert squash(LEGACY_IN_PERSON) in squash(body)


def test_coach_pick_moves_the_coach_reviewed_pill_under_the_in_person_pill(
    client, flags_off
):
    member, _ = pick_world("in_person")
    body = text_of(logged_in(client, member).get(COACH_PICK_URL))

    assert coach_reviewed_rows(body) == ["top-11"]


def test_coach_pick_keeps_the_coach_reviewed_pill_on_top_otherwise(client, flags_on):
    member, _ = pick_world("voice")
    body = text_of(logged_in(client, member).get(COACH_PICK_URL))

    assert coach_reviewed_rows(body) == ["top-3"]


def test_coach_pick_flags_off_hides_a_granted_badge(client, flags_off):
    member, _ = pick_world("video")
    body = text_of(logged_in(client, member).get(COACH_PICK_URL))

    assert squashed_labels(body) == []


def test_coach_pick_loads_the_badge_with_the_pick(flags_on, django_assert_num_queries):
    from crush_lu.services.crush_connect import get_active_coach_pick

    member, _ = pick_world("video")
    pick = get_active_coach_pick(member)

    with django_assert_num_queries(0):
        assert pick.candidate.crushprofile.real_person_badge == "video"


def test_coach_pick_page_costs_the_same_with_the_flags_on(client, settings):
    member, _candidate = pick_world("video")
    logged_in(client, member)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(COACH_PICK_URL)
        assert response.status_code == 200
        return len(queries), text_of(response)

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    cost()
    off, _ = cost()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    on, body = cost()

    assert LABELS["en"]["video"] in body
    assert on == off


def test_the_staff_dev_preview_shows_the_overlay_pill(client, flags_on):
    from django.contrib.auth import get_user_model

    target = decorate(make_member("rpc_ui_preview_target"), "video")
    staff = get_user_model().objects.create_user(
        "rpc_ui_staff", "staff@example.com", "pw12345678", is_staff=True
    )
    _login_eligible(client, staff)  # consent, or the middleware turns staff away

    body = text_of(client.get(f"/en/dev/connect-card/{target.pk}/"))

    assert LABELS["en"]["video"] in body


# ---------------------------------------------------------------------------
# Connect Showcase (_candidate_card, showcase=True)
# ---------------------------------------------------------------------------


@pytest.fixture
def showcase_event():
    from datetime import timedelta

    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Autumn mixer",
        description="",
        event_type="mixer",
        location="Atmos",
        date_time=timezone.now() + timedelta(hours=1),
        registration_deadline=timezone.now(),
        duration_minutes=180,
        max_participants=60,
        is_published=True,
    )


def showcase_cost(client, event):
    from crush_lu.tests.test_connect_showcase import _deck_url

    cache.clear()
    with CaptureQueriesContext(connection) as queries:
        response = client.get(_deck_url(event, show=1))
    assert response.status_code == 200
    return len(queries), text_of(response)


def test_the_showcase_deck_cost_does_not_grow_with_badged_cards(
    client, flags_on, showcase_event, monkeypatch
):
    from crush_lu.tests.test_connect_showcase import _member
    from crush_lu.tests.test_coach_photo_review import _make_coach

    for field in ("photo_1", "photo_2", "photo_3"):
        monkeypatch.setattr(
            CrushProfile._meta.get_field(field).storage, "delete", lambda name: None
        )
    coach = _make_coach("rpc_ui_showcase_coach")
    client.force_login(coach.user)
    for number in range(2):
        _member(f"rpc_ui_deck_small_{number}")
    showcase_cost(client, showcase_event)
    small, _ = showcase_cost(client, showcase_event)
    for number in range(2):
        grant(_member(f"rpc_ui_deck_video_{number}"), "video")
    for number in range(2):
        grant(_member(f"rpc_ui_deck_voice_{number}"), "voice")
    large, body = showcase_cost(client, showcase_event)

    assert body.count("data-showcase-card") == 6
    assert body.count(LABELS["en"]["video"]) == 2
    assert body.count(LABELS["en"]["voice"]) == 2
    assert large == small


# ---------------------------------------------------------------------------
# Chat header
# ---------------------------------------------------------------------------


def chat_world(kind):
    me = _make_cycle_user("rpc_ui_chat_me")
    partner = decorate(_make_cycle_user("rpc_ui_chat_partner"), kind)
    me, partner, chat = _make_open_chat(me, partner)
    return me, partner, chat, f"/en/crush-connect/week/chats/{chat.pk}/"


@pytest.mark.parametrize("kind", ["video", "voice", "in_person"])
def test_the_chat_header_shows_the_partners_badge(client, flags_on, kind):
    me, _partner, _chat, url = chat_world(kind)
    body = text_of(logged_in(client, me).get(url))

    assert LABELS["en"][kind] in body


def test_the_chat_header_shows_no_badge_of_the_viewer(client, flags_on):
    me, partner, _chat, url = chat_world(None)
    decorate(me, "video")
    body = text_of(logged_in(client, me).get(url))

    assert squashed_labels(body) == []


def test_the_chat_header_is_unchanged_with_the_flags_off(client, flags_off):
    me, _partner, _chat, url = chat_world("in_person")
    decorate(me, "video")
    body = text_of(logged_in(client, me).get(url))

    assert squashed_labels(body) == []
    assert LABELS["en"]["in_person"] not in body


def test_the_chat_header_hides_the_badge_beside_a_hidden_photo(client, flags_on):
    """A badge vouching for a photo this viewer may not see would mislead."""
    me, partner, _chat, url = chat_world("video")
    membership = partner.crush_connect_membership
    membership.photo_share_consent = False
    membership.save(update_fields=["photo_share_consent"])
    body = text_of(logged_in(client, me).get(url))

    assert squashed_labels(body) == []


def test_chat_detail_costs_the_same_with_the_flags_on(client, settings):
    me, partner, _chat, url = chat_world("video")
    logged_in(client, me)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url)
        assert response.status_code == 200
        return len(queries), text_of(response)

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    cost()
    off, _ = cost()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    on, body = cost()

    assert LABELS["en"]["video"] in body
    assert on == off


def test_the_chat_send_and_poll_endpoints_skip_the_badge_join(client, flags_on):
    me, _partner, chat, _url = chat_world("video")
    logged_in(client, me)
    with CaptureQueriesContext(connection) as queries:
        assert (
            client.get(f"/en/crush-connect/week/chats/{chat.pk}/messages/").status_code
            == 200
        )

    assert not [q for q in queries.captured_queries if "realpersonbadge" in q["sql"]]


# ---------------------------------------------------------------------------
# Trust label (hub readiness card)
# ---------------------------------------------------------------------------


def trust(user):
    from crush_lu.views_crush_connect import _connect_trust_status

    return _connect_trust_status(profile_of(user))


def test_a_luxid_member_with_a_video_badge_gets_the_video_kind(flags_on):
    member = decorate(make_member("rpc_ui_trust_video"), "video")
    status = trust(member)

    assert status["kind"] == "luxid_video"
    assert "video call" in str(status["label"])


def test_a_luxid_member_with_a_voice_badge_gets_the_voice_kind(flags_on):
    member = decorate(make_member("rpc_ui_trust_voice"), "voice")

    assert trust(member)["kind"] == "luxid_voice"


def test_a_replaced_video_photo_is_the_voice_kind(flags_on):
    member = decorate(make_member("rpc_ui_trust_replaced"), "video")
    set_photo(member, "users/777/photos/replacement.jpg")

    assert trust(member)["kind"] == "luxid_voice"


def test_a_luxid_member_without_a_badge_keeps_the_luxid_kind(flags_on):
    assert trust(make_member("rpc_ui_trust_plain"))["kind"] == "luxid"


@pytest.mark.parametrize("check,badge", [(False, False), (True, False), (False, True)])
def test_the_trust_kinds_need_both_flags(settings, check, badge):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = check
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = badge
    member = decorate(make_member("rpc_ui_trust_flags"), "video")

    assert trust(member)["kind"] == "luxid"


def test_double_verification_is_unchanged_by_a_badge(flags_on):
    member = decorate(make_member("rpc_ui_trust_double"), "video")
    _attend(member)

    assert trust(member)["kind"] == "double"


def test_event_verification_is_unchanged_by_a_badge(flags_on):
    member = decorate(make_member("rpc_ui_trust_event", has_luxid=False), "video")
    _attend(member)

    assert trust(member)["kind"] == "event"


def test_a_badge_alone_without_luxid_adds_no_label(flags_on):
    member = decorate(make_member("rpc_ui_trust_noluxid", has_luxid=False), "video")

    assert trust(member) is None


def _attend(user):
    from crush_lu.tests.test_crush_connect import _mark_attended

    _mark_attended(user)


@pytest.mark.parametrize("kind", ["luxid_video", "luxid_voice"])
def test_the_new_explanations_still_say_an_event_unlocks_the_journey(flags_on, kind):
    medium = kind.split("_")[1]
    member = decorate(make_member(f"rpc_ui_trust_text_{medium}"), medium)
    explanation = str(trust(member)["explanation"])

    # The badge never unlocks Connect Week: only taking part in an event does.
    assert "Taking part in a Crush event unlocks the active Connect journey" in (
        explanation
    )
    assert "up to two new profiles a day" in explanation
    assert "unlock" not in explanation.split("Taking part")[0]


def test_the_hub_prints_the_new_kinds_in_all_three_languages(client, flags_on):
    video = decorate(make_member("rpc_ui_hub_video"), "video")
    logged_in(client, video)
    expected = {
        "en": ("Identity confirmed by LuxID and a coach video call", "video call"),
        "de": ("Identität durch LuxID und einen Videoanruf mit einem Coach", "dich"),
        "fr": ("Identité confirmée par LuxID et un appel vidéo avec un coach", "vous"),
    }
    for language, (label, marker) in expected.items():
        body = text_of(client.get(f"/{language}/crush-connect/home/"))
        assert 'data-trust-status="luxid_video"' in body
        assert label in body
        assert marker in body


def test_readiness_counts_are_the_same_with_and_without_a_badge(settings):
    from crush_lu.views_crush_connect import _connect_readiness

    member = make_member("rpc_ui_readiness")

    def readiness():
        # Reload: make_member moved photo_1 in the database behind the cached profile.
        return _connect_readiness(type(member).objects.get(pk=member.pk))

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    before = readiness()
    grant(member, "video")
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    after = readiness()

    for key in ("completed_count", "total_count", "is_complete"):
        assert before[key] == after[key]
    assert [(s["key"], s["complete"]) for s in before["steps"]] == [
        (s["key"], s["complete"]) for s in after["steps"]
    ]
    assert before["trust_status"]["kind"] == "luxid"
    assert after["trust_status"]["kind"] == "luxid_video"


# ---------------------------------------------------------------------------
# Hub note
# ---------------------------------------------------------------------------


def hub_text(client, member, language="en"):
    return text_of(logged_in(client, member).get(f"/{language}/crush-connect/home/"))


def test_the_note_shows_for_a_cohort_member_without_a_badge(client, flags_on):
    member = make_member("rpc_ui_note")
    body = hub_text(client, member)

    assert "data-real-person-check-note" in body
    assert HUB_NOTE["en"] in body
    # It sits beside the readiness card, not inside it.
    assert 'id="connect-readiness-title"' in body


@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_the_note_copy_is_the_approved_text(client, flags_on, language):
    member = make_member(f"rpc_ui_note_{language}")

    assert HUB_NOTE[language] in hub_text(client, member, language)


@pytest.mark.parametrize(
    "check,badge",
    [(False, False), (True, False), (False, True)],
    ids=["both_off", "badge_flag_off", "tooling_flag_off"],
)
def test_the_note_needs_both_flags(client, settings, check, badge):
    """The note promises a badge, so it shows only once members can see one."""
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = check
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = badge
    member = make_member("rpc_ui_note_flags")

    assert "data-real-person-check-note" not in hub_text(client, member)


def test_the_note_is_for_members_outside_the_coach_attested_group(client, flags_on):
    attended = _make_cycle_user("rpc_ui_note_attended")

    assert "data-real-person-check-note" not in hub_text(client, attended)


@pytest.mark.parametrize("kind", ["video", "voice"])
def test_the_note_goes_once_a_badge_is_granted(client, flags_on, kind):
    member = decorate(make_member(f"rpc_ui_note_{kind}"), kind)

    assert "data-real-person-check-note" not in hub_text(client, member)


def test_the_note_returns_when_a_badge_stops_being_valid(client, flags_on):
    member = decorate(make_member("rpc_ui_note_revoked"), "voice")
    CrushProfile.objects.filter(user=member).update(approved_at=timezone.now())

    assert "data-real-person-check-note" in hub_text(client, member)


def test_the_note_ignores_the_thirty_day_login_window(client, flags_on):
    member = make_member("rpc_ui_note_stale", last_login_days_ago=200)

    assert "data-real-person-check-note" in hub_text(client, member)


def test_the_note_is_not_for_someone_outside_the_catalogue(client, flags_on):
    member = make_member("rpc_ui_note_paused")
    membership = member.crush_connect_membership
    membership.paused_at = timezone.now()
    membership.save(update_fields=["paused_at"])

    assert "data-real-person-check-note" not in hub_text(client, member)


def decline(member, *, days_ago):
    """A coach's recorded "declined" for ``member``, ``days_ago`` days old."""
    coach = CrushCoach.objects.filter(user__username="rpc_ui_decliner").first()
    coach = coach or make_coach("rpc_ui_decliner")
    when = timezone.now() - timedelta(days=days_ago)
    return ConnectRealPersonCheck.objects.create(
        profile=CrushProfile.objects.get(user=member),
        coach=coach,
        status=ConnectRealPersonCheck.STATUS_DECLINED,
        decided_at=when,
    )


def fake_report(member, status="open"):
    reporter = make_member(f"rpc_ui_reporter_{status}_{member.pk}")
    return UserReport.objects.create(
        reporter=reporter,
        reported_user=member,
        reason="fake_profile",
        status=status,
    )


def test_the_note_goes_while_a_decline_is_inside_the_cooldown(client, flags_on):
    """A member who said no is not told a coach "may text" them."""
    member = make_member("rpc_ui_note_declined")
    decline(member, days_ago=DECLINE_COOLDOWN.days - 1)

    assert "data-real-person-check-note" not in hub_text(client, member)


def test_the_note_returns_once_the_decline_cooldown_has_passed(client, flags_on):
    member = make_member("rpc_ui_note_cooled")
    decline(member, days_ago=DECLINE_COOLDOWN.days + 1)

    assert "data-real-person-check-note" in hub_text(client, member)


@pytest.mark.parametrize("status", ["open", "reviewing"])
def test_the_note_goes_while_a_fake_profile_report_awaits_a_superuser(
    client, flags_on, status
):
    member = make_member(f"rpc_ui_note_reported_{status}")
    fake_report(member, status)

    assert "data-real-person-check-note" not in hub_text(client, member)


@pytest.mark.parametrize("status", ["actioned", "dismissed"])
def test_the_note_returns_once_the_fake_profile_report_is_handled(
    client, flags_on, status
):
    member = make_member(f"rpc_ui_note_handled_{status}")
    fake_report(member, status)

    assert "data-real-person-check-note" in hub_text(client, member)


def test_a_report_for_another_reason_does_not_hide_the_note(client, flags_on):
    member = make_member("rpc_ui_note_spam")
    UserReport.objects.create(
        reporter=make_member("rpc_ui_note_spammer"),
        reported_user=member,
        reason="spam",
        status="open",
    )

    assert "data-real-person-check-note" in hub_text(client, member)


def test_the_note_and_the_queue_agree_on_who_can_be_reached(flags_on):
    """``member_awaits_outreach`` is the queue's coach-independent half.

    Two copies of the exclusion list would drift; this pins the one to the
    other for every exclusion they share.
    """
    from crush_lu.services.real_person_check import (
        member_awaits_outreach,
        real_person_check_queue,
    )

    coach = make_coach("rpc_ui_agree_coach")
    members = {
        "plain": make_member("rpc_ui_agree_plain"),
        "badged": decorate(make_member("rpc_ui_agree_badged"), "voice"),
        "declined": make_member("rpc_ui_agree_declined"),
        "cooled": make_member("rpc_ui_agree_cooled"),
        "reported": make_member("rpc_ui_agree_reported"),
        "handled": make_member("rpc_ui_agree_handled"),
    }
    decline(members["declined"], days_ago=1)
    decline(members["cooled"], days_ago=DECLINE_COOLDOWN.days + 1)
    fake_report(members["reported"], "open")
    fake_report(members["handled"], "dismissed")

    queued = set(real_person_check_queue(coach).values_list("pk", flat=True))
    reachable = {name for name, user in members.items() if member_awaits_outreach(user)}

    assert reachable == {"plain", "cooled", "handled"}
    assert reachable == {name for name, user in members.items() if user.pk in queued}


def test_the_hub_costs_nothing_extra_with_the_flags_off(client, settings):
    member = make_member("rpc_ui_note_cost")
    logged_in(client, member)

    def cost():
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            assert client.get(HUB_URL).status_code == 200
        return len(queries)

    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    cost()
    off = cost()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    on = cost()

    # Flags on adds the cohort lookup (and the badge row): a handful, fixed.
    assert off <= on <= off + 3
