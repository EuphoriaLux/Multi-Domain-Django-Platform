"""Tests for the Quiz Night question packs and the seeding command.

Covers the pack registry, ``validate_pack``'s contract, and ``populate_quiz``'s
handling of the two media shapes (embeddable video/audio vs. images that need a
manual upload).
"""

import pytest
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from crush_lu.management.commands.generate_crush_quiz import (
    populate_quiz,
    validate_pack,
)
from crush_lu.models.quiz import QuizEvent, QuizQuestion, QuizRound
from crush_lu.quiz_packs import PACKS, pack_names

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        username="packcoach@test.com",
        email="packcoach@test.com",
        password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def quiz_event(db, coach_user):
    from crush_lu.models import MeetupEvent

    event = MeetupEvent.objects.create(
        title="Pack Quiz Night",
        description="Seeding test event",
        event_type="quiz_night",
        date_time=timezone.now() + timedelta(days=1),
        location="Luxembourg City",
        address="1 Place d'Armes",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(hours=12),
        is_published=True,
    )
    return QuizEvent.objects.create(event=event, status="draft", created_by=coach_user)


def _question(**overrides):
    """A minimal valid multiple-choice question, trilingual."""
    data = {
        "type": "multiple_choice",
        "text_en": "Question?",
        "text_de": "Frage?",
        "text_fr": "Question ?",
        "correct_answer_en": "A",
        "correct_answer_de": "A",
        "correct_answer_fr": "A",
        "choices_en": [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
        "choices_de": [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
        "choices_fr": [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
    }
    data.update(overrides)
    return data


def _pack(*questions, **round_overrides):
    """A one-round pack wrapping *questions*."""
    round_data = {
        "title_en": "Round",
        "title_de": "Runde",
        "title_fr": "Manche",
        "questions": list(questions) or [_question()],
    }
    round_data.update(round_overrides)
    return [round_data]


# ============================================================================
# REGISTRY
# ============================================================================


class TestPackRegistry:
    def test_classic_pack_is_registered(self):
        assert "classic" in PACKS
        assert "classic" in pack_names()

    def test_every_registered_pack_validates(self):
        """A shipped pack must never fail its own validator."""
        for name, rounds in PACKS.items():
            assert validate_pack(rounds) == [], f"pack '{name}' is invalid"

    def test_classic_pack_shape_is_unchanged(self):
        """The classic pack is 6 rounds x 6 questions and stays text-only."""
        rounds = PACKS["classic"]
        assert len(rounds) == 6
        assert [len(r["questions"]) for r in rounds] == [6] * 6
        assert not any(q.get("media") for r in rounds for q in r["questions"])


# ============================================================================
# VALIDATION
# ============================================================================


class TestValidatePack:
    def test_valid_pack_has_no_errors(self):
        assert validate_pack(_pack()) == []

    def test_missing_translation_is_reported(self):
        errors = validate_pack(_pack(_question(text_de="")))
        assert any("missing text_de" in e for e in errors)

    def test_unknown_question_type_is_reported(self):
        errors = validate_pack(_pack(_question(type="essay")))
        assert any("unknown type" in e for e in errors)

    def test_two_correct_options_is_reported(self):
        bad = [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": True},
        ]
        errors = validate_pack(_pack(_question(choices_en=bad)))
        assert any("exactly one correct" in e for e in errors)

    def test_no_correct_option_is_reported(self):
        bad = [
            {"text": "A", "is_correct": False},
            {"text": "B", "is_correct": False},
        ]
        errors = validate_pack(_pack(_question(choices_en=bad)))
        assert any("exactly one correct" in e for e in errors)

    def test_mismatched_option_counts_across_languages(self):
        errors = validate_pack(
            _pack(_question(choices_fr=[{"text": "A", "is_correct": True}]))
        )
        assert any("option counts differ" in e for e in errors)

    def test_open_ended_must_not_carry_choices(self):
        errors = validate_pack(
            _pack(
                _question(
                    type="open_ended",
                    choices_de=[],
                    choices_fr=[],
                )
            )
        )
        assert any("open_ended must have empty choices_en" in e for e in errors)

    def test_round_without_questions_is_reported(self):
        errors = validate_pack(
            [
                {
                    "title_en": "R",
                    "title_de": "R",
                    "title_fr": "R",
                    "questions": [],
                }
            ]
        )
        assert any("has no questions" in e for e in errors)


class TestValidateMedia:
    def test_allowlisted_embed_passes(self):
        media = {
            "kind": "audio",
            "url": "https://open.spotify.com/track/abc123",
            "description": "A love song",
        }
        assert validate_pack(_pack(_question(media=media))) == []

    def test_offlist_host_is_rejected(self):
        media = {
            "kind": "video",
            "url": "https://example.com/clip.mp4",
            "description": "A clip",
        }
        errors = validate_pack(_pack(_question(media=media)))
        assert any("not on the embed allowlist" in e for e in errors)

    def test_embed_without_url_is_rejected(self):
        media = {"kind": "video", "description": "A clip"}
        errors = validate_pack(_pack(_question(media=media)))
        assert any("needs an external embed url" in e for e in errors)

    def test_media_without_description_is_rejected(self):
        """The accessible label is required, not optional."""
        media = {
            "kind": "video",
            "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        }
        errors = validate_pack(_pack(_question(media=media)))
        assert any("needs a description" in e for e in errors)

    def test_image_with_external_url_is_rejected(self):
        """Images have no external-URL path — the allowlist serves embeds only."""
        media = {
            "kind": "image",
            "url": "https://upload.wikimedia.org/photo.jpg",
            "upload": "photo.jpg",
            "description": "A couple",
        }
        errors = validate_pack(_pack(_question(media=media)))
        assert any("images cannot use an external url" in e for e in errors)

    def test_image_without_upload_filename_is_rejected(self):
        media = {"kind": "image", "description": "A couple"}
        errors = validate_pack(_pack(_question(media=media)))
        assert any("needs an 'upload' filename" in e for e in errors)

    def test_unknown_media_kind_is_rejected(self):
        media = {"kind": "gif", "description": "x"}
        errors = validate_pack(_pack(_question(media=media)))
        assert any("media kind must be" in e for e in errors)


# ============================================================================
# SEEDING
# ============================================================================


class TestPopulateQuiz:
    def test_seeds_classic_pack(self, quiz_event):
        result = populate_quiz(quiz_event)
        assert result.rounds_created == 6
        assert result.questions_created == 36
        assert result.pending_uploads == []
        assert quiz_event.rounds.count() == 6

    def test_defaults_to_classic_pack(self, quiz_event):
        """The pack argument is optional so existing callers keep working."""
        result = populate_quiz(quiz_event)
        titles = list(quiz_event.rounds.values_list("title_en", flat=True))
        assert titles[0] == PACKS["classic"][0]["title_en"]
        assert result.questions_created == 36

    def test_sets_all_three_languages(self, quiz_event, monkeypatch):
        monkeypatch.setitem(PACKS, "tmp", _pack())
        populate_quiz(quiz_event, pack="tmp")
        question = QuizQuestion.objects.get()
        assert question.text_en == "Question?"
        assert question.text_de == "Frage?"
        assert question.text_fr == "Question ?"
        assert question.correct_answer_de == "A"

    def test_clear_removes_existing_rounds(self, quiz_event):
        populate_quiz(quiz_event)
        result = populate_quiz(quiz_event, clear=True)
        assert result.rounds_created == 6
        assert quiz_event.rounds.count() == 6

    def test_round_can_override_timing_and_bonus(self, quiz_event, monkeypatch):
        monkeypatch.setitem(PACKS, "tmp", _pack(time_per_question=45, is_bonus=True))
        populate_quiz(quiz_event, pack="tmp")
        quiz_round = QuizRound.objects.get()
        assert quiz_round.time_per_question == 45
        assert quiz_round.is_bonus is True

    def test_timing_defaults_to_30_seconds(self, quiz_event, monkeypatch):
        monkeypatch.setitem(PACKS, "tmp", _pack())
        populate_quiz(quiz_event, pack="tmp")
        assert QuizRound.objects.get().time_per_question == 30

    def test_embed_media_is_seeded_onto_the_row(self, quiz_event, monkeypatch):
        media = {
            "kind": "audio",
            "url": "https://open.spotify.com/track/abc123",
            "description": "A love song",
        }
        monkeypatch.setitem(PACKS, "tmp", _pack(_question(media=media)))
        result = populate_quiz(quiz_event, pack="tmp")

        question = QuizQuestion.objects.get()
        assert question.media_kind == "audio"
        assert question.media_url == "https://open.spotify.com/track/abc123"
        assert question.media_description == "A love song"
        assert result.pending_uploads == []

    def test_embed_media_survives_the_model_media_payload(
        self, quiz_event, monkeypatch
    ):
        """A seeded embed must produce a usable broadcast payload."""
        media = {
            "kind": "video",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "description": "A famous clip",
        }
        monkeypatch.setitem(PACKS, "tmp", _pack(_question(media=media)))
        populate_quiz(quiz_event, pack="tmp")

        payload = QuizQuestion.objects.get().get_media_payload()
        assert payload["kind"] == "video"
        assert payload["source"] == "external"
        # Watch URLs are normalized to their embed form on the way out.
        assert payload["url"] == ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")
        assert payload["description"] == "A famous clip"

    def test_image_media_is_reported_not_seeded(self, quiz_event, monkeypatch):
        """Images seed media-less and come back as an upload manifest entry."""
        media = {
            "kind": "image",
            "upload": "famous-couple.jpg",
            "description": "A famous couple",
            "credit": "https://commons.wikimedia.org/… — CC BY-SA",
        }
        monkeypatch.setitem(PACKS, "tmp", _pack(_question(media=media)))
        result = populate_quiz(quiz_event, pack="tmp")

        question = QuizQuestion.objects.get()
        # Seeded rows stay valid: a kind without a file or URL would not.
        assert question.media_kind == "none"
        assert question.get_media_payload()["kind"] == "none"

        assert len(result.pending_uploads) == 1
        pending = result.pending_uploads[0]
        assert pending.filename == "famous-couple.jpg"
        assert pending.description == "A famous couple"

    def test_seeded_questions_pass_model_validation(self, quiz_event):
        """Every seeded row must survive the model's own clean()."""
        populate_quiz(quiz_event)
        for question in QuizQuestion.objects.all():
            question.clean()


class TestPopulateQuizRejectsBadPacks:
    def test_invalid_pack_raises(self, quiz_event, monkeypatch):
        monkeypatch.setitem(PACKS, "tmp", _pack(_question(text_fr="")))
        with pytest.raises(ValueError, match="missing text_fr"):
            populate_quiz(quiz_event, pack="tmp")

    def test_invalid_pack_writes_nothing(self, quiz_event, monkeypatch):
        """Validation runs before any row is written, so no half-seeded quiz."""
        monkeypatch.setitem(
            PACKS,
            "tmp",
            _pack(_question(), _question(text_de="")),
        )
        with pytest.raises(ValueError):
            populate_quiz(quiz_event, pack="tmp")
        assert quiz_event.rounds.count() == 0
        assert QuizQuestion.objects.count() == 0

    def test_unknown_pack_raises_key_error(self, quiz_event):
        with pytest.raises(KeyError):
            populate_quiz(quiz_event, pack="does-not-exist")


# ============================================================================
# COMMAND
# ============================================================================


class TestGenerateCrushQuizCommand:
    def test_seeds_named_pack(self, quiz_event):
        call_command("generate_crush_quiz", quiz_id=quiz_event.pk, pack="classic")
        assert quiz_event.rounds.count() == 6

    def test_refuses_to_overwrite_without_clear(self, quiz_event):
        populate_quiz(quiz_event)
        with pytest.raises(CommandError, match="already has 6 rounds"):
            call_command("generate_crush_quiz", quiz_id=quiz_event.pk)

    def test_clear_replaces_existing_rounds(self, quiz_event):
        populate_quiz(quiz_event)
        call_command("generate_crush_quiz", quiz_id=quiz_event.pk, clear=True)
        assert quiz_event.rounds.count() == 6

    def test_missing_quiz_id_is_an_error(self, db):
        with pytest.raises(CommandError, match="--quiz-id is required"):
            call_command("generate_crush_quiz")

    def test_unknown_quiz_id_is_an_error(self, db):
        with pytest.raises(CommandError, match="does not exist"):
            call_command("generate_crush_quiz", quiz_id=999999)

    def test_list_packs_needs_no_quiz(self, db, capsys):
        call_command("generate_crush_quiz", list_packs=True)
        out = capsys.readouterr().out
        assert "classic" in out
        assert "36 questions" in out


# ============================================================================
# MEDIA-LOVE PACK
# ============================================================================


class TestMediaLovePack:
    """The media edition is content, so the tests guard its shape and links."""

    def test_pack_is_registered(self):
        assert "media-love" in PACKS
        assert "media-love" in pack_names()

    def test_pack_shape_is_six_by_six(self):
        rounds = PACKS["media-love"]
        assert len(rounds) == 6
        assert [len(r["questions"]) for r in rounds] == [6] * 6

    def test_pack_carries_media(self):
        """The whole point of this pack — a stimulus on most questions."""
        rounds = PACKS["media-love"]
        with_media = [q for r in rounds for q in r["questions"] if q.get("media")]
        assert len(with_media) == 18

    def test_every_embed_url_is_allowlisted(self):
        """A URL that slipped off the allowlist would fail live, not in review."""
        from crush_lu.models.quiz import is_embed_url_allowed

        for r in PACKS["media-love"]:
            for q in r["questions"]:
                media = q.get("media") or {}
                if media.get("kind") in ("video", "audio"):
                    assert is_embed_url_allowed(media["url"]), media["url"]

    def test_every_media_block_has_an_accessible_description(self):
        for r in PACKS["media-love"]:
            for q in r["questions"]:
                media = q.get("media") or {}
                if media:
                    assert (media.get("description") or "").strip()

    def test_bonus_round_is_marked_and_scored_higher(self):
        bonus = [r for r in PACKS["media-love"] if r.get("is_bonus")]
        assert len(bonus) == 1
        assert all(q.get("points") == 20 for q in bonus[0]["questions"])

    def test_seeding_splits_embeds_from_pending_images(self, quiz_event):
        result = populate_quiz(quiz_event, pack="media-love")
        assert result.rounds_created == 6
        assert result.questions_created == 36
        # 12 embeds seed onto the row; the 6 photographs come back as a manifest.
        assert len(result.pending_uploads) == 6
        assert QuizQuestion.objects.exclude(media_kind="none").count() == 12

    def test_seeded_questions_pass_model_validation(self, quiz_event):
        populate_quiz(quiz_event, pack="media-love")
        for question in QuizQuestion.objects.all():
            question.clean()


# ============================================================================
# EMPTY PACKS
# ============================================================================


class TestEmptyPackIsRejected:
    """A stub pack must fail loudly rather than seed nothing and report success."""

    def test_validate_reports_an_empty_pack(self):
        assert any("has no rounds" in e for e in validate_pack([]))

    def test_populate_raises_on_an_empty_pack(self, quiz_event, monkeypatch):
        monkeypatch.setitem(PACKS, "empty", [])
        with pytest.raises(ValueError, match="has no rounds"):
            populate_quiz(quiz_event, pack="empty")

    def test_clear_does_not_wipe_an_existing_quiz(self, quiz_event, monkeypatch):
        """The destructive case: --clear deletes before it writes."""
        populate_quiz(quiz_event)
        monkeypatch.setitem(PACKS, "empty", [])
        with pytest.raises(ValueError):
            populate_quiz(quiz_event, clear=True, pack="empty")
        assert quiz_event.rounds.count() == 6


# ============================================================================
# CREATE-EVENT COMMAND
# ============================================================================


class TestCreateQuizNightEventCommand:
    TITLE = "Media Quiz Under Test"

    @pytest.fixture(autouse=True)
    def owner(self, coach_user):
        """QuizEvent.created_by is a non-nullable FK, so a user must exist."""
        return coach_user

    def _run(self, **kwargs):
        options = {"title": self.TITLE, "pack": "media-love"}
        options.update(kwargs)
        call_command("create_quiz_night_event", **options)

    def test_creates_event_quiz_tables_and_questions(self, db):
        from crush_lu.models import MeetupEvent
        from crush_lu.models.quiz import QuizTable

        self._run(tables=4)

        event = MeetupEvent.objects.get(title=self.TITLE)
        assert event.event_type == "quiz_night"
        quiz = QuizEvent.objects.get(event=event)
        assert quiz.rounds.count() == 6
        assert QuizQuestion.objects.filter(round__quiz=quiz).count() == 36
        assert QuizTable.objects.filter(quiz=quiz).count() == 4

    def test_event_is_a_draft_by_default(self, db):
        from crush_lu.models import MeetupEvent

        self._run()
        assert MeetupEvent.objects.get(title=self.TITLE).is_published is False

    def test_publish_flag_publishes(self, db):
        from crush_lu.models import MeetupEvent

        self._run(publish=True)
        assert MeetupEvent.objects.get(title=self.TITLE).is_published is True

    def test_registration_closes_before_the_doors(self, db):
        from crush_lu.models import MeetupEvent

        self._run()
        event = MeetupEvent.objects.get(title=self.TITLE)
        assert event.registration_deadline < event.date_time

    def test_rerun_refuses_to_reseed_without_clear(self, db):
        self._run()
        with pytest.raises(CommandError, match="already has 6 rounds"):
            self._run()

    def test_rerun_with_clear_reuses_the_same_event(self, db):
        from crush_lu.models import MeetupEvent

        self._run()
        self._run(clear=True)
        assert MeetupEvent.objects.filter(title=self.TITLE).count() == 1
        assert QuizQuestion.objects.count() == 36

    def test_explicit_date_is_honoured(self, db):
        from crush_lu.models import MeetupEvent

        self._run(date="2026-09-19T19:30")
        event = MeetupEvent.objects.get(title=self.TITLE)
        assert (event.date_time.year, event.date_time.month) == (2026, 9)

    def test_unparseable_date_is_an_error(self, db):
        with pytest.raises(CommandError, match="Could not parse"):
            self._run(date="next friday")

    def test_too_few_tables_is_an_error(self, db):
        with pytest.raises(CommandError, match="at least 2"):
            self._run(tables=1)

    def test_dry_run_writes_nothing(self, db, capsys):
        from crush_lu.models import MeetupEvent

        self._run(dry_run=True)
        assert not MeetupEvent.objects.filter(title=self.TITLE).exists()
        assert "Dry run" in capsys.readouterr().out

    def test_invalid_pack_is_rejected_before_the_event_exists(self, db, monkeypatch):
        from crush_lu.models import MeetupEvent

        monkeypatch.setitem(PACKS, "empty", [])
        with pytest.raises(CommandError, match="has no rounds"):
            self._run(pack="empty")
        assert not MeetupEvent.objects.filter(title=self.TITLE).exists()

    def test_reports_the_pending_upload_manifest(self, db, capsys):
        self._run()
        out = capsys.readouterr().out
        assert "image question(s) still need a file" in out
        assert "couple-curie.jpg" in out

    def test_records_the_named_owner(self, db, coach_user):
        self._run(created_by=coach_user.username)
        assert QuizEvent.objects.get().created_by == coach_user

    def test_unknown_owner_is_an_error(self, db):
        with pytest.raises(CommandError, match="No user with username"):
            self._run(created_by="nobody@test.com")


class TestCreateQuizNightEventWithoutAnOwner:
    def test_missing_staff_user_is_a_clear_error(self, db):
        """No autouse fixture here: the database has no users at all."""
        with pytest.raises(CommandError, match="No superuser or staff user"):
            call_command("create_quiz_night_event", pack="media-love")
