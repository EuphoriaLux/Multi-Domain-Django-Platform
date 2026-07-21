"""
Seed a labeled, loginable *debug cast* for manual QA of the Crush.lu signup
journey and the core Crush Connect interactions.

Creates ~10 accounts sharing one password, each with a **verified allauth
EmailAddress** so they can actually log in under the project's mandatory
email-verification setting (the other seeders skip this, so their accounts
cannot complete email login).

Signup stages (log in, hit /en/onboarding/, land on the named step):
    debug_new@crush.lu       -> Welcome            (nothing done yet)
    debug_phone@crush.lu     -> Verify number      (welcome seen, phone unverified)
    debug_draft@crush.lu     -> Build profile (4)  (phone verified, mid-build)
    debug_pending@crush.lu   -> Profile submitted  (pending coach review)
    debug_approved@crush.lu  -> Dashboard          (verified member)

Crush Connect cast (asymmetric model — needs a small supporting cast):
    debug_receiver@crush.lu  -> Premium receiver + beta tester; gets Today's Drop
    debug_cand_1..3@crush.lu -> candidates that appear in the receiver's Drop
    debug_sender@crush.lu    -> 2nd Premium actor; sends the pending Spark to receiver
  Seeded interactions: receiver's Drop = the 3 candidates; a mutual MATCH
  (receiver x cand_1, accepted Spark); a PENDING Spark received by the receiver.

LOCAL-ONLY: refuses to run on Azure (WEBSITE_HOSTNAME set) or when DEBUG is
False, unless --force. These are shared, known-password accounts.

Usage:
    python manage.py seed_debug_profiles --reset
    python manage.py seed_debug_profiles --reset --skip-photos
"""

import os

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import CrushProfile, PremiumMembership, ProfileSubmission
from crush_lu.models.crush_connect import (
    ConnectInterest,
    CrushConnectMembership,
    CrushConnectWaitlist,
    CuriositySpark,
)
from crush_lu.models.profiles import CrushCoach, UserDataConsent
from crush_lu.onboarding_connect import TOTAL_STEPS

# Reuse the proven helpers from the Connect seeder rather than duplicating them.
from crush_lu.management.commands.create_connect_test_users import (
    _ensure_spark_prompt,
    _fake_user_data,
    _fetch_user_data,
    _test_dob,
    _weighted_location,
)

_PREFIX = "debug_"
_EMAIL_DOMAIN = "crush.lu"

# Which stages get realistic content fields / an uploaded photo.
_WITH_CONTENT = {"draft", "pending", "approved", "receiver", "sender", "candidate"}
_WITH_PHOTO = {"pending", "approved", "receiver", "sender", "candidate"}
_CONNECT_ROLES = {"receiver", "sender", "candidate"}


class Command(BaseCommand):
    help = (
        "Seed ~10 loginable debug accounts covering every signup stage and the "
        "core Crush Connect interactions (drop, match, pending spark)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="debug2025",
            help="Password for all created users (default: debug2025)",
        )
        parser.add_argument(
            "--skip-photos",
            action="store_true",
            help="Skip randomuser.me photo download (faster, offline)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing debug_* users first",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Override the local-only safety guard (NOT for production)",
        )

    # ------------------------------------------------------------------ #

    def handle(self, *args, **options):
        self._guard_local(force=options["force"])

        password = options["password"]
        skip_photos = options["skip_photos"]
        self._phone_seq = 0

        if options["reset"]:
            self._delete_existing()

        coach = self._ensure_coach(skip_photos)
        prompt = _ensure_spark_prompt()

        self.stdout.write("\nCreating signup-stage accounts...")
        for suffix, stage, gender in (
            ("new", "new", "female"),
            ("phone", "phone", "male"),
            ("draft", "draft", "female"),
            ("pending", "pending", "male"),
            ("approved", "approved", "female"),
        ):
            self._create_account(
                suffix,
                gender=gender,
                stage=stage,
                password=password,
                skip_photos=skip_photos,
            )

        self.stdout.write("\nCreating Crush Connect cast...")
        receiver = self._create_account(
            "receiver",
            gender="male",
            stage="receiver",
            password=password,
            skip_photos=skip_photos,
            coach=coach,
            prompt=prompt,
        )
        sender = self._create_account(
            "sender",
            gender="female",
            stage="sender",
            password=password,
            skip_photos=skip_photos,
            coach=coach,
            prompt=prompt,
        )
        candidates = [
            self._create_account(
                f"cand_{i}",
                gender="female" if i % 2 else "male",
                stage="candidate",
                password=password,
                skip_photos=skip_photos,
                prompt=prompt,
            )
            for i in range(1, 4)
        ]

        self._wire_connect(receiver, sender, candidates)
        self._print_summary(password)

    # ------------------------------------------------------------------ #
    # Safety + reset
    # ------------------------------------------------------------------ #

    def _guard_local(self, *, force):
        if force:
            return
        on_azure = bool(os.environ.get("WEBSITE_HOSTNAME"))
        if on_azure or not settings.DEBUG:
            raise CommandError(
                "Refusing to seed shared debug accounts outside local dev "
                "(WEBSITE_HOSTNAME is set or DEBUG=False). Pass --force to override."
            )

    def _delete_existing(self):
        from django.db import connection

        user_ids = list(
            User.objects.filter(username__startswith=_PREFIX).values_list(
                "id", flat=True
            )
        )
        if user_ids:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name LIKE 'crush_empire_%%'"
                )
                tables = [row[0] for row in cursor.fetchall()]
                for table in tables:
                    cursor.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s AND column_name='user_id'",
                        [table],
                    )
                    if cursor.fetchone():
                        cursor.execute(
                            f"DELETE FROM {table} WHERE user_id = ANY(%s)",
                            [user_ids],
                        )

        qs = User.objects.filter(username__startswith=_PREFIX)
        count = qs.count()
        qs.delete()  # cascades to profile, membership, waitlist, sparks, drops, email
        self.stdout.write(f"Deleted {count} existing debug_* users.")

    def _ensure_coach(self, skip_photos):
        coach = CrushCoach.objects.filter(is_active=True).first()
        if coach is None:
            self.stdout.write("No active coach found — running create_crush_coaches...")
            call_command("create_crush_coaches", skip_photos=skip_photos)
            coach = CrushCoach.objects.filter(is_active=True).first()
        if coach is None:
            raise CommandError(
                "Could not obtain an active CrushCoach (create_crush_coaches failed)."
            )
        return coach

    # ------------------------------------------------------------------ #
    # Account creation
    # ------------------------------------------------------------------ #

    def _next_phone(self):
        self._phone_seq += 1
        return f"+352 661 {self._phone_seq:03d} 100"

    def _create_account(
        self, suffix, *, gender, stage, password, skip_photos, coach=None, prompt=None
    ):
        username = f"{_PREFIX}{suffix}"
        email = f"{username}@{_EMAIL_DOMAIN}"

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"  skip {email} (exists — use --reset to rebuild)")
            return User.objects.get(username=username)

        want_photo = stage in _WITH_PHOTO and not skip_photos
        user_data = _fetch_user_data(gender) if want_photo else _fake_user_data(gender)
        gender_code = "M" if gender == "male" else "F"
        now = timezone.now()

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=user_data["first_name"],
            last_name=user_data["last_name"],
        )
        # Recent activity so candidates pass the 30-day inactivity cutoff.
        User.objects.filter(pk=user.pk).update(last_login=now)

        # The piece every other seeder omits: a verified email so login works
        # under ACCOUNT_EMAIL_VERIFICATION = "mandatory".
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)

        # Consent gate: CrushConsentMiddleware bounces any authenticated user
        # without recorded consent to /consent/confirm/ on every page. Real
        # users consent at signup, so record it here (else all debug accounts
        # get stuck on the consent page instead of their target).
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"crushlu_consent_given": True, "crushlu_consent_date": now},
        )

        kwargs = self._profile_kwargs(stage, gender_code, now, coach)
        kwargs["user"] = user
        # Phone fields must be set at create() time — the model's save() locks
        # them on update (crush_lu/models/profiles.py save() phone-lock).
        profile = CrushProfile.objects.create(**kwargs)

        if want_photo and user_data.get("photo_data"):
            try:
                profile.photo_1.save(
                    f"{user.pk}_photo1.jpg",
                    ContentFile(user_data["photo_data"]),
                    save=True,
                )
            except Exception as exc:  # network / storage hiccup — non-fatal
                self.stderr.write(f"  photo save failed for {email}: {exc}")

        self._create_submission(profile, stage, now)
        if stage in _CONNECT_ROLES:
            self._wire_connect_membership(user, stage, prompt, now)

        self.stdout.write(f"  {stage:<9} {email}")
        return user

    def _profile_kwargs(self, stage, gender_code, now, coach):
        """Field recipe per stage (caller adds `user`). Gates are read by
        crush_lu/onboarding.py get_current_step (first failing gate wins)."""
        kwargs = {"preferred_language": "en"}

        # --- Journey markers + verification status ---
        if stage == "new":
            kwargs.update(
                welcome_seen_at=None,
                coach_intro_seen_at=None,
                phone_verified=False,
                verification_status="incomplete",
            )
        elif stage == "phone":
            kwargs.update(
                welcome_seen_at=now,
                coach_intro_seen_at=None,
                phone_verified=False,
                verification_status="incomplete",
            )
        elif stage == "draft":
            kwargs.update(
                welcome_seen_at=now,
                coach_intro_seen_at=now,
                phone_number=self._next_phone(),
                phone_verified=True,
                phone_verified_at=now,
                verification_status="incomplete",
                draft_data={"step": 2, "note": "half-filled debug draft"},
            )
        else:  # pending / approved / receiver / sender / candidate
            kwargs.update(
                welcome_seen_at=now,
                coach_intro_seen_at=now,
                phone_number=self._next_phone(),
                phone_verified=True,
                phone_verified_at=now,
            )
            if stage == "pending":
                kwargs.update(
                    verification_status="pending",
                    completion_status="submitted",
                    is_approved=False,
                )
            else:
                # Realistic per-persona verification method: candidates self-serve
                # via LuxID, Premium (receiver/sender) go through paid coach review,
                # the plain approved member was verified in person at an event.
                method = {
                    "candidate": "luxid",
                    "receiver": "premium_coach",
                    "sender": "premium_coach",
                }.get(stage, "coach_event")
                kwargs.update(
                    is_approved=True,
                    is_active=True,
                    approved_at=now,
                    verification_status="verified",
                    verification_method=method,
                    completion_status="submitted",
                )

        # --- Content fields ---
        kwargs.update(gender=gender_code, date_of_birth=_test_dob())
        if stage in _WITH_CONTENT:
            kwargs.update(
                location=_weighted_location(),
                bio=f"Debug {stage} account — local QA only.",
                interests="hiking, photography, travel",
                show_full_name=True,
                show_exact_age=True,
                event_languages=["en"],
            )

        # --- Premium (receiver track) ---
        if stage in ("receiver", "sender"):
            kwargs.update(assigned_coach=coach, assigned_coach_at=now)

        return kwargs

    def _create_submission(self, profile, stage, now):
        # Match the current flow (crush_lu/views.py create_profile ~line 678): a
        # fresh "pending" submit creates NO ProfileSubmission, and LuxID / event-
        # verified members never get one either. The row exists only for the paid
        # coach-review path — so only the Premium accounts carry an approved
        # submission with a completed review call.
        if stage in ("receiver", "sender"):
            ProfileSubmission.objects.create(
                profile=profile,
                coach=profile.assigned_coach,
                status="approved",
                coach_notes="Auto-created debug profile (paid coach review)",
                review_call_completed=True,
                review_call_date=now,
                reviewed_at=now,
            )
            # The receiver gate keys off an ACTIVE PremiumMembership, not
            # assigned_coach — give receiver/sender seeds the real entitlement.
            PremiumMembership.objects.create(
                user=profile.user,
                coach=profile.assigned_coach,
                status="active",
                payment_confirmed=True,
                payment_date=now,
            )

    def _wire_connect_membership(self, user, stage, prompt, now):
        membership = CrushConnectMembership.objects.create(
            user=user,
            onboarded_at=now,
            # The real wizard sets onboarded_at only at the LAST step, alongside
            # onboarding_step=TOTAL_STEPS (views_crush_connect.py). Set both so a
            # seeded member reads as fully onboarded, not "in the mix at step 1".
            onboarding_step=TOTAL_STEPS,
            onboarding_started_at=now,
            photo_share_consent=True,  # Read-the-Photo consent — required to surface
            languages=["en", "fr"],
            story_prompt=prompt,
            story_answer=(
                "Looking for a genuine connection"
                if stage != "candidate"
                else "Open to meeting interesting people"
            ),
        )
        self._assign_gate_questions(membership, now)
        # The wizard also collects interests — give them a few so the profile
        # isn't empty (otherwise they look half-onboarded).
        membership.interests.set(ConnectInterest.objects.order_by("?")[:3])

        # LuxID makes them catalogue-eligible. The receiver needs it too, so it
        # can appear in the sender's Drop and thus receive the pending Spark.
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(
            user=user,
            provider="luxid",
            uid=f"luxid-debug-{user.pk}",
            extra_data={"sub": f"luxid-debug-{user.pk}"},
        )

        # Beta receiver gate: connect_phase.receiver_access_open() lets a
        # selected waitlist tester reach Today's Drop in the beta phase.
        # (The CrushConnectWaitlist model docstring predates this and is stale.)
        if stage in ("receiver", "sender"):
            CrushConnectWaitlist.objects.create(
                user=user, selected_as_tester=True, notification_preference=True
            )

    def _assign_gate_questions(self, membership, now):
        from crush_lu.models import MemberGateQuestion
        from crush_lu.services.crush_connect import get_or_create_question_week

        week = get_or_create_question_week()
        for position, question in enumerate(
            week.questions.filter(is_active=True)[:3], start=1
        ):
            MemberGateQuestion.objects.create(
                membership=membership,
                question=question,
                position=position,
                owner_answer=(position % 2 == 1),  # alternate Yes/No
                picked_week=week,
            )

    # ------------------------------------------------------------------ #
    # Connect interaction wiring
    # ------------------------------------------------------------------ #

    def _wire_connect(self, receiver, sender, candidates):
        from crush_lu.services.crush_connect import get_or_create_daily_drop

        self.stdout.write("\nWiring Connect interactions...")

        # 1) Receiver's Drop = the 3 candidates (deterministic, override the
        #    weighted sample so the debug view is predictable). Use the service
        #    so the drop_date matches what the view looks up (06:00 unlock rule).
        receiver_drop = get_or_create_daily_drop(receiver)
        receiver_drop.recipients.set(candidates)
        self.stdout.write(
            f"  {receiver.email}: Drop -> " + ", ".join(c.email for c in candidates)
        )

        # 2) Mutual match: receiver x cand_1 (accepted spark). Rows created
        #    directly (idempotent) to avoid notification side effects.
        CuriositySpark.objects.get_or_create(
            sender=receiver,
            recipient=candidates[0],
            defaults=dict(
                drop=receiver_drop,
                message="Your hiking photos caught my eye — coffee this week?",
                status="accepted",
                responded_at=timezone.now(),
            ),
        )
        self.stdout.write(f"  match: {receiver.email} x {candidates[0].email}")

        # 3) Pending spark RECEIVED by the receiver, from the sender. The
        #    sparks-received view requires the recipient (receiver) to have
        #    appeared in the sender's Drop, so add it explicitly.
        sender_drop = get_or_create_daily_drop(sender)
        sender_drop.recipients.add(receiver)
        CuriositySpark.objects.get_or_create(
            sender=sender,
            recipient=receiver,
            defaults=dict(
                drop=sender_drop,
                message="Loved your story answer — curious to know more!",
                status="pending",
            ),
        )
        self.stdout.write(f"  pending spark: {sender.email} -> {receiver.email}")

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def _print_summary(self, password):
        rows = [
            ("debug_new", "Welcome step", "/en/onboarding/"),
            ("debug_phone", "Verify-number step", "/en/onboarding/"),
            ("debug_draft", "Build-profile (step 4)", "/en/onboarding/"),
            ("debug_pending", "Submitted / pending", "/en/onboarding/"),
            ("debug_approved", "Dashboard (verified)", "/en/dashboard/"),
            (
                "debug_receiver",
                "Today's Drop (3 cards) + match + spark received",
                "/en/crush-connect/home/",
            ),
            ("debug_cand_1", "Candidate (matched w/ receiver)", "/en/dashboard/"),
            ("debug_cand_2", "Candidate in the pool", "/en/dashboard/"),
            ("debug_cand_3", "Candidate in the pool", "/en/dashboard/"),
            (
                "debug_sender",
                "Premium (sent pending spark to receiver)",
                "/en/crush-connect/home/",
            ),
        ]
        self.stdout.write("\n" + "=" * 78)
        self.stdout.write(self.style.SUCCESS("  Debug cast ready"))
        self.stdout.write("=" * 78)
        self.stdout.write(f"\n  Password for all accounts: {password}\n")
        self.stdout.write(f"  {'Email':<26}{'What to verify':<48}URL")
        self.stdout.write(f"  {'-' * 24:<26}{'-' * 46:<48}{'-' * 20}")
        for name, what, url in rows:
            self.stdout.write(f"  {name + '@crush.lu':<26}{what:<48}{url}")

        launched = getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
        candidate_open = getattr(settings, "CRUSH_CONNECT_CANDIDATE_OPEN", False)
        self.stdout.write("\n" + "-" * 78)
        self.stdout.write("Crush Connect phase (from settings):")
        self.stdout.write(
            f"  CRUSH_CONNECT_LAUNCHED={launched}   "
            f"CRUSH_CONNECT_CANDIDATE_OPEN={candidate_open}"
        )
        if not launched and not candidate_open:
            self.stdout.write(
                self.style.WARNING(
                    "  Connect surfaces are CLOSED (prelaunch). To debug the beta, add to "
                    ".env:\n    CRUSH_CONNECT_CANDIDATE_OPEN=true\n"
                    "  (debug_receiver/debug_sender are seeded beta testers), or "
                    "CRUSH_CONNECT_LAUNCHED=true to open everything — then restart runserver."
                )
            )
        elif not launched and candidate_open:
            self.stdout.write(
                "  Beta phase active — debug_receiver (seeded tester) can reach Today's Drop."
            )
        else:
            self.stdout.write("  Fully launched — all Connect surfaces open.")
        self.stdout.write("-" * 78 + "\n")
