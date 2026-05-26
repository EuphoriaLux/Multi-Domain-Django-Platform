"""
Local development tool: simulate a LuxID identity verification for a user.

This bypasses the real OAuth flow and directly exercises the auto-approval
signal path, so you can test the LuxID fast-lane without LuxID credentials.

What it does:
  1. Ensures a stub LuxID SocialApp exists on the current site
     (so the CTA appears on /profile-submitted/).
  2. Creates a SocialAccount(provider="luxid") for the user, which is
     what social_account_added fires on after a real OAuth connect.
  3. Sets the is_crush_luxid_login thread-local flag (normally set by
     pre_social_login / update_crush_profile_from_luxid).
  4. Sends social_account_added to trigger auto_approve_profile_on_luxid_connect.

Usage:
    python manage.py simulate_luxid_verify user@example.com
    python manage.py simulate_luxid_verify user@example.com --setup-only
    python manage.py simulate_luxid_verify --list-pending
"""
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Simulate LuxID identity verification for local development/testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "email",
            nargs="?",
            help="Email of the user to simulate LuxID verification for",
        )
        parser.add_argument(
            "--setup-only",
            action="store_true",
            help="Only create the stub LuxID SocialApp (makes CTA appear); do not simulate approval",
        )
        parser.add_argument(
            "--list-pending",
            action="store_true",
            help="List all users with pending ProfileSubmissions",
        )

    def handle(self, *args, **options):
        if options["list_pending"]:
            self._list_pending()
            return

        stub_app = self._ensure_luxid_app()

        if options["setup_only"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nDone. LuxID CTA will now appear on /profile-submitted/ for "
                    "users with a pending submission and no linked LuxID account.\n"
                    "Visit http://localhost:8000/en/profile-submitted/ while logged "
                    "in as a pending-profile user to see it."
                )
            )
            return

        email = options.get("email")
        if not email:
            raise CommandError(
                "Provide a user email, or use --setup-only / --list-pending."
            )

        self._simulate_verify(email, stub_app)

    # ------------------------------------------------------------------

    def _ensure_luxid_app(self):
        from allauth.socialaccount.models import SocialApp

        # SITE_ID is not set (sites are determined per-request via middleware),
        # so look up the crush.lu site directly by domain.
        site = Site.objects.filter(domain__in=("localhost", "crush.lu")).first()
        if site is None:
            site = Site.objects.first()
        app, created = SocialApp.objects.get_or_create(
            provider="openid_connect",
            provider_id="luxid",
            defaults={
                "name": "LuxID (Dev Stub)",
                "client_id": "dev-stub-client-id",
                "secret": "dev-stub-secret",  # nosec B105 - intentional dev-only stub, not a real credential  # gitleaks:allow
                "settings": {
                    "server_url": "https://luxid.gov.lu/.well-known/openid-configuration",
                },
            },
        )
        if site not in app.sites.all():
            app.sites.add(site)
        label = "created" if created else "already exists"
        self.stdout.write(f"  LuxID SocialApp ({label}) — id={app.pk}, site={site.domain}")
        return app

    def _list_pending(self):
        from crush_lu.models.profiles import ProfileSubmission

        subs = ProfileSubmission.objects.filter(status="pending").select_related(
            "profile__user"
        )
        if not subs.exists():
            self.stdout.write(self.style.WARNING("No pending submissions found."))
            return

        self.stdout.write(f"\n{'Email':<40} {'Profile pk':<12} {'Submitted'}")
        self.stdout.write("-" * 70)
        for sub in subs:
            email = sub.profile.user.email
            self.stdout.write(
                f"{email:<40} {sub.profile.pk:<12} {sub.submitted_at.strftime('%Y-%m-%d %H:%M')}"
            )

    def _simulate_verify(self, email, stub_app):
        from allauth.socialaccount.models import SocialAccount
        from allauth.socialaccount.signals import social_account_added
        from django.contrib.sessions.backends.db import SessionStore

        from crush_lu import signals as crush_signals
        from crush_lu.models import CrushProfile
        from crush_lu.models.profiles import ProfileSubmission

        # --- resolve user ---
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No user with email '{email}'.")

        # --- sanity checks ---
        try:
            profile = CrushProfile.objects.get(user=user)
        except CrushProfile.DoesNotExist:
            raise CommandError(f"User '{email}' has no CrushProfile.")

        if profile.is_approved:
            self.stdout.write(self.style.WARNING(
                f"  Profile for '{email}' is already approved — nothing to do."
            ))
            return

        if not ProfileSubmission.objects.filter(profile=profile, status="pending").exists():
            raise CommandError(
                f"No pending ProfileSubmission for '{email}'. "
                "Submit the profile first (via the /create-profile/ flow), "
                "or use --list-pending to see eligible users."
            )

        self.stdout.write(f"\nSimulating LuxID connect for: {email}")

        # --- create SocialAccount (represents post-OAuth state) ---
        sa, sa_created = SocialAccount.objects.get_or_create(
            user=user,
            provider="openid_connect",
            defaults={"uid": f"dev-luxid-{user.pk}"},
        )
        if not sa_created:
            self.stdout.write(self.style.WARNING(
                "  SocialAccount already exists for this user — reusing it."
            ))
        else:
            self.stdout.write(f"  Created SocialAccount uid={sa.uid}")

        # --- build a minimal request (crush.lu domain + session) ---
        from django.test import RequestFactory
        request = RequestFactory().get("/", HTTP_HOST="localhost")
        request.session = SessionStore()

        # --- set the thread-local flag that pre_social_login normally sets ---
        crush_signals._thread_local.is_crush_luxid_login = True

        # --- build a minimal sociallogin object the signal reads ---
        from unittest.mock import MagicMock
        account_mock = MagicMock()
        account_mock.provider = "openid_connect"
        sociallogin = MagicMock()
        sociallogin.user = user
        sociallogin.account = account_mock

        try:
            social_account_added.send(
                sender=SocialAccount,
                request=request,
                sociallogin=sociallogin,
            )
        finally:
            crush_signals._thread_local.is_crush_luxid_login = False

        # --- report result ---
        profile.refresh_from_db()
        submission = ProfileSubmission.objects.filter(profile=profile).latest("submitted_at")

        if profile.is_approved and submission.status == "approved":
            self.stdout.write(self.style.SUCCESS(
                f"\n  Profile approved!\n"
                f"  submission.status  = {submission.status}\n"
                f"  submission.coach_notes = {submission.coach_notes!r}\n"
                f"  profile.is_approved = {profile.is_approved}\n"
                f"  profile.approved_at = {profile.approved_at}\n"
                f"\n  Now visit /en/profile-submitted/ as this user to see the approved state."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"  Approval did NOT happen. "
                f"submission.status={submission.status}, "
                f"profile.is_approved={profile.is_approved}. "
                f"Check server logs for [LUXID-AUTO-APPROVE] entries."
            ))
