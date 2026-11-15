import secrets
from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from crush_lu.management.commands import enrich_staging_candidates as enrich_command
from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.crush_connect import CrushConnectMembership
from crush_lu.models.profiles import CrushCoach

User = get_user_model()


def _fake_get(*args, **kwargs):
    class FakeResp:
        status_code = 200
        content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"

    return FakeResp()


@pytest.mark.django_db
class TestEnrichStagingCandidatesCommand:
    def test_enrich_existing_candidate(self, monkeypatch):
        # Mock requests.get so test runs hermetically without network
        def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200
                content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"

            return FakeResp()

        monkeypatch.setattr("requests.get", fake_get)

        # Create coach and initial un-enriched candidate
        coach_user = User.objects.create_user(
            username="test_coach", email="coach@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        user = User.objects.create_user(
            username="connect_candidate_1",
            email="connect_candidate_1@crush.lu",
            first_name="OldName",
            last_name="OldLast",
        )
        profile = CrushProfile.objects.create(
            user=user,
            is_approved=False,
            verification_status="incomplete",
        )

        call_command("enrich_staging_candidates", email="connect_candidate_1@crush.lu")

        user.refresh_from_db()
        profile.refresh_from_db()

        assert user.first_name != "OldName"
        assert profile.is_approved is True
        assert profile.verification_status == "verified"
        assert profile.phone_verified is True
        assert profile.location in [
            "Luxembourg City",
            "Esch-sur-Alzette",
            "Echternach",
            "Mersch",
            "Differdange",
            "Remich",
            "Dudelange",
            "Strassen",
            "Mamer",
        ]
        assert bool(profile.photo_1) is True

        membership = CrushConnectMembership.objects.get(user=user)
        assert membership.photo_share_consent is True
        assert membership.onboarded_at is not None
        assert bool(membership.story_answer) is True

    def test_create_missing_candidates(self, monkeypatch):
        def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200
                content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"

            return FakeResp()

        monkeypatch.setattr("requests.get", fake_get)

        coach_user = User.objects.create_user(
            username="test_coach2", email="coach2@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        call_command("enrich_staging_candidates", create_missing=4)

        candidates = User.objects.filter(username__startswith="connect_candidate_")
        assert candidates.count() >= 4

        for cand in candidates:
            p = cand.crushprofile
            assert p.is_approved is True
            assert p.verification_status == "verified"
            assert bool(p.photo_1) is True

    def test_leave_pending_for_coach_testing(self, monkeypatch):
        def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200
                content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"

            return FakeResp()

        monkeypatch.setattr("requests.get", fake_get)

        coach_user = User.objects.create_user(
            username="test_coach3", email="coach3@crush.lu"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)

        call_command("enrich_staging_candidates", create_missing=3, leave_pending=2)

        pending_subs = ProfileSubmission.objects.filter(status="pending")
        assert pending_subs.count() == 2
        for sub in pending_subs:
            assert sub.profile.verification_status == "pending"
            assert sub.profile.is_approved is False
            assert sub.coach == coach

    def test_all_without_candidates_does_not_touch_real_profiles(self, monkeypatch):
        """Regression test for the claude-review finding on PR #899 (#2).

        With no candidate-named accounts and no --include-any-profile, --all
        must leave real, unrelated non-staff profiles completely untouched
        -- not silently overwrite their bio/photos/verification/password.
        """
        monkeypatch.setattr("requests.get", _fake_get)

        coach_user = User.objects.create_user(
            username="test_coach4", email="coach4@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        real_user = User.objects.create_user(
            username="a_real_tester",
            email="real.tester@example.com",
            first_name="Real",
            last_name="Tester",
        )
        real_profile = CrushProfile.objects.create(
            user=real_user,
            is_approved=False,
            verification_status="incomplete",
        )
        real_user.set_password("their-own-chosen-password")
        real_user.save()
        original_password_hash = real_user.password

        call_command("enrich_staging_candidates", all=True)

        real_user.refresh_from_db()
        real_profile.refresh_from_db()
        assert real_user.first_name == "Real"
        assert real_user.password == original_password_hash
        assert real_profile.is_approved is False
        assert real_profile.verification_status == "incomplete"
        assert not CrushConnectMembership.objects.filter(user=real_user).exists()

    def test_all_with_include_any_profile_opt_in_falls_back(self, monkeypatch):
        """The fallback still works -- but only when explicitly requested."""
        monkeypatch.setattr("requests.get", _fake_get)

        coach_user = User.objects.create_user(
            username="test_coach5", email="coach5@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        real_user = User.objects.create_user(
            username="a_real_tester2", email="real.tester2@example.com"
        )
        CrushProfile.objects.create(
            user=real_user, is_approved=False, verification_status="incomplete"
        )

        call_command("enrich_staging_candidates", all=True, include_any_profile=True)

        real_user.refresh_from_db()
        assert real_user.crushprofile.is_approved is True
        assert real_user.crushprofile.verification_status == "verified"

    def test_password_is_random_per_run_when_not_specified(self, monkeypatch):
        """No fixed default -- a hardcoded password would be a predictable,
        publicly-visible credential (this file is in the OSS repo) for
        every seeded account on a live-reachable site.
        """
        monkeypatch.setattr("requests.get", _fake_get)

        coach_user = User.objects.create_user(
            username="test_coach6", email="coach6@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        generated = []
        real_token_urlsafe = secrets.token_urlsafe

        def recording_token_urlsafe(*args, **kwargs):
            value = real_token_urlsafe(*args, **kwargs)
            generated.append(value)
            return value

        monkeypatch.setattr(
            enrich_command,
            "secrets",
            SimpleNamespace(token_urlsafe=recording_token_urlsafe),
        )

        out1, out2 = StringIO(), StringIO()
        call_command("enrich_staging_candidates", create_missing=1, stdout=out1)
        first = User.objects.get(username="connect_candidate_1")
        assert first.check_password(generated[0])
        call_command("enrich_staging_candidates", create_missing=2, stdout=out2)

        assert len(generated) == 2
        first_password, second_password = generated
        assert first_password != second_password
        assert len(first_password) >= 12
        assert "connect2025" not in out1.getvalue()

    def test_generated_password_and_emails_never_printed(self, monkeypatch):
        """Command output ends up in logs other people can read (CI runs,
        Kudu consoles): log usernames, never the password or the account
        email."""
        monkeypatch.setattr("requests.get", _fake_get)
        monkeypatch.setattr(
            enrich_command,
            "secrets",
            SimpleNamespace(token_urlsafe=lambda *a, **kw: "generated-secret-XYZ"),
        )

        coach_user = User.objects.create_user(
            username="test_coach7", email="coach7@crush.lu"
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)

        out, err = StringIO(), StringIO()
        call_command(
            "enrich_staging_candidates", create_missing=1, stdout=out, stderr=err
        )

        user = User.objects.get(username="connect_candidate_1")
        assert user.check_password("generated-secret-XYZ")
        for stream in (out.getvalue(), err.getvalue()):
            assert "generated-secret-XYZ" not in stream
            assert "connect_candidate_1@crush.lu" not in stream
        assert "connect_candidate_1" in out.getvalue()
