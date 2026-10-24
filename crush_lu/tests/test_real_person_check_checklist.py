"""Server-side validation of the real-person check checklist and notes.

Today's screening call stores any JSON the browser posts; the check does not.
A pass needs every key its medium requires, ticked, and nothing else.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import pytest
from django.core.cache import cache

from crush_lu.models import ConnectRealPersonBadge, ConnectRealPersonCheck
from crush_lu.real_person_check_rules import (
    CHECKLIST_KEYS,
    NOTES_MAX_LENGTH,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    claim_check,
    decide_check,
    log_outreach,
    validate_checklist,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)

pytestmark = pytest.mark.django_db

VOICE_KEYS = REQUIRED_CHECKLIST_KEYS["voice"]
VIDEO_KEYS = REQUIRED_CHECKLIST_KEYS["video"]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _code(call):
    with pytest.raises(RealPersonCheckError) as excinfo:
        call()
    assert excinfo.value.status == 400
    return excinfo.value.code


def test_each_medium_requires_its_own_keys():
    assert set(VOICE_KEYS) == {
        "spoke_live",
        "name_matches",
        "age_matches",
        "badge_explained",
    }
    assert set(VIDEO_KEYS) == set(VOICE_KEYS) | {"face_matches_photo", "live_camera"}
    assert set(CHECKLIST_KEYS) == set(VIDEO_KEYS)


@pytest.mark.parametrize("medium", ["voice", "video"])
def test_a_fully_ticked_checklist_is_accepted(medium):
    keys = REQUIRED_CHECKLIST_KEYS[medium]
    assert validate_checklist(medium, {key: True for key in keys}) == {
        key: True for key in keys
    }


@pytest.mark.parametrize("medium", ["voice", "video"])
def test_every_required_key_is_needed_and_must_be_ticked(medium):
    keys = REQUIRED_CHECKLIST_KEYS[medium]
    full = {key: True for key in keys}
    for key in keys:
        missing = {k: v for k, v in full.items() if k != key}
        assert _code(lambda: validate_checklist(medium, missing)) == (
            "checklist_incomplete"
        )
        unticked = {**full, key: False}
        assert _code(lambda: validate_checklist(medium, unticked)) == (
            "checklist_incomplete"
        )
    assert _code(lambda: validate_checklist(medium, {})) == "checklist_incomplete"
    assert _code(lambda: validate_checklist(medium, None)) == "checklist_incomplete"


def test_a_voice_pass_cannot_claim_the_video_only_keys():
    payload = {key: True for key in VIDEO_KEYS}
    assert _code(lambda: validate_checklist("voice", payload)) == (
        "unknown_checklist_keys"
    )


@pytest.mark.parametrize("medium", ["voice", "video", ""])
def test_unknown_keys_are_refused(medium):
    keys = REQUIRED_CHECKLIST_KEYS.get(medium, ())
    payload = {**{key: True for key in keys}, "is_admin": True}
    assert _code(lambda: validate_checklist(medium, payload)) == (
        "unknown_checklist_keys"
    )


@pytest.mark.parametrize("bad", ["true", "false", "on", 1, 0, None, [], {}, "yes"])
@pytest.mark.parametrize("medium", ["voice", ""])
def test_non_boolean_values_are_refused_not_coerced(medium, bad):
    payload = {key: True for key in VOICE_KEYS}
    payload["spoke_live"] = bad
    assert _code(lambda: validate_checklist(medium, payload)) == "invalid_checklist"


@pytest.mark.parametrize("payload", ["spoke_live", ["spoke_live"], 5, True])
def test_the_payload_must_be_an_object(payload):
    assert _code(lambda: validate_checklist("voice", payload)) == "invalid_checklist"


def test_an_unknown_medium_is_refused():
    assert _code(lambda: validate_checklist("telepathy", {})) == "invalid_medium"


def test_outside_a_pass_only_the_shape_is_checked():
    assert validate_checklist("", None) == {}
    assert validate_checklist("", {"spoke_live": True, "name_matches": False}) == {
        "spoke_live": True
    }
    assert validate_checklist("", {key: True for key in VIDEO_KEYS}) == {
        key: True for key in VIDEO_KEYS
    }


def test_the_result_follows_the_required_order_not_the_posted_one():
    reversed_payload = {key: True for key in reversed(VOICE_KEYS)}
    assert list(validate_checklist("voice", reversed_payload)) == list(VOICE_KEYS)


@pytest.fixture
def claimed():
    coach = make_coach("rpc_checklist_coach")
    member = make_member("rpc_checklist_member")
    check = claim_check(coach, profile_of(member).pk)
    return coach, member, check


def test_a_bad_checklist_never_reaches_the_database(claimed):
    coach, _member, check = claimed
    tampered = {key: "true" for key in VOICE_KEYS}
    assert (
        _code(
            lambda: decide_check(
                coach, check.pk, "pass_voice", checklist=tampered, confirmed=True
            )
        )
        == "invalid_checklist"
    )
    check.refresh_from_db()
    assert check.status == "open"
    assert check.checklist == {}
    assert not ConnectRealPersonBadge.objects.exists()


def test_a_non_pass_outcome_stores_only_known_boolean_ticks(claimed):
    coach, _member, check = claimed
    decided = decide_check(
        coach,
        check.pk,
        "declined",
        checklist={"spoke_live": True, "name_matches": False},
    )
    assert decided.checklist == {"spoke_live": True}
    assert ConnectRealPersonCheck.objects.get(pk=check.pk).status == "declined"


def test_notes_are_limited_to_1000_characters(claimed):
    coach, _member, check = claimed
    too_long = "x" * (NOTES_MAX_LENGTH + 1)
    assert NOTES_MAX_LENGTH == 1000
    assert (
        _code(lambda: log_outreach(coach, check.pk, result="sms_sent", notes=too_long))
        == "notes_too_long"
    )
    assert (
        _code(lambda: decide_check(coach, check.pk, "declined", notes=too_long))
        == "notes_too_long"
    )
    attempt = log_outreach(
        coach, check.pk, result="sms_sent", notes="x" * NOTES_MAX_LENGTH
    )
    assert len(attempt.notes) == NOTES_MAX_LENGTH
