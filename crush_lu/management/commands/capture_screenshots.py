"""
Capture a review gallery of Crush.lu pages.

Logs in via session injection (no login form), visits a curated list of the
public + member-journey + onboarding pages at mobile and desktop widths, saves
full-page PNGs into a timestamped folder, and builds an ``index.html`` gallery
(mobile + desktop side by side) so a human can scan the whole site's look in
one place.

This is a review aid, not a test: it drives a *separately running* dev server
with Playwright. Start the server first, then run this command.

Setup (one-time):
    .venv-1/Scripts/python.exe -m playwright install chromium

Usage:
    # Terminal 1 - seed sample data, then serve crush.lu on :8000
    .venv-1/Scripts/python.exe manage.py setup_local_dev
    .venv-1/Scripts/python.exe manage.py runserver

    # Terminal 2 - capture
    .venv-1/Scripts/python.exe manage.py capture_screenshots
    .venv-1/Scripts/python.exe manage.py capture_screenshots --pages public
    .venv-1/Scripts/python.exe manage.py capture_screenshots --viewports mobile

Output lands in screenshots/crush_lu/<timestamp>/ (gitignored).
"""

import html
from importlib import import_module
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
)
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from crush_lu.models.connections import EventConnection
from crush_lu.models.crush_connect import CrushConnectMembership
from crush_lu.models.events import MeetupEvent
from crush_lu.models.journey import (
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
)
from crush_lu.models.profiles import (
    CrushCoach,
    CrushProfile,
    ProfileSubmission,
    SpecialUserExperience,
    UserDataConsent,
)

# Viewport presets: (width, height). Mobile ~ iPhone 14, desktop ~ laptop.
VIEWPORTS = {
    "mobile": {"width": 390, "height": 844},
    "desktop": {"width": 1440, "height": 900},
}

# Capture users (user_key on each page picks which session is injected).
#   None         -> anonymous (public pages)
#   "member"     -> approved + consented member
#   "onboarding" -> pre-submission user (steps 1-4, incl. the create-profile
#                   builder form, which only renders before a profile is submitted)
#   "review"     -> post-submission user whose profile is under coach review
#                   (renders meet-coach / screening-call / profile-submitted)
ANON = None
MEMBER = "member"
ONBOARDING = "onboarding"
REVIEW = "review"
# Staff user with an onboarded Crush Connect membership. Crush Connect gates
# every page behind beta enrolment, but staff bypass the gates "to preview the
# UI in any state" (views_crush_connect), so this persona renders the real
# hub/today/catalogue without disturbing the member persona's other pages.
CC = "cc"

# Curated page list: (section, name, path_template, user_key).
# path_template may contain {event_id}/{connection_id}; pages whose id can't be
# resolved are skipped and reported (never silently dropped).
PAGES = [
    # ── Public (no login) ──────────────────────────────────────────────────
    ("Public", "home", "/", ANON),
    ("Public", "about", "/about/", ANON),
    ("Public", "how-it-works", "/how-it-works/", ANON),
    ("Public", "crush-coach", "/crush-coach/", ANON),
    ("Public", "crush-connect-teaser", "/crush-connect/", ANON),
    ("Public", "membership", "/membership/", ANON),
    ("Public", "login", "/login/", ANON),
    ("Public", "signup", "/signup/", ANON),
    ("Public", "privacy-policy", "/privacy-policy/", ANON),
    ("Public", "terms-of-service", "/terms-of-service/", ANON),
    ("Public", "data-deletion", "/data-deletion/", ANON),
    ("Public", "changelog", "/changelog/", ANON),
    # ── Member journey ─────────────────────────────────────────────────────
    ("Member", "dashboard", "/dashboard/", MEMBER),
    ("Member", "profile-edit", "/profile/edit/", MEMBER),
    ("Member", "preferences", "/profile/preferences/", MEMBER),
    ("Member", "account-settings", "/account/settings/", MEMBER),
    ("Member", "gdpr", "/account/gdpr/", MEMBER),
    ("Member", "events", "/events/", MEMBER),
    ("Member", "my-events", "/my-events/", MEMBER),
    ("Member", "event-detail", "/events/{event_id}/", MEMBER),
    ("Member", "event-register", "/events/{event_id}/register/", MEMBER),
    ("Member", "connections", "/connections/", MEMBER),
    ("Member", "connection-detail", "/connections/{connection_id}/", MEMBER),
    ("Member", "notifications", "/notifications/", MEMBER),
    ("Member", "journey-select", "/journey/select/", MEMBER),
    ("Member", "journey-wonderland", "/journey/wonderland/", MEMBER),
    ("Member", "polls", "/polls/", MEMBER),
    # ── Crush Connect (staff-preview persona renders the real feature UI) ──
    ("Crush Connect", "cc-hub", "/crush-connect/home/", CC),
    ("Crush Connect", "cc-today", "/crush-connect/today/", CC),
    ("Crush Connect", "cc-catalogue", "/crush-connect/catalogue/", CC),
    ("Crush Connect", "cc-profile", "/crush-connect/profile/", CC),
    # ── Onboarding wizard (pre-submission user → real builder form) ─────────
    ("Onboarding", "welcome", "/welcome/", ONBOARDING),
    ("Onboarding", "onboarding-phone", "/onboarding/phone/", ONBOARDING),
    ("Onboarding", "onboarding-coach-intro", "/onboarding/coach-intro/", ONBOARDING),
    ("Onboarding", "create-profile", "/create-profile/", ONBOARDING),
    # ── Coach review (post-submission user) ────────────────────────────────
    ("Review", "onboarding-meet-coach", "/onboarding/meet-coach/", REVIEW),
    ("Review", "onboarding-screening-call", "/onboarding/screening-call/", REVIEW),
    ("Review", "profile-submitted", "/profile-submitted/", REVIEW),
]

# Which named page set maps to which user keys.
PAGE_SETS = {
    "public": {ANON},
    "member": {MEMBER, CC},
    "onboarding": {ONBOARDING, REVIEW},
    "all": {ANON, MEMBER, CC, ONBOARDING, REVIEW},
}

# Paths that signal a page bounced us off the requested view.
REDIRECT_GATES = ("/accounts/login", "/login/", "/consent/", "/account/banned")


class Command(BaseCommand):
    help = "Capture a mobile+desktop screenshot review gallery of Crush.lu pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default="http://localhost:8000",
            help="URL of the running dev server (default: http://localhost:8000)",
        )
        parser.add_argument(
            "--output-dir",
            default="screenshots/crush_lu",
            help="Base output directory (a timestamped subfolder is created).",
        )
        parser.add_argument(
            "--viewports",
            default="mobile,desktop",
            help="Comma-separated viewports to capture: mobile, desktop.",
        )
        parser.add_argument(
            "--lang",
            default="en",
            help="Language prefix for user-facing pages (en, de, fr).",
        )
        parser.add_argument(
            "--pages",
            default="all",
            choices=sorted(PAGE_SETS.keys()),
            help="Which page set to capture (default: all).",
        )
        parser.add_argument(
            "--member-user",
            default=None,
            help="Username/email of the member capture user (default: testuser1 or a created one).",
        )
        parser.add_argument(
            "--onboarding-user",
            default="screenshot_onboarding@crush.lu",
            help="Username of the onboarding capture user (created if missing).",
        )

    # ── Entry point ────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CommandError(
                "Playwright is not available. Install it with:\n"
                "  .venv-1/Scripts/python.exe -m playwright install chromium"
            ) from exc

        base_url = options["base_url"].rstrip("/")
        lang = options["lang"]
        wanted_users = PAGE_SETS[options["pages"]]

        viewports = [v.strip() for v in options["viewports"].split(",") if v.strip()]
        bad = [v for v in viewports if v not in VIEWPORTS]
        if bad:
            raise CommandError(
                f"Unknown viewport(s): {', '.join(bad)}. Choose from: {', '.join(VIEWPORTS)}"
            )

        # Resolve capture users + sample object ids (ORM access).
        sessions = {}  # user_key -> session cookie value
        if MEMBER in wanted_users:
            sessions[MEMBER] = self._session_for(
                self._ensure_member_user(options["member_user"])
            )
        if ONBOARDING in wanted_users:
            sessions[ONBOARDING] = self._session_for(
                self._ensure_onboarding_user(options["onboarding_user"])
            )
        if REVIEW in wanted_users:
            sessions[REVIEW] = self._session_for(self._ensure_review_user())
        if CC in wanted_users:
            sessions[CC] = self._session_for(self._ensure_cc_user())

        ids = self._sample_ids(options["member_user"])

        # Build the work list (resolve placeholders / drop unavailable pages).
        pages, skipped = self._resolve_pages(wanted_users, lang, ids)
        for name, reason in skipped:
            self.stdout.write(self.style.WARNING(f"  skip {name}: {reason}"))
        if not pages:
            raise CommandError("No pages to capture after filtering.")

        run_dir = (
            Path(options["output_dir"]) / timezone.now().strftime("%Y%m%d-%H%M%S")
        )
        run_dir.mkdir(parents=True, exist_ok=True)

        # results[(viewport, name)] = dict(status, final_url, redirected, error, file)
        results = {}
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:
                raise CommandError(
                    f"Could not launch Chromium ({exc}).\n"
                    "Install the browser with:\n"
                    "  .venv-1/Scripts/python.exe -m playwright install chromium"
                ) from exc

            for viewport in viewports:
                (run_dir / viewport).mkdir(exist_ok=True)
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n[{viewport}]"))
                # Group by user so each session needs only one browser context.
                for user_key in (ANON, MEMBER, CC, ONBOARDING, REVIEW):
                    group = [pg for pg in pages if pg["user_key"] == user_key]
                    if not group:
                        continue
                    ctx = browser.new_context(
                        viewport=VIEWPORTS[viewport],
                        ignore_https_errors=True,
                    )
                    ctx.add_cookies(self._cookies(base_url, sessions.get(user_key)))
                    page = ctx.new_page()
                    for pg in group:
                        results[(viewport, pg["name"])] = self._capture(
                            page, base_url, run_dir, viewport, pg
                        )
                    ctx.close()
            browser.close()

        index = run_dir / "index.html"
        index.write_text(
            self._render_gallery(pages, viewports, results), encoding="utf-8"
        )

        self._print_summary(pages, viewports, results, index)

    # ── Capture users ──────────────────────────────────────────────────────

    def _ensure_member_user(self, override):
        """Approved + consented member used for member-journey pages."""
        user = None
        if override:
            user = User.objects.filter(
                Q(username=override) | Q(email=override)
            ).first()
            if not user:
                raise CommandError(f"--member-user '{override}' not found.")
        if user is None:
            user = User.objects.filter(username="testuser1").first() or User.objects.filter(
                email="testuser1@crush.lu"
            ).first()
        if user is None:
            user, _ = User.objects.get_or_create(
                username="screenshot_member@crush.lu",
                defaults={"email": "screenshot_member@crush.lu"},
            )

        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.is_approved = True
        profile.is_active = True
        profile.verification_status = "verified"
        profile.phone_verified = True
        if not profile.phone_verified_at:
            profile.phone_verified_at = timezone.now()
        if not profile.approved_at:
            profile.approved_at = timezone.now()
        profile.save()

        self._grant_consent(user)
        self._ensure_journey(user)
        self.stdout.write(f"  member user: {user.username}")
        return user

    def _ensure_cc_user(self):
        """Staff user with an onboarded Crush Connect membership. Staff bypass
        the launch-flag / eligibility gates, so the hub/today/catalogue/profile
        views render their real UI; the membership keeps the views from hitting
        a null-membership path."""
        username = "screenshot_cc@crush.lu"
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": username, "first_name": "Screenshot", "last_name": "Connect"},
        )
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])

        now = timezone.now()
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.is_approved = True
        profile.is_active = True
        profile.verification_status = "verified"
        profile.phone_verified = True
        profile.phone_verified_at = profile.phone_verified_at or now
        profile.approved_at = profile.approved_at or now
        profile.save()

        membership, _ = CrushConnectMembership.objects.get_or_create(user=user)
        if membership.onboarded_at is None:
            membership.onboarded_at = now
            membership.relationship_goal = membership.relationship_goal or "open"
            membership.story_answer = (
                membership.story_answer
                or "Always up for a spontaneous hike and a good coffee afterwards."
            )
            membership.save()

        self._grant_consent(user)
        self.stdout.write(f"  crush-connect user: {user.username} (staff preview)")
        return user

    def _ensure_journey(self, user):
        """Seed a minimal active Wonderland journey linked to the user so the
        journey pages render real content instead of redirecting to the
        dashboard with 'No special journey found' (views_journey)."""
        experience, _ = SpecialUserExperience.objects.get_or_create(
            linked_user=user,
            defaults={
                "first_name": user.first_name or "Screenshot",
                "last_name": user.last_name or "Member",
                "is_active": True,
            },
        )
        journey, _ = JourneyConfiguration.objects.get_or_create(
            special_experience=experience,
            journey_type="wonderland",
            defaults={"is_active": True, "journey_name": "The Wonderland of You"},
        )
        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=1,
            defaults={
                "title": "Down the Rabbit Hole",
                "theme": "Mystery & Curiosity",
                "story_introduction": "Every story has a beginning. Yours starts here.",
                "completion_message": "Beautifully done — the next chapter awaits.",
            },
        )
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                "challenge_type": "riddle",
                "question": "What grows the more you share it?",
                "correct_answer": "love",
                "success_message": "Exactly. Onward!",
            },
        )

    def _ensure_onboarding_user(self, username):
        """Pre-submission user: finished steps 1-3 but hasn't submitted, so
        /create-profile/ renders the real builder form (it shows 'under review'
        once a submission exists). create_profile redirects unless the user's
        onboarding step is >= 4 (views.py: get_current_step)."""
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": username, "first_name": "Screenshot", "last_name": "Onboarding"},
        )
        now = timezone.now()
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.welcome_seen_at = profile.welcome_seen_at or now
        profile.phone_verified = True
        profile.phone_verified_at = profile.phone_verified_at or now
        profile.coach_intro_seen_at = profile.coach_intro_seen_at or now
        # 'incomplete' => get_current_step == 4 (build profile), which is what
        # makes /create-profile/ render the builder rather than the verify state.
        profile.verification_status = "incomplete"
        profile.is_active = True
        profile.is_approved = False
        profile.save()
        # Must have NO submission, or create-profile shows the review state.
        ProfileSubmission.objects.filter(profile=profile).delete()

        self._grant_consent(user)
        self.stdout.write(f"  onboarding user: {user.username}")
        return user

    def _ensure_review_user(self):
        """Post-submission user under coach review: renders meet-coach,
        screening-call and profile-submitted (these gate on a submission whose
        coach is assigned, see views_profile.meet_coach_step)."""
        username = "screenshot_review@crush.lu"
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": username, "first_name": "Screenshot", "last_name": "Review"},
        )
        now = timezone.now()
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.welcome_seen_at = profile.welcome_seen_at or now
        profile.phone_verified = True
        profile.phone_verified_at = profile.phone_verified_at or now
        profile.coach_intro_seen_at = profile.coach_intro_seen_at or now
        profile.verification_status = "pending"
        profile.is_active = True
        profile.is_approved = False
        profile.save()

        coach = CrushCoach.objects.filter(is_active=True).first()
        submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .order_by("-submitted_at")
            .first()
        )
        if submission is None:
            ProfileSubmission.objects.create(
                profile=profile, status="pending", coach=coach
            )
        elif coach and submission.coach_id is None:
            submission.coach = coach
            submission.save(update_fields=["coach"])
        if coach is None:
            self.stdout.write(
                self.style.WARNING(
                    "  no active coach found - /onboarding/meet-coach/ may redirect."
                )
            )

        self._grant_consent(user)
        self.stdout.write(f"  review user: {user.username}")
        return user

    def _grant_consent(self, user):
        """Set the real consent gate (CrushConsentMiddleware checks the DB record)."""
        consent, _ = UserDataConsent.objects.get_or_create(user=user)
        if not consent.crushlu_consent_given:
            consent.crushlu_consent_given = True
            consent.crushlu_consent_date = timezone.now()
            consent.save()

    def _session_for(self, user):
        """Mint a DB-backed session cookie for the given user."""
        store = import_module(settings.SESSION_ENGINE).SessionStore
        session = store()
        session[SESSION_KEY] = user.pk
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        return session.session_key

    def _cookies(self, base_url, session_key):
        cookies = [
            # Django consent middleware honours the DB record; this mirrors it.
            {"name": "crushlu_consent_given", "value": "true", "url": base_url},
            # Front-end cookie banner (core/templates/includes/cookie_banner.html)
            # shows whenever the `cookie_consent` cookie is absent. Any non-empty
            # value suppresses the overlay so it doesn't cover the screenshots.
            {"name": "cookie_consent", "value": "accepted", "url": base_url},
        ]
        if session_key:
            cookies.append(
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_key,
                    "url": base_url,
                    "httpOnly": True,
                }
            )
        return cookies

    # ── Sample data + page resolution ──────────────────────────────────────

    def _sample_ids(self, member_override):
        """Resolve ids for parameterized pages from existing seed data."""
        event = (
            MeetupEvent.objects.filter(is_published=True).order_by("id").first()
            or MeetupEvent.objects.order_by("id").first()
        )
        member = (
            User.objects.filter(Q(username=member_override) | Q(email=member_override)).first()
            if member_override
            else User.objects.filter(username="testuser1").first()
        )
        conn_qs = EventConnection.objects.all()
        if member:
            conn_qs = conn_qs.filter(Q(requester=member) | Q(recipient=member))
        connection = conn_qs.order_by("id").first()
        return {
            "event_id": event.id if event else None,
            "connection_id": connection.id if connection else None,
        }

    def _resolve_pages(self, wanted_users, lang, ids):
        """Expand the page list: prefix lang, fill ids, drop unresolvable pages."""
        pages, skipped = [], []
        for section, name, template, user_key in PAGES:
            if user_key not in wanted_users:
                continue
            needs_event = "{event_id}" in template
            needs_conn = "{connection_id}" in template
            if needs_event and not ids["event_id"]:
                skipped.append((name, "no MeetupEvent in DB (run setup_local_dev)"))
                continue
            if needs_conn and not ids["connection_id"]:
                skipped.append((name, "no EventConnection for member user"))
                continue
            path = template.format(**ids)
            pages.append(
                {
                    "section": section,
                    "name": name,
                    "path": f"/{lang}{path}",
                    "user_key": user_key,
                }
            )
        return pages, skipped

    # ── Per-page capture ───────────────────────────────────────────────────

    def _prepare_page(self, page):
        """Make a full-page capture faithful to what a user sees: reveal
        scroll-animated sections, hide the cookie overlay if the cookie missed
        it, and scroll through to trigger lazy-loaded images. page.evaluate runs
        via CDP so it is not blocked by the site's nonce-based CSP."""
        try:
            page.wait_for_timeout(400)  # initial paint
            page.evaluate(
                """() => {
                    // IntersectionObserver only adds `.visible` on scroll; in a
                    // static capture it never fires, leaving sections invisible.
                    document.querySelectorAll('.animate-on-scroll')
                        .forEach(el => el.classList.add('visible'));
                    const banner = document.getElementById('cookie-consent-banner');
                    if (banner) banner.style.display = 'none';
                    const modal = document.getElementById('cookie-settings-modal');
                    if (modal) modal.style.display = 'none';
                    // Hide the fixed mobile bottom nav (and any bottom-anchored
                    // fixed bar): full_page stitching paints it mid-page once the
                    // document is taller than the viewport. It's app chrome, not
                    // page content, so drop it for a faithful capture.
                    document.querySelectorAll('.bottom-nav').forEach(el => el.style.display = 'none');
                    document.querySelectorAll('body *').forEach(el => {
                        const s = getComputedStyle(el);
                        // Bottom-anchored fixed chrome (nav, push prompt, the
                        // floating WhatsApp button) gets stitched mid-page in a
                        // full_page capture. Top-pinned headers (top != auto)
                        // are kept.
                        if (s.position === 'fixed' && s.top === 'auto' && s.bottom !== 'auto') {
                            el.style.display = 'none';
                        }
                    });
                }"""
            )
            page.evaluate(
                """async () => {
                    const step = Math.max(window.innerHeight, 400);
                    for (let y = 0; y < document.body.scrollHeight; y += step) {
                        window.scrollTo(0, y);
                        await new Promise(r => setTimeout(r, 50));
                    }
                    window.scrollTo(0, 0);
                }"""
            )
            page.wait_for_timeout(500)  # let fonts/lazy images settle
        except Exception:
            pass  # best-effort; never abort a capture over preparation

    def _capture(self, page, base_url, run_dir, viewport, pg):
        url = base_url + pg["path"]
        result = {
            "status": None,
            "final_url": url,
            "redirected": False,
            "error": None,
            "file": None,
        }
        try:
            resp = page.goto(url, wait_until="load", timeout=25000)
            result["status"] = resp.status if resp else None
        except Exception as exc:
            result["error"] = str(exc).splitlines()[0]

        self._prepare_page(page)

        final_path = urlparse(page.url).path
        result["final_url"] = page.url
        requested = pg["path"].rstrip("/")
        if final_path.rstrip("/") != requested:
            # A bounce to a known gate (login/consent) is a real problem; a
            # within-page redirect (e.g. trailing-slash) is benign.
            if any(gate in final_path for gate in REDIRECT_GATES):
                result["redirected"] = True

        rel = f"{viewport}/{pg['name']}.png"
        try:
            page.screenshot(path=str(run_dir / rel), full_page=True)
            result["file"] = rel
        except Exception as exc:
            if not result["error"]:
                result["error"] = str(exc).splitlines()[0]

        flag = (
            "REDIRECT"
            if result["redirected"]
            else ("ERROR" if result["error"] else "ok")
        )
        self.stdout.write(
            f"  {flag:8s} {str(result['status'] or '-'):4s} {pg['path']}"
        )
        return result

    # ── Gallery + summary ──────────────────────────────────────────────────

    def _render_gallery(self, pages, viewports, results):
        generated = timezone.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<title>Crush.lu screenshot review</title>",
            "<style>",
            "body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#0f0f12;color:#e7e7ea}",
            "header{padding:20px 28px;border-bottom:1px solid #26262d;position:sticky;top:0;background:#0f0f12;z-index:2}",
            "h1{margin:0 0 4px;font-size:18px}.sub{color:#9a9aa5}",
            "h2{margin:34px 28px 8px;font-size:15px;color:#c9a6ff;border-bottom:1px solid #26262d;padding-bottom:6px}",
            ".card{margin:14px 28px;background:#17171c;border:1px solid #26262d;border-radius:10px;padding:14px}",
            ".card h3{margin:0 0 2px;font-size:14px}",
            ".meta{color:#9a9aa5;font-size:12px;margin-bottom:10px;word-break:break-all}",
            ".shots{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}",
            ".shot{flex:0 0 auto}.shot .vp{font-size:11px;color:#9a9aa5;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em}",
            ".frame{overflow:auto;max-height:560px;border:1px solid #2c2c34;border-radius:6px;background:#fff}",
            ".frame.mobile{width:300px}.frame.desktop{width:600px}",
            ".frame img{display:block;width:100%}",
            ".badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;margin-left:8px;vertical-align:middle}",
            ".badge.redirect{background:#5a1d1d;color:#ffb4b4}.badge.error{background:#5a1d1d;color:#ffb4b4}",
            ".badge.ok{background:#1d4a2a;color:#9ff0b6}",
            ".missing{display:flex;align-items:center;justify-content:center;color:#9a9aa5;height:120px}",
            "</style></head><body>",
            "<header><h1>Crush.lu screenshot review</h1>",
            f"<div class='sub'>Generated {html.escape(generated)} &middot; "
            f"{len(pages)} pages &middot; {', '.join(viewports)}</div></header>",
        ]

        current_section = None
        for pg in pages:
            if pg["section"] != current_section:
                current_section = pg["section"]
                parts.append(f"<h2>{html.escape(current_section)}</h2>")

            # Worst status across viewports drives the card badge.
            redirected = any(
                results.get((vp, pg["name"]), {}).get("redirected") for vp in viewports
            )
            errored = any(
                results.get((vp, pg["name"]), {}).get("error") for vp in viewports
            )
            if redirected:
                badge = "<span class='badge redirect'>redirected</span>"
            elif errored:
                badge = "<span class='badge error'>error</span>"
            else:
                badge = "<span class='badge ok'>ok</span>"

            sample = next(
                (results.get((vp, pg["name"])) for vp in viewports if results.get((vp, pg["name"]))),
                {},
            )
            final = sample.get("final_url", "")
            parts.append("<div class='card'>")
            parts.append(f"<h3>{html.escape(pg['name'])}{badge}</h3>")
            parts.append(
                f"<div class='meta'>{html.escape(pg['path'])}"
                + (f" &rarr; {html.escape(final)}" if redirected and final else "")
                + "</div>"
            )
            parts.append("<div class='shots'>")
            for vp in viewports:
                res = results.get((vp, pg["name"]), {})
                parts.append("<div class='shot'>")
                parts.append(f"<div class='vp'>{html.escape(vp)}</div>")
                if res.get("file"):
                    parts.append(
                        f"<div class='frame {vp}'><img loading='lazy' "
                        f"src='{html.escape(res['file'])}' alt='{html.escape(pg['name'])}'></div>"
                    )
                else:
                    note = html.escape(res.get("error") or "no screenshot")
                    parts.append(f"<div class='frame {vp}'><div class='missing'>{note}</div></div>")
                parts.append("</div>")
            parts.append("</div></div>")

        parts.append("</body></html>")
        return "".join(parts)

    def _print_summary(self, pages, viewports, results, index):
        self.stdout.write(self.style.MIGRATE_HEADING("\nSummary"))
        problems = 0
        for pg in pages:
            for vp in viewports:
                res = results.get((vp, pg["name"]), {})
                if res.get("redirected") or res.get("error"):
                    problems += 1
                    why = "redirected" if res.get("redirected") else res.get("error")
                    self.stdout.write(
                        self.style.WARNING(f"  [{vp}] {pg['path']} -> {why}")
                    )
        captured = sum(1 for r in results.values() if r.get("file"))
        self.stdout.write(
            f"\nCaptured {captured} screenshots across {len(pages)} pages "
            f"({len(viewports)} viewport(s)); {problems} flagged."
        )
        self.stdout.write(self.style.SUCCESS(f"Gallery: {index}"))
        self.stdout.write("Open it in a browser to review the page looks.")
