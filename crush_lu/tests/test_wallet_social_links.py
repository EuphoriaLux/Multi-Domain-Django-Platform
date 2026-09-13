"""The social links on a Google Wallet member pass come from CrushSiteConfig.

The pass shipped for months pointing at instagram.com/crush.lu — a handle that
belongs to an unrelated person — because both Google builders hardcoded it.
The site footer and the email templates already render whatever the
CrushSiteConfig singleton holds, so the pass now reads the same source and can
never disagree with them. When the singleton is blank or unreadable it falls
back to the real accounts rather than dropping the link.
"""

import base64
import json

import pytest

from crush_lu.models import CrushSiteConfig
from crush_lu.wallet_pass import build_wallet_social_links

WRONG_INSTAGRAM = "instagram.com/crush.lu"
REAL_INSTAGRAM = "https://www.instagram.com/crushluofficial/"
REAL_FACEBOOK = "https://www.facebook.com/crushluxembourg"


def _uris(links):
    return {link["description"]: link["uri"] for link in links}


@pytest.mark.django_db
class TestBuildWalletSocialLinks:
    def test_reads_the_configured_urls(self):
        config = CrushSiteConfig.get_config()
        config.social_instagram_url = "https://www.instagram.com/configured/"
        config.social_facebook_url = "https://www.facebook.com/configured"
        config.save()

        uris = _uris(build_wallet_social_links())

        assert uris["📸 Instagram"] == "https://www.instagram.com/configured/"
        assert uris["👍 Facebook"] == "https://www.facebook.com/configured"

    def test_blank_fields_fall_back_to_the_real_accounts(self):
        # A blank field must not drop the entry: the pass has a fixed layout,
        # and a missing link reads as a bug. It also must never fall back to
        # the old wrong handle.
        config = CrushSiteConfig.get_config()
        config.social_instagram_url = ""
        config.social_facebook_url = ""
        config.save()

        uris = _uris(build_wallet_social_links())

        assert uris["📸 Instagram"] == REAL_INSTAGRAM
        assert uris["👍 Facebook"] == REAL_FACEBOOK

    def test_a_partial_config_mixes_configured_and_fallback(self):
        config = CrushSiteConfig.get_config()
        config.social_instagram_url = "https://www.instagram.com/configured/"
        config.social_facebook_url = ""
        config.save()

        uris = _uris(build_wallet_social_links())

        assert uris["📸 Instagram"] == "https://www.instagram.com/configured/"
        assert uris["👍 Facebook"] == REAL_FACEBOOK

    def test_an_unreadable_config_falls_back(self, monkeypatch):
        # Building a pass must never fail because the singleton could not be
        # read (missing table on a fresh deploy, transient DB error).
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(CrushSiteConfig, "get_config", _boom)

        uris = _uris(build_wallet_social_links())

        assert uris["📸 Instagram"] == REAL_INSTAGRAM
        assert uris["👍 Facebook"] == REAL_FACEBOOK


@pytest.mark.django_db
class TestGoogleBuildersUseSiteConfig:
    """Both Google paths — the save-to-wallet JWT and the REST PATCH refresh —
    build their own linksModuleData, so each is checked separately."""

    @pytest.fixture
    def configured_socials(self):
        config = CrushSiteConfig.get_config()
        config.social_instagram_url = "https://www.instagram.com/from-config/"
        config.social_facebook_url = "https://www.facebook.com/from-config"
        config.save()
        return config

    @pytest.fixture
    def google_settings(self, settings):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        settings.WALLET_GOOGLE_ISSUER_ID = "3388000000022222222"
        settings.WALLET_GOOGLE_CLASS_ID = "3388000000022222222.crush_member"
        settings.WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL = (
            "wallet@example.iam.gserviceaccount.com"
        )
        settings.WALLET_GOOGLE_PRIVATE_KEY = pem.decode("utf-8")
        settings.WALLET_GOOGLE_PRIVATE_KEY_PATH = None

    def test_patch_refresh_payload(self, configured_socials, test_user_with_profile):
        from crush_lu.wallet import google_api

        _user, profile = test_user_with_profile

        payload = google_api._build_generic_object_payload(profile, "obj-1", "class-1")

        uris = _uris(payload["linksModuleData"]["uris"])
        assert uris["📸 Instagram"] == "https://www.instagram.com/from-config/"
        assert uris["👍 Facebook"] == "https://www.facebook.com/from-config"
        assert WRONG_INSTAGRAM not in json.dumps(payload)

    def test_save_to_wallet_jwt(
        self, configured_socials, google_settings, test_user_with_profile
    ):
        from crush_lu.wallet import build_google_wallet_jwt

        _user, profile = test_user_with_profile

        token = build_google_wallet_jwt(profile)

        claims_segment = token.split(".")[1]
        claims_segment += "=" * (-len(claims_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(claims_segment))
        generic_object = claims["payload"]["genericObjects"][0]

        uris = _uris(generic_object["linksModuleData"]["uris"])
        assert uris["📸 Instagram"] == "https://www.instagram.com/from-config/"
        assert uris["👍 Facebook"] == "https://www.facebook.com/from-config"
        assert WRONG_INSTAGRAM not in json.dumps(claims)

    def test_blank_config_never_emits_the_wrong_handle(
        self, google_settings, test_user_with_profile
    ):
        from crush_lu.wallet import build_google_wallet_jwt, google_api

        config = CrushSiteConfig.get_config()
        config.social_instagram_url = ""
        config.social_facebook_url = ""
        config.save()
        _user, profile = test_user_with_profile

        payload = google_api._build_generic_object_payload(profile, "obj-1", "class-1")
        token = build_google_wallet_jwt(profile)

        assert (
            _uris(payload["linksModuleData"]["uris"])["📸 Instagram"] == REAL_INSTAGRAM
        )
        assert WRONG_INSTAGRAM not in json.dumps(payload)
        assert WRONG_INSTAGRAM not in token
