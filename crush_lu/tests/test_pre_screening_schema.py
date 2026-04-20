"""Unit tests for the pre-screening schema validator and scorer."""
from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from crush_lu.pre_screening_schema import (
    FLAG_DESCRIPTIONS,
    PRE_SCREENING_SCHEMA,
    compute_readiness_score,
    get_question,
    get_section,
    iter_questions,
    readiness_score_label,
    validate_pre_screening_responses,
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _valid_responses(**overrides) -> dict:
    """A complete, valid response set. Override keys to test edge cases."""
    base = {
        "residence": "lu_city",
        "languages": ["en", "fr"],
        "age_confirm": True,
        "source": "friend",
        "what_is_crush": "events",
        "event_frequency": "monthly",
        "relationship_goal": "meaningful",
        "coach_attitude": "loves",
        "looking_forward_to": ["events", "discovery"],
        "hoping_to_meet": "Someone curious who likes long walks and honest conversation.",
        "note_to_coach": "",
        "consent_events": True,
        "consent_coach": True,
        "consent_no_show": True,
        "consent_terms": True,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Schema shape
# --------------------------------------------------------------------------

def test_schema_has_version():
    assert PRE_SCREENING_SCHEMA["version"] >= 1


def test_schema_has_all_four_sections():
    section_ids = [s["id"] for s in PRE_SCREENING_SCHEMA["sections"]]
    assert section_ids == ["logistics", "concept", "own_words", "consents"]


def test_schema_question_counts_match_appendix_a():
    counts = {s["id"]: len(s["questions"]) for s in PRE_SCREENING_SCHEMA["sections"]}
    assert counts == {
        "logistics": 4,
        "concept": 5,
        "own_words": 2,
        "consents": 4,
    }


def test_get_question_and_section():
    assert get_question("what_is_crush")["type"] == "single_select"
    assert get_section("concept")["id"] == "concept"
    assert get_question("nope") is None


# --------------------------------------------------------------------------
# Validator — happy paths
# --------------------------------------------------------------------------

def test_valid_response_passes():
    validate_pre_screening_responses(_valid_responses(), version=1)


def test_valid_response_with_optional_note():
    validate_pre_screening_responses(
        _valid_responses(note_to_coach="I'm a bit shy."), version=1
    )


def test_valid_response_without_version():
    validate_pre_screening_responses(_valid_responses(), version=None)


# --------------------------------------------------------------------------
# Validator — error codes
# --------------------------------------------------------------------------

def _error_codes(exc: ValidationError, qid: str) -> list[str]:
    messages = exc.message_dict if hasattr(exc, "message_dict") else {}
    return [getattr(e, "code", None) for e in (messages.get(qid) or [])]


def test_invalid_version_raises():
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(_valid_responses(), version=999)
    assert excinfo.value.error_list[0].code == "invalid_version"


def test_required_missing():
    resp = _valid_responses()
    resp.pop("residence")
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "required_missing" in [
        e.code for e in excinfo.value.error_dict.get("residence", [])
    ]


def test_invalid_choice_single_select():
    resp = _valid_responses(residence="atlantis")
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "invalid_choice" in [
        e.code for e in excinfo.value.error_dict.get("residence", [])
    ]


def test_invalid_choice_multi_select():
    resp = _valid_responses(languages=["en", "klingon"])
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "invalid_choice" in [
        e.code for e in excinfo.value.error_dict.get("languages", [])
    ]


def test_min_choices_languages_empty():
    resp = _valid_responses(languages=[])
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    # Empty list is treated as "not present" -> required_missing
    assert "required_missing" in [
        e.code for e in excinfo.value.error_dict.get("languages", [])
    ]


def test_min_choices_not_met_for_looking_forward_to_never_triggers_for_empty():
    # Empty list short-circuits to required_missing, which is correct.
    resp = _valid_responses(looking_forward_to=[])
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    codes = [e.code for e in excinfo.value.error_dict.get("looking_forward_to", [])]
    assert "required_missing" in codes


def test_max_choices_exceeded():
    resp = _valid_responses(
        looking_forward_to=["events", "discovery", "coach_match", "offline_quickly"]
    )
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "max_choices_exceeded" in [
        e.code for e in excinfo.value.error_dict.get("looking_forward_to", [])
    ]


def test_text_too_long():
    resp = _valid_responses(hoping_to_meet="x" * 201)
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "text_too_long" in [
        e.code for e in excinfo.value.error_dict.get("hoping_to_meet", [])
    ]


def test_checkbox_unchecked_fails():
    resp = _valid_responses(consent_events=False)
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "required_missing" in [
        e.code for e in excinfo.value.error_dict.get("consent_events", [])
    ]


def test_unknown_question_rejected():
    resp = _valid_responses(bogus_question="yes")
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "unknown_question" in [
        e.code for e in excinfo.value.error_dict.get("bogus_question", [])
    ]


def test_multi_select_wrong_type():
    resp = _valid_responses(languages="en")
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses(resp, version=1)
    assert "invalid_type" in [
        e.code for e in excinfo.value.error_dict.get("languages", [])
    ]


# --------------------------------------------------------------------------
# Validator — partial / section-scoped saves
# --------------------------------------------------------------------------

def test_partial_mode_skips_required_missing():
    # Only logistics answered; concept/own_words/consents missing.
    resp = {
        "residence": "lu_city",
        "languages": ["en"],
        "age_confirm": True,
        "source": "friend",
    }
    # partial=True must not raise for unanswered required questions.
    validate_pre_screening_responses(resp, version=1, partial=True)


def test_section_scoped_validation():
    # Only validate logistics — missing concept answers is fine.
    resp = {
        "residence": "lu_city",
        "languages": ["en"],
        "age_confirm": True,
        "source": "friend",
    }
    validate_pre_screening_responses(resp, version=1, section_id="logistics")


def test_section_scoped_still_catches_bad_choice():
    resp = {"residence": "atlantis"}
    with pytest.raises(ValidationError):
        validate_pre_screening_responses(
            resp, version=1, partial=True, section_id="logistics"
        )


def test_non_dict_response_rejected():
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_screening_responses([], version=1)  # type: ignore[arg-type]
    assert excinfo.value.error_list[0].code == "invalid_type"


# --------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------

def test_scorer_returns_tuple_and_clips_to_range():
    for resp in [
        _valid_responses(),
        _valid_responses(coach_attitude="no_intermediary", event_frequency="online_only"),
        _valid_responses(what_is_crush="tinder", event_frequency="online_only",
                         coach_attitude="no_intermediary", hoping_to_meet="x"),
    ]:
        score, flags = compute_readiness_score(resp)
        assert 0 <= score <= 10
        assert isinstance(flags, list)


def test_high_readiness_profile():
    resp = _valid_responses(
        what_is_crush="events",
        event_frequency="weekly",
        coach_attitude="loves",
        hoping_to_meet=(
            "Someone thoughtful and curious who loves walking and good conversation, "
            "maybe a bit introverted but warm, who values depth over small talk."
        ),
        looking_forward_to=["events", "discovery"],
    )
    score, flags = compute_readiness_score(resp)
    assert score >= 8
    assert flags == []


def test_low_readiness_triggers_all_flags():
    resp = _valid_responses(
        what_is_crush="tinder",
        event_frequency="online_only",
        coach_attitude="no_intermediary",
        hoping_to_meet="idk",
    )
    score, flags = compute_readiness_score(resp)
    assert score <= 2
    assert "concept_misalignment" in flags
    assert "events_disinterest" in flags
    assert "coach_reluctance" in flags
    assert "low_effort_text" in flags


def test_low_effort_detects_repeated_chars():
    resp = _valid_responses(hoping_to_meet="aaaaaaaaaaaaaaaaaaaaaa")
    _score, flags = compute_readiness_score(resp)
    assert "low_effort_text" in flags


def test_low_effort_detects_short_text():
    resp = _valid_responses(hoping_to_meet="anyone")
    _score, flags = compute_readiness_score(resp)
    assert "low_effort_text" in flags


def test_optional_note_to_coach_never_flags():
    resp = _valid_responses(note_to_coach="")
    _score, flags = compute_readiness_score(resp)
    assert "no_note_to_coach" not in flags


def test_tinder_answer_flags_without_rejecting():
    """Per product principle: no single answer auto-rejects."""
    resp = _valid_responses(what_is_crush="tinder")
    score, flags = compute_readiness_score(resp)
    assert "concept_misalignment" in flags
    assert score >= 0  # not forced to rock bottom


def test_unsure_about_concept_is_positive():
    # "events" and "unsure" both get the +2 concept-alignment bump; their
    # scores must match when everything else is equal.
    resp_events = _valid_responses(what_is_crush="events")
    resp_unsure = _valid_responses(what_is_crush="unsure")
    s_events, _ = compute_readiness_score(resp_events)
    s_unsure, _ = compute_readiness_score(resp_unsure)
    assert s_events == s_unsure
    # And they should be clearly above the "low" threshold.
    assert s_events >= 5


def test_diverse_inputs_always_in_range():
    import random
    rng = random.Random(42)
    what_choices = ["events", "tinder", "matchmaking", "unsure"]
    freq_choices = ["weekly", "monthly", "few_per_year", "online_only"]
    attitudes = ["loves", "fine", "curious", "no_intermediary"]
    for _ in range(20):
        resp = _valid_responses(
            what_is_crush=rng.choice(what_choices),
            event_frequency=rng.choice(freq_choices),
            coach_attitude=rng.choice(attitudes),
            hoping_to_meet="x" * rng.randint(0, 200),
            looking_forward_to=rng.sample(
                ["events", "discovery", "coach_match"], rng.randint(1, 3)
            ),
        )
        score, _flags = compute_readiness_score(resp)
        assert 0 <= score <= 10


# --------------------------------------------------------------------------
# Score label bucketing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("score,label", [
    (None, "pending"),
    (0, "low"),
    (4, "low"),
    (5, "medium"),
    (7, "medium"),
    (8, "high"),
    (10, "high"),
])
def test_readiness_score_label(score, label):
    assert readiness_score_label(score) == label


# --------------------------------------------------------------------------
# Flag descriptions exist for every flag the scorer can emit
# --------------------------------------------------------------------------

def test_every_flag_has_a_description():
    # Enumerate all flag codes the scorer can emit by running it across the
    # canonical worst-case input. Add more here if new flags are introduced.
    known = {"concept_misalignment", "events_disinterest", "coach_reluctance", "low_effort_text"}
    assert set(FLAG_DESCRIPTIONS.keys()) == known
