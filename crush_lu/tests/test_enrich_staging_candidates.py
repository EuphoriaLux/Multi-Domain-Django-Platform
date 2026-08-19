import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.crush_connect import CrushConnectMembership
from crush_lu.models.profiles import CrushCoach

User = get_user_model()


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
