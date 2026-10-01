"""Canonical legal-document versions for Crush.lu.

``CURRENT_TERMS_VERSION`` is the version of the Terms of Service / Privacy
Policy that a member accepts. It must match the "Version X.Y" shown on
``terms_of_service.html`` and ``privacy_policy.html`` (a test enforces this).

Every consent writer stamps it via ``UserDataConsent.save()``. Existing rows
keep whatever version they were recorded with: there is no backfill and no
re-ask. Always read it as ``legal.CURRENT_TERMS_VERSION`` (module attribute)
so a bump takes effect everywhere at once.
"""

CURRENT_TERMS_VERSION = "2.1"
