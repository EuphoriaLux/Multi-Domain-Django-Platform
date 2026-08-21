"""
Tests for the social-login latency work.

Three groups, matching the three places the callback spent its time:

  1. LuxIDOAuth2Adapter — the /userinfo round trip is skipped when the
     id_token already carries the claims, and the discovery document is
     cached across adapter instances instead of refetched per request.
  2. update_crush_profile_from_luxid — a relogin whose claims match the
     stored profile takes neither the row lock nor the write.
  3. The Google/Facebook/Microsoft handlers — a login that changes nothing
     no longer rewrites the profile, and the social photo is not
     re-downloaded on every single sign-in.

Note on locking: SQLite reports has_select_for_update = False, so the row
lock is a silent no-op both locally and in CI. "The lock was skipped" can
therefore never be observed through locking behaviour — these tests assert it
structurally, by watching QuerySet.select_for_update itself.
"""

from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)
from allauth.socialaccount.signals import pre_social_login

from crush_lu import signals as crush_signals
from crush_lu.models import CrushProfile
from crush_lu.providers.luxid.views import LuxIDOAuth2Adapter

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(host="crush.lu"):
    factory = RequestFactory()
    request = factory.get("/", HTTP_HOST=host)
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    return request


def _complete_id_token(**overrides):
    """An id_token carrying the full Annex C6 claim set."""
    claims = {
        "sub": "luxid-subject-1",
        "email": "member@example.com",
        "given_name": "Ada",
        "family_name": "Lovelace",
        "birthdate": "1990-01-01",
        "gender": "female",
        "phone_number": "+352691234567",
        "locale": "fr-FR",
    }
    claims.update(overrides)
    return claims


class _FakeApp:
    """Stand-in for SocialApp: only .settings and .client_id are read here."""

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.client_id = "crush-lu-client"


def _make_adapter(request=None):
    adapter = LuxIDOAuth2Adapter(request or _make_request())
    # The real one resolves a SocialApp from the DB; the adapter only ever
    # asks it for server_url here.
    provider = MagicMock()
    provider.server_url = "https://login.luxid.lu/.well-known/openid-configuration"
    adapter.get_provider = MagicMock(return_value=provider)
    return adapter


# ---------------------------------------------------------------------------
# 1. Adapter: conditional /userinfo
# ---------------------------------------------------------------------------


class LuxIDUserinfoSkipTests(TestCase):
    """complete_login() calls /userinfo only when the id_token is not enough."""

    def setUp(self):
        cache.clear()

    def _complete_login(self, id_token_claims, app_settings=None):
        adapter = _make_adapter()
        app = _FakeApp(app_settings)
        token = MagicMock()
        token.token = "access-token"

        with patch.object(
            LuxIDOAuth2Adapter, "_decode_id_token", return_value=id_token_claims
        ), patch.object(
            LuxIDOAuth2Adapter,
            "_fetch_user_info",
            return_value={"sub": "from-userinfo"},
        ) as fetch_userinfo:
            sociallogin_from_response = MagicMock()
            adapter.get_provider.return_value.sociallogin_from_response = (
                sociallogin_from_response
            )
            adapter.complete_login(
                _make_request(), app, token, response={"id_token": "jwt.goes.here"}
            )

        # The envelope allauth stores is the single positional arg's 2nd item.
        envelope = sociallogin_from_response.call_args[0][1]
        return envelope, fetch_userinfo

    def test_complete_id_token_skips_userinfo(self):
        envelope, fetch_userinfo = self._complete_login(_complete_id_token())

        fetch_userinfo.assert_not_called()
        # Envelope shape must be preserved: id_token present, userinfo absent
        # entirely rather than present-and-empty, because _luxid_claims() and
        # allauth's _pick_data() both branch on the key's presence.
        self.assertIn("id_token", envelope)
        self.assertNotIn("userinfo", envelope)

    def test_missing_core_claim_still_fetches_userinfo(self):
        # email is a core claim; without it a login cannot be completed.
        claims = _complete_id_token()
        del claims["email"]

        _envelope, fetch_userinfo = self._complete_login(claims)
        fetch_userinfo.assert_called_once()

    def test_no_optional_claims_still_fetches_userinfo(self):
        """The conservative half of the predicate.

        A member may withhold every optional claim, so their absence cannot
        prove the id_token is complete — it might equally mean this IdP
        reserves profile claims for /userinfo. This case must fetch.
        """
        claims = {
            "sub": "s",
            "email": "m@example.com",
            "given_name": "Ada",
            "family_name": "Lovelace",
        }
        _envelope, fetch_userinfo = self._complete_login(claims)
        fetch_userinfo.assert_called_once()

    def test_single_optional_claim_is_enough_to_skip(self):
        """Only one optional claim is needed as evidence.

        A member who shared nothing but their locale still proves the IdP
        puts profile claims in the id_token.
        """
        claims = {
            "sub": "s",
            "email": "m@example.com",
            "given_name": "Ada",
            "family_name": "Lovelace",
            "locale": "de-DE",
        }
        _envelope, fetch_userinfo = self._complete_login(claims)
        fetch_userinfo.assert_not_called()

    def test_app_setting_can_force_fetch(self):
        _envelope, fetch_userinfo = self._complete_login(
            _complete_id_token(), app_settings={"fetch_userinfo": True}
        )
        fetch_userinfo.assert_called_once()

    def test_app_setting_can_force_skip(self):
        """False wins even when the id_token looks incomplete."""
        claims = {"sub": "s"}
        _envelope, fetch_userinfo = self._complete_login(
            claims, app_settings={"fetch_userinfo": False}
        )
        fetch_userinfo.assert_not_called()

    def test_no_id_token_fetches_even_when_setting_says_not_to(self):
        """fetch_userinfo=False must not be able to produce an empty envelope.

        With no id_token, /userinfo is the only source of claims. Honouring
        the setting here would hand sociallogin_from_response() a {}, whose
        extract_uid() raises KeyError("sub") and 500s the callback. Upstream
        encodes the same precedence.
        """
        adapter = _make_adapter()
        token = MagicMock()
        token.token = "access-token"

        with patch.object(
            LuxIDOAuth2Adapter,
            "_fetch_user_info",
            return_value={"sub": "from-userinfo"},
        ) as fetch_userinfo:
            sociallogin_from_response = MagicMock()
            adapter.get_provider.return_value.sociallogin_from_response = (
                sociallogin_from_response
            )
            adapter.complete_login(
                _make_request(),
                _FakeApp({"fetch_userinfo": False}),
                token,
                response={},
            )

        fetch_userinfo.assert_called_once()
        envelope = sociallogin_from_response.call_args[0][1]
        self.assertIn("userinfo", envelope)
        self.assertNotEqual(envelope, {})

    def test_no_id_token_falls_back_to_userinfo(self):
        adapter = _make_adapter()
        token = MagicMock()
        token.token = "access-token"

        with patch.object(
            LuxIDOAuth2Adapter,
            "_fetch_user_info",
            return_value={"sub": "from-userinfo"},
        ) as fetch_userinfo:
            sociallogin_from_response = MagicMock()
            adapter.get_provider.return_value.sociallogin_from_response = (
                sociallogin_from_response
            )
            adapter.complete_login(
                _make_request(), _FakeApp(), token, response={}
            )

        fetch_userinfo.assert_called_once()
        envelope = sociallogin_from_response.call_args[0][1]
        self.assertIn("userinfo", envelope)
        self.assertNotIn("id_token", envelope)


# ---------------------------------------------------------------------------
# 1b. Adapter: discovery document caching
# ---------------------------------------------------------------------------


class LuxIDDiscoveryCacheTests(TestCase):
    """The .well-known document survives across adapter instances."""

    DISCOVERY = {
        "issuer": "https://login.luxid.lu",
        "token_endpoint": "https://login.luxid.lu/oxauth/restv1/token",
        "authorization_endpoint": "https://login.luxid.lu/oxauth/restv1/authorize",
        "userinfo_endpoint": "https://login.luxid.lu/oxauth/restv1/userinfo",
        "jwks_uri": "https://login.luxid.lu/oxauth/restv1/jwks",
    }

    def setUp(self):
        cache.clear()

    def test_second_adapter_does_not_refetch(self):
        """A fresh adapter is built per request; the fetch must not be."""
        with patch.object(
            OpenIDConnectOAuth2Adapter,
            "openid_config",
            new_callable=lambda: property(lambda self: dict(LuxIDDiscoveryCacheTests.DISCOVERY)),
        ):
            first = _make_adapter()
            self.assertEqual(first.openid_config["issuer"], "https://login.luxid.lu")

        # With the upstream property no longer patched, a cache miss would
        # raise (there is no HTTP available here). Reaching the same value
        # proves it was served from cache.
        second = _make_adapter()
        self.assertEqual(
            second.openid_config["token_endpoint"],
            self.DISCOVERY["token_endpoint"],
        )

    def test_staging_and_production_do_not_share_an_entry(self):
        """The key is the server_url, so the two hosts stay isolated."""
        prod = _make_adapter()
        uat = _make_adapter()
        uat.get_provider.return_value.server_url = (
            "https://login-uat.luxid.lu/.well-known/openid-configuration"
        )
        self.assertNotEqual(prod._discovery_cache_key, uat._discovery_cache_key)

    def test_malformed_cache_entry_is_ignored(self):
        """A truncated entry must refetch, not surface as a KeyError mid-login."""
        adapter = _make_adapter()
        cache.set(adapter._discovery_cache_key, {"not": "a discovery doc"}, 60)

        with patch.object(
            OpenIDConnectOAuth2Adapter,
            "openid_config",
            new_callable=lambda: property(lambda self: dict(LuxIDDiscoveryCacheTests.DISCOVERY)),
        ):
            self.assertEqual(
                adapter.openid_config["token_endpoint"],
                self.DISCOVERY["token_endpoint"],
            )


# ---------------------------------------------------------------------------
# 2. update_crush_profile_from_luxid: no-op relogin
# ---------------------------------------------------------------------------


class _LockWatcher:
    """Records whether select_for_update() was called, and still delegates."""

    def __init__(self):
        self.called = False
        self._real = QuerySet.select_for_update

    def __enter__(self):
        watcher = self

        def _tracking(self, *args, **kwargs):
            watcher.called = True
            return watcher._real(self, *args, **kwargs)

        self._patcher = patch.object(QuerySet, "select_for_update", _tracking)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


class LuxIDReloginWriteTests(TestCase):
    """A relogin writes only what actually moved."""

    def setUp(self):
        cache.clear()
        crush_signals._thread_local.is_crush_luxid_login = False
        self.user = User.objects.create_user(
            username="relogin@example.com",
            email="relogin@example.com",
            password="pass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1990, 1, 1),
            gender="F",
            location="Luxembourg City",
            phone_number="+352691234567",
            phone_verified=True,
            preferred_language="fr",
            language_explicitly_set=True,
        )

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def _login(self, claims, provider="luxid"):
        account = MagicMock()
        account.provider = provider
        account.extra_data = {"id_token": claims}
        sl = MagicMock()
        sl.user = self.user
        sl.account = account
        sl.token = MagicMock()
        sl.token.token = "fake-token"
        request = _make_request()
        pre_social_login.send(
            sender=SocialAccount, request=request, sociallogin=sl
        )
        return request

    def test_identical_claims_skip_lock_and_write(self):
        """The hot path: nothing changed, so nothing is locked or written."""
        claims = {
            "sub": "s",
            "email": "relogin@example.com",
            "birthdate": "1990-01-01",  # same as stored
            "gender": "female",  # maps to F, same as stored
            "phone_number": "+352691234567",  # same, already verified
            "locale": "fr-FR",  # language already explicitly set
        }

        with patch.object(
            crush_signals, "_luxid_save_profile"
        ) as save, _LockWatcher() as lock:
            self._login(claims)

        save.assert_not_called()
        self.assertFalse(
            lock.called,
            "select_for_update() was taken for a relogin that changed nothing",
        )

    def test_changed_birthdate_is_written(self):
        claims = {
            "sub": "s",
            "email": "relogin@example.com",
            "birthdate": "1991-02-03",
        }
        self._login(claims)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.date_of_birth, date(1991, 2, 3))

    def test_changed_gender_is_written(self):
        claims = {"sub": "s", "email": "relogin@example.com", "gender": "male"}
        self._login(claims)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.gender, "M")

    def test_identical_birthdate_is_not_in_update_fields(self):
        """Presence of a claim is not a change; only a moved value is."""
        claims = {
            "sub": "s",
            "email": "relogin@example.com",
            "birthdate": "1990-01-01",
            "gender": "male",  # this one DID change
        }
        with patch.object(crush_signals, "_luxid_save_profile") as save:
            self._login(claims)

        save.assert_called_once()
        updated_fields = save.call_args[0][1]
        self.assertIn("gender", updated_fields)
        self.assertNotIn("date_of_birth", updated_fields)

    def test_locale_still_locks_when_it_could_apply(self):
        """The race the lock was written for is unchanged."""
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            preferred_language="en", language_explicitly_set=False
        )
        claims = {"sub": "s", "email": "relogin@example.com", "locale": "de-DE"}

        with _LockWatcher() as lock:
            self._login(claims)

        self.assertTrue(
            lock.called,
            "the locale gate could apply, so the row lock must still be taken",
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_language, "de")

    def test_explicit_language_is_not_overwritten(self):
        """Unchanged guard: an answered language survives a LuxID locale."""
        claims = {"sub": "s", "email": "relogin@example.com", "locale": "de-DE"}
        self._login(claims)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_language, "fr")


# ---------------------------------------------------------------------------
# 3. Google / Facebook / Microsoft: no pointless saves, throttled photos
# ---------------------------------------------------------------------------


class SocialProviderSaveTests(TestCase):
    """A login that changes nothing must not rewrite the profile."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="social@example.com",
            email="social@example.com",
            password="pass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1990, 1, 1),
            gender="F",
            location="Luxembourg City",
        )

    def _login(self, provider, extra_data=None):
        account = MagicMock()
        account.provider = provider
        account.extra_data = extra_data or {}
        account.id = 4242
        sl = MagicMock()
        sl.user = self.user
        sl.account = account
        sl.token = MagicMock()
        sl.token.token = "fake-token"
        request = _make_request()
        with patch(
            "crush_lu.social_photos.refresh_social_photo_cache", return_value=False
        ):
            pre_social_login.send(
                sender=SocialAccount, request=request, sociallogin=sl
            )

    def test_microsoft_login_does_not_rewrite_profile(self):
        """Microsoft feeds no profile column, so it must not save at all."""
        with patch.object(CrushProfile, "save") as save:
            self._login("microsoft", {"displayName": "Ada"})
        save.assert_not_called()

    def test_google_login_without_photo_change_does_not_rewrite_profile(self):
        self.profile.photo_1 = "users/1/photo.jpg"
        self.profile.save()

        with patch.object(CrushProfile, "save") as save:
            self._login("google", {"picture": "https://lh3.example/x=s96-c"})
        save.assert_not_called()

    def test_facebook_login_without_changes_does_not_rewrite_profile(self):
        self.profile.photo_1 = "users/1/photo.jpg"
        self.profile.save()

        with patch.object(CrushProfile, "save") as save:
            # birthday/gender already set on the profile, so
            # update_profile_from_facebook_data() reports no change.
            self._login("facebook", {"id": "fb-1", "birthday": "01/01/1990"})
        save.assert_not_called()


class SocialPhotoRefreshThrottleTests(TestCase):
    """The photo is not re-downloaded on every single login."""

    def setUp(self):
        cache.clear()

    def _account(self, provider="google", account_id=99):
        account = MagicMock()
        account.provider = provider
        account.id = account_id
        account.user_id = 1
        account.extra_data = {"picture": "https://lh3.example/photo=s96-c"}
        return account

    def test_second_login_within_interval_skips_the_download(self):
        from crush_lu.social_photos import refresh_social_photo_cache

        account = self._account()
        with patch("crush_lu.social_photos.requests.get") as get, patch(
            "crush_lu.social_photos._persist_social_photo", return_value=True
        ):
            get.return_value.headers = {"Content-Type": "image/jpeg"}
            get.return_value.content = b"bytes"

            self.assertTrue(refresh_social_photo_cache(account))
            self.assertEqual(get.call_count, 1)

            # Same account, same day: no second network call.
            self.assertFalse(refresh_social_photo_cache(account))
            self.assertEqual(get.call_count, 1)

    def test_force_bypasses_the_throttle(self):
        from crush_lu.social_photos import refresh_social_photo_cache

        account = self._account(account_id=100)
        with patch("crush_lu.social_photos.requests.get") as get, patch(
            "crush_lu.social_photos._persist_social_photo", return_value=True
        ):
            get.return_value.headers = {"Content-Type": "image/jpeg"}
            get.return_value.content = b"bytes"

            refresh_social_photo_cache(account)
            refresh_social_photo_cache(account, force=True)

        self.assertEqual(get.call_count, 2)

    def test_expired_token_does_not_burn_the_throttle(self):
        """The cheap early return must not lock out a later real refresh.

        A member who reconnects an expired account should get their photo
        refreshed on that login, not one interval later.
        """
        from crush_lu.social_photos import refresh_social_photo_cache

        account = self._account(provider="microsoft", account_id=101)

        with patch(
            "crush_lu.social_photos._get_token_for_account", return_value=None
        ):
            self.assertFalse(refresh_social_photo_cache(account))

        # Token is back; the refresh must actually run.
        token = MagicMock()
        token.expires_at = None
        with patch(
            "crush_lu.social_photos._get_token_for_account", return_value=token
        ), patch("crush_lu.social_photos.requests.get") as get, patch(
            "crush_lu.social_photos._persist_social_photo", return_value=True
        ):
            get.return_value.status_code = 200
            get.return_value.content = b"bytes"
            self.assertTrue(refresh_social_photo_cache(account))
            self.assertEqual(get.call_count, 1)


# ---------------------------------------------------------------------------
# 4. Preconnect resource hints
# ---------------------------------------------------------------------------


class SocialLoginPreconnectTests(TestCase):
    """The head hints cover exactly the IdPs this site actually offers."""

    def setUp(self):
        cache.clear()
        from django.contrib.sites.models import Site

        self.site = Site.objects.get_current()

    def _render(self):
        from django.template import Context, Template

        template = Template("{% load social_tags %}{% social_login_preconnect %}")
        return template.render(Context({"request": _make_request()}))

    def _add_app(self, provider, provider_id="", settings=None):
        from allauth.socialaccount.models import SocialApp

        app = SocialApp.objects.create(
            provider=provider,
            provider_id=provider_id,
            name=f"{provider} app",
            client_id="cid",
            secret="secret",
            settings=settings or {},
        )
        app.sites.add(self.site)
        return app

    def test_no_apps_emits_nothing(self):
        """No providers offered means no connections opened."""
        self.assertEqual(self._render().strip(), "")

    def test_non_luxid_providers_get_dns_prefetch_only(self):
        """A member uses one provider, so only one socket is worth opening.

        Preconnecting every configured IdP would leave four unused TLS
        connections competing for bandwidth during page load.
        """
        self._add_app("google")
        self._add_app("apple")
        html = self._render()

        self.assertIn(
            '<link rel="dns-prefetch" href="https://accounts.google.com">', html
        )
        self.assertIn(
            '<link rel="dns-prefetch" href="https://appleid.apple.com">', html
        )
        self.assertNotIn("preconnect", html)

        # Not configured, so not hinted at all.
        self.assertNotIn("facebook.com", html)
        self.assertNotIn("login.microsoftonline.com", html)

    def test_luxid_gets_the_preconnect(self):
        """LuxID is the promoted path and had the slowest callback."""
        self._add_app(
            "luxid", settings={"server_url": "https://login-uat.luxid.lu"}
        )
        self._add_app("google")
        html = self._render()

        self.assertIn(
            '<link rel="preconnect" href="https://login-uat.luxid.lu" crossorigin>',
            html,
        )
        # Exactly one preconnect, and it is LuxID's.
        self.assertEqual(html.count('rel="preconnect"'), 1)
        self.assertIn(
            '<link rel="dns-prefetch" href="https://accounts.google.com">', html
        )

    def test_luxid_origin_is_read_from_the_socialapp(self):
        """Staging and production must hint different hosts."""
        self._add_app(
            "luxid", settings={"server_url": "https://login-uat.luxid.lu"}
        )
        html = self._render()

        self.assertIn("https://login-uat.luxid.lu", html)
        self.assertNotIn('"https://login.luxid.lu"', html)

    def test_luxid_via_generic_openid_connect_app(self):
        """LuxID is also configured as a generic OIDC app in some sites."""
        self._add_app(
            "openid_connect",
            provider_id="luxid",
            settings={"server_url": "https://login.luxid.lu"},
        )
        html = self._render()
        self.assertIn(
            '<link rel="preconnect" href="https://login.luxid.lu" crossorigin>', html
        )

    def test_dns_prefetch_also_covers_luxid(self):
        """The cheap hint is a harmless belt-and-braces for older browsers."""
        self._add_app("luxid", settings={"server_url": "https://login.luxid.lu"})
        html = self._render()
        self.assertIn(
            '<link rel="dns-prefetch" href="https://login.luxid.lu">', html
        )

    def test_broken_app_config_does_not_break_the_page(self):
        """A login page must render even if the hints cannot be computed."""
        self._add_app("luxid", settings={"server_url": "::::not a url::::"})
        html = self._render()
        # No crash, and nothing bogus emitted for the unparseable entry.
        self.assertNotIn("::::", html)


class LoginPageRendersHintsTests(TestCase):
    """End-to-end: the hints reach the real login page's <head>.

    The template-tag tests above prove the tag; this proves the wiring — that
    it is loaded, called, and lands inside <head> rather than somewhere the
    browser ignores.
    """

    def setUp(self):
        cache.clear()
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        app = SocialApp.objects.create(
            provider="luxid",
            name="LuxID",
            client_id="cid",
            secret="secret",
            settings={"server_url": "https://login.luxid.lu"},
        )
        app.sites.add(site)

    def test_login_page_head_carries_the_luxid_preconnect(self):
        # Literal path, not reverse(): the domain middleware swaps urlconf per
        # host, so a reversed crush_lu URL 404s under HTTP_HOST=crush.lu.
        response = self.client.get("/accounts/login/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)

        html = response.content.decode()
        self.assertIn(
            '<link rel="preconnect" href="https://login.luxid.lu" crossorigin>',
            html,
        )

        # It must be in <head>, not dumped into the body where it does nothing.
        head = html.split("</head>", 1)[0]
        self.assertIn("https://login.luxid.lu", head)

    def test_login_page_has_no_leaked_comment_text(self):
        """Regression guard for the multi-line {# #} trap.

        A multi-line {# ... #} is not a comment to Django's parser — it renders
        as visible text. test_template_hygiene catches the syntax repo-wide;
        this catches the symptom on the page this work touched.
        """
        response = self.client.get("/accounts/login/", HTTP_HOST="crush.lu")
        html = response.content.decode()
        self.assertNotIn("Warm the TCP+TLS handshake", html)


class LuxIDDownstreamFanoutTests(TestCase):
    """What the skipped write buys: no Graph call on the request thread.

    transaction.on_commit() does NOT move work off the request — outside a
    transaction it runs the callback inline, and production sets no task
    queue. So the only way sync_profile_to_outlook stops parking the response
    is for the post_save that reaches it never to fire.
    """

    def setUp(self):
        cache.clear()
        crush_signals._thread_local.is_crush_luxid_login = False
        self.user = User.objects.create_user(
            username="fanout@example.com",
            email="fanout@example.com",
            password="pass123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1990, 1, 1),
            gender="F",
            location="Luxembourg City",
            phone_number="+352691234567",
            phone_verified=True,
            preferred_language="fr",
            language_explicitly_set=True,
        )

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def _login(self, claims):
        account = MagicMock()
        account.provider = "luxid"
        account.extra_data = {"id_token": claims}
        sl = MagicMock()
        sl.user = self.user
        sl.account = account
        sl.token = MagicMock()
        sl.token.token = "fake-token"
        pre_social_login.send(
            sender=SocialAccount, request=_make_request(), sociallogin=sl
        )

    def test_unchanged_relogin_triggers_no_outlook_sync(self):
        claims = {
            "sub": "s",
            "email": "fanout@example.com",
            "birthdate": "1990-01-01",
            "gender": "female",
            "phone_number": "+352691234567",
            "locale": "fr-FR",
        }
        with patch(
            "crush_lu.services.graph_contacts.GraphContactsService"
        ) as graph:
            self._login(claims)
        graph.assert_not_called()

    def test_unchanged_relogin_triggers_no_wallet_push(self):
        claims = {
            "sub": "s",
            "email": "fanout@example.com",
            "birthdate": "1990-01-01",
            "gender": "female",
        }
        with patch.object(crush_signals, "trigger_wallet_pass_updates") as wallet:
            self._login(claims)
        wallet.assert_not_called()

    def test_changed_claim_still_reaches_the_profile(self):
        """The fan-out is skipped because nothing changed, not because the
        handler stopped working."""
        with patch(
            "crush_lu.services.graph_contacts.GraphContactsService"
        ):
            self._login(
                {
                    "sub": "s",
                    "email": "fanout@example.com",
                    "birthdate": "1985-06-30",
                }
            )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.date_of_birth, date(1985, 6, 30))


class LuxIDNewUserSignupTests(TestCase):
    """A brand-new LuxID signup still lands every Annex C6 claim.

    This is the create_crush_profile_from_luxid (post_save on SocialAccount)
    path, not the relogin path. It is not modified by this work, but it reads
    the same _luxid_claims() envelope the adapter now builds without a
    "userinfo" key — so it is pinned here to prove the skipped fetch did not
    quietly starve the signup flow.
    """

    def setUp(self):
        cache.clear()
        # The handler refuses to run unless the crush.lu LuxID login flag is
        # set; pre_social_login normally sets it.
        crush_signals._thread_local.is_crush_luxid_login = True

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def _signup(self, envelope):
        user = User.objects.create_user(
            username="newbie@example.com",
            email="newbie@example.com",
            password="pass123",
        )
        # Creating the SocialAccount fires the post_save receiver.
        SocialAccount.objects.create(
            user=user, provider="luxid", uid="luxid-sub-new", extra_data=envelope
        )
        return user

    def test_signup_from_id_token_only_envelope(self):
        """The shape the adapter now produces when /userinfo is skipped."""
        user = self._signup({"id_token": _complete_id_token()})

        profile = CrushProfile.objects.get(user=user)
        self.assertEqual(profile.date_of_birth, date(1990, 1, 1))
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.phone_number, "+352691234567")
        self.assertTrue(profile.phone_verified)
        self.assertIsNotNone(profile.phone_verified_at)
        self.assertEqual(profile.preferred_language, "fr")

    def test_signup_from_both_sources_lets_userinfo_win(self):
        """Unchanged merge rule: userinfo overrides id_token per-key."""
        user = self._signup(
            {
                "id_token": _complete_id_token(gender="male"),
                "userinfo": {"gender": "female"},
            }
        )

        profile = CrushProfile.objects.get(user=user)
        self.assertEqual(profile.gender, "F")
        # id_token-only claims survive the merge.
        self.assertEqual(profile.date_of_birth, date(1990, 1, 1))

    def test_signup_skips_untrusted_country_phone(self):
        """Unchanged guard, pinned so the skip cannot silently widen it."""
        user = self._signup(
            {"id_token": _complete_id_token(phone_number="+15551234567")}
        )

        profile = CrushProfile.objects.get(user=user)
        self.assertFalse(profile.phone_verified)
        self.assertNotEqual(profile.phone_number, "+15551234567")

    def test_signup_without_optional_claims_still_creates_profile(self):
        """A member who withheld everything optional still gets an account."""
        user = self._signup(
            {
                "id_token": {
                    "sub": "s",
                    "email": "newbie@example.com",
                    "given_name": "Ada",
                    "family_name": "Lovelace",
                }
            }
        )

        profile = CrushProfile.objects.get(user=user)
        self.assertIsNone(profile.date_of_birth)
        self.assertFalse(profile.phone_verified)
