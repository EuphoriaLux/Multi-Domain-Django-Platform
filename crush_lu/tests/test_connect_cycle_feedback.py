"""
Tests for post-cycle feedback (Epic 13 / Task 13.4): the prompt shown on the
Connect Week home after a cycle completes, and the row it writes.

The interesting property is the EXIT. ``connect_week_home`` opens a new session
the moment the old one completes, so no page ever naturally stops rendering the
prompt — the recency window and the stored dismissal are the only two things
that make it stop. Both have tests here.

Reuses the Cycle suite's fixtures (see ``test_connect_week_experience``).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import (
    ConnectCycleFeedback,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import (
    FEEDBACK_PROMPT_WINDOW_DAYS,
    get_pending_feedback_session,
    record_cycle_feedback,
)
from crush_lu.tests.test_connect_week_experience import (
    WEEK_HOME_URL,
    _make_cycle_user,
)
from crush_lu.tests.test_crush_connect import _login_eligible

WEEK_FEEDBACK_URL = "/en/crush-connect/week/feedback/"
PROMPT_HEADING = "How was your Connect Week?"


def _completed_session(user, days_ago=0):
    """A cycle that finished ``days_ago`` days ago — the state the prompt asks
    about."""
    completed = timezone.now() - timedelta(days=days_ago)
    return ConnectWeekSession.objects.create(
        user=user,
        status=ConnectWeekSession.Status.COMPLETED,
        completed_at=completed,
    )


# ---------------------------------------------------------------------------
# Which cycle is owed a verdict
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recently_completed_cycle_is_pending_feedback():
    me = _make_cycle_user("fb_pending")
    session = _completed_session(me, days_ago=1)
    assert get_pending_feedback_session(me) == session


@pytest.mark.django_db
def test_active_cycle_is_not_asked_about():
    """A cycle still being played has no verdict to give, and asking mid-week
    would both bias the answer and interrupt the review."""
    me = _make_cycle_user("fb_active")
    ConnectWeekSession.objects.create(user=me)
    assert get_pending_feedback_session(me) is None


@pytest.mark.django_db
def test_review_open_cycle_is_not_asked_about():
    me = _make_cycle_user("fb_review")
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()
    assert get_pending_feedback_session(me) is None


@pytest.mark.django_db
def test_cycle_older_than_the_window_lapses():
    """The exit. Without this an unanswered, undismissed cycle would follow the
    member into every future week forever, because a completed session has no
    page that stops rendering the prompt."""
    me = _make_cycle_user("fb_stale")
    _completed_session(me, days_ago=FEEDBACK_PROMPT_WINDOW_DAYS + 1)
    assert get_pending_feedback_session(me) is None


@pytest.mark.django_db
def test_answered_cycle_is_not_asked_again():
    me = _make_cycle_user("fb_answered")
    session = _completed_session(me, days_ago=1)
    record_cycle_feedback(session, sentiment="good")
    assert get_pending_feedback_session(me) is None


@pytest.mark.django_db
def test_dismissed_cycle_is_not_asked_again():
    """A dismissal writes a row precisely so that absence never has to mean
    "no" — otherwise "not now" would come back tomorrow."""
    me = _make_cycle_user("fb_dismissed")
    session = _completed_session(me, days_ago=1)
    record_cycle_feedback(session, dismissed=True)
    assert get_pending_feedback_session(me) is None


@pytest.mark.django_db
def test_only_the_latest_completed_cycle_is_asked_about():
    """A member returning after an absence is asked about the week they just
    finished — never handed a backlog of surveys."""
    me = _make_cycle_user("fb_latest")
    _completed_session(me, days_ago=6)
    recent = _completed_session(me, days_ago=1)
    assert get_pending_feedback_session(me) == recent


@pytest.mark.django_db
def test_another_members_cycle_is_never_returned():
    me = _make_cycle_user("fb_mine")
    other = _make_cycle_user("fb_theirs")
    _completed_session(other, days_ago=1)
    assert get_pending_feedback_session(me) is None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_record_stores_the_answers_against_the_session_owner():
    me = _make_cycle_user("fb_store")
    session = _completed_session(me, days_ago=1)

    row, created = record_cycle_feedback(
        session, sentiment="good", match_quality="mixed", comment="  liked it  "
    )

    assert created
    assert row.user == me
    assert row.sentiment == "good"
    assert row.match_quality == "mixed"
    assert row.comment == "liked it"
    assert not row.dismissed
    assert row.was_answered


@pytest.mark.django_db
def test_a_dismissal_is_not_an_answer():
    me = _make_cycle_user("fb_dismiss_flag")
    session = _completed_session(me, days_ago=1)

    row, _created = record_cycle_feedback(session, dismissed=True)

    assert row.dismissed
    assert row.sentiment == ""
    assert not row.was_answered


@pytest.mark.django_db
def test_a_second_write_cannot_overwrite_the_first():
    """The prompt is a plain form POST, so a double-submit must land on the
    existing row rather than raising IntegrityError at the member — and a
    stray dismissal arriving after a verdict must not erase it."""
    me = _make_cycle_user("fb_replay")
    session = _completed_session(me, days_ago=1)

    record_cycle_feedback(session, sentiment="good")
    row, created = record_cycle_feedback(session, dismissed=True)

    assert not created
    assert row.sentiment == "good"
    assert not row.dismissed
    assert ConnectCycleFeedback.objects.filter(session=session).count() == 1


@pytest.mark.django_db
def test_comment_is_capped_at_the_field_length():
    me = _make_cycle_user("fb_long")
    session = _completed_session(me, days_ago=1)

    row, _created = record_cycle_feedback(
        session, sentiment="good", comment="x" * 2000
    )

    assert len(row.comment) == 1000


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_prompt_renders_on_week_home_after_a_completed_cycle(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_view")
    _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert resp.status_code == 200
    assert PROMPT_HEADING in resp.content.decode()


@pytest.mark.django_db
def test_prompt_absent_when_nothing_is_pending(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_view_none")
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert resp.status_code == 200
    assert PROMPT_HEADING not in resp.content.decode()


@pytest.mark.django_db
def test_submitting_feedback_stores_it_and_stops_the_prompt(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_submit")
    session = _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    resp = client.post(
        WEEK_FEEDBACK_URL,
        {"sentiment": "good", "match_quality": "poor", "comment": "more variety"},
    )

    assert resp.status_code in (301, 302)
    row = ConnectCycleFeedback.objects.get(session=session)
    assert row.sentiment == "good"
    assert row.match_quality == "poor"
    assert row.comment == "more variety"

    follow_up = client.get(WEEK_HOME_URL)
    assert PROMPT_HEADING not in follow_up.content.decode()


@pytest.mark.django_db
def test_dismissing_stops_the_prompt_without_recording_a_verdict(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_dismiss_view")
    session = _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    resp = client.post(WEEK_FEEDBACK_URL, {"action": "dismiss"})

    assert resp.status_code in (301, 302)
    row = ConnectCycleFeedback.objects.get(session=session)
    assert row.dismissed
    assert not row.was_answered

    follow_up = client.get(WEEK_HOME_URL)
    assert PROMPT_HEADING not in follow_up.content.decode()


@pytest.mark.django_db
def test_an_invalid_sentiment_records_nothing(client, settings):
    """Rejecting rather than coercing: a survey that silently stores a value
    the member did not choose is worse than one that asks again."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_bad")
    session = _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    resp = client.post(WEEK_FEEDBACK_URL, {"sentiment": "amazing"})

    assert resp.status_code in (301, 302)
    assert not ConnectCycleFeedback.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_an_invalid_match_quality_is_dropped_not_rejected(client, settings):
    """The second question is optional, so a junk value costs the member their
    answer to it — not their whole submission."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_partial")
    session = _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    client.post(WEEK_FEEDBACK_URL, {"sentiment": "good", "match_quality": "junk"})

    row = ConnectCycleFeedback.objects.get(session=session)
    assert row.sentiment == "good"
    assert row.match_quality == ""


@pytest.mark.django_db
def test_get_is_not_a_write(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_get")
    session = _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    resp = client.get(WEEK_FEEDBACK_URL)

    assert resp.status_code in (301, 302)
    assert not ConnectCycleFeedback.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_posting_with_nothing_pending_writes_nothing(client, settings):
    """The view re-resolves the pending cycle instead of trusting a posted id,
    so a replayed or stale POST lands on "nothing pending" rather than writing
    a second row or touching a cycle already answered for."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_stale_post")
    session = _completed_session(me, days_ago=FEEDBACK_PROMPT_WINDOW_DAYS + 1)
    _login_eligible(client, me)

    resp = client.post(WEEK_FEEDBACK_URL, {"sentiment": "good"})

    assert resp.status_code in (301, 302)
    assert not ConnectCycleFeedback.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_feedback_requires_cycle_access(client, settings):
    """Same gate as the rest of the Connect Week: a LuxID-only candidate has
    no cycle access, so this endpoint must bounce them like every other."""
    from crush_lu.tests.test_crush_connect import CONNECT_TEASER_URL, _make_user

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="fb_luxid_only", premium=False)
    _login_eligible(client, me)

    resp = client.post(WEEK_FEEDBACK_URL, {"sentiment": "good"})

    assert resp.status_code in (301, 302)
    assert CONNECT_TEASER_URL in resp.url
    assert not ConnectCycleFeedback.objects.exists()


@pytest.mark.django_db
def test_feedback_requires_login(client):
    resp = client.post(WEEK_FEEDBACK_URL, {"sentiment": "good"})
    assert resp.status_code in (301, 302)
    assert not ConnectCycleFeedback.objects.exists()


@pytest.mark.django_db
def test_the_two_questions_have_distinct_labels(client, settings):
    """Shared values, separate wording: "Good week" is not an answer to "did
    the daily profiles feel like a fit?"."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("fb_labels")
    _completed_session(me, days_ago=1)
    _login_eligible(client, me)

    body = client.get(WEEK_HOME_URL).content.decode()

    assert "Good week" in body
    assert "Good fit" in body
    assert "Not for me" in body
    assert "Not really" in body
    # The scales must stay comparable even though the labels differ.
    assert [c.value for c in ConnectCycleFeedback.Sentiment] == [
        c.value for c in ConnectCycleFeedback.MatchQuality
    ]
