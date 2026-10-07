"""Deployment-time configuration checks for the azureproject settings.

``ERASURE_DIGEST_KEY`` keys the digests in ``hub.ErasedPhoneNumber`` (GDPR
erasure tombstones). It must be a dedicated key that is never rotated: the
numbers are gone after erasure, so a digest cannot be rebuilt, and falling back
to ``SECRET_KEY`` would orphan every tombstone when that key is rotated. The
Bicep configuration does not provision it, so a redeploy from infra, or a
manually configured production slot losing the setting, would silently fall
back. This module makes that loud without blocking startup: the staging slot
legitimately has no key (the setting is slot-sticky on production only), and a
hard failure there would break every staging deploy.
"""

import os

from django.conf import settings
from django.core.checks import Tags, Warning, register

ERASURE_KEY_CHECK_ID = "azureproject.W001"


def is_production_slot(environ=None):
    """True on the Azure production slot (not staging, not local/CI)."""
    environ = os.environ if environ is None else environ
    if "WEBSITE_HOSTNAME" not in environ:
        return False
    return environ.get("WEBSITE_SLOT_NAME", "").strip().lower() in ("", "production")


def erasure_key_problem(environ=None):
    """A human-readable problem string, or ``None`` when the key is fine."""
    if not is_production_slot(environ):
        return None
    if getattr(settings, "ERASURE_DIGEST_KEY", ""):
        return None
    return (
        "ERASURE_DIGEST_KEY is not set on the production slot: erased phone "
        "number tombstones fall back to SECRET_KEY and will be orphaned when "
        "it is rotated."
    )


@register(Tags.security, deploy=True)
def check_erasure_digest_key(app_configs, **kwargs):
    problem = erasure_key_problem()
    if problem is None:
        return []
    return [
        Warning(
            problem,
            hint=(
                "Set a dedicated, never-rotated ERASURE_DIGEST_KEY as a "
                "slot-sticky App Service setting on the production slot "
                "(see README 'Production Environment Variables')."
            ),
            id=ERASURE_KEY_CHECK_ID,
        )
    ]
