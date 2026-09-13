"""
Google Business Profile helpers for Crush.lu.

Crush.lu's listing (`locations/10954543438099561082`, a verified
`CUSTOMER_LOCATION_ONLY` service-area business) is reachable through the
Business Profile APIs — Google approved allowlist access for Cloud project
297130634516 on 2026-09-11.

This module currently holds only the part that needs **no API call at all**:
the public review deep link. The API-backed pieces — event posts on the legacy
v4.9 `mybusiness.googleapis.com`, and the review pull/reply loop — land later
and belong here too; see
`C:\\GitHub\\ai-memory-hub\\policies\\gbp-integration-implementation-brief.md`
for their specification and traps.

References:
- https://developers.google.com/my-business/content/overview
"""

from django.conf import settings


def get_review_url() -> str:
    """The listing's "write a review" deep link, or "" when asking is not allowed.

    Mirrors ``google_indexing.get_indexing_domain()``: an unset value is not a
    misconfiguration to warn about, it is the deployment saying "not from here".
    Every caller must treat "" as *render nothing* rather than falling back to a
    hardcoded default — a staging slot must not be able to point members at the
    production listing.
    """
    return (getattr(settings, "CRUSH_GOOGLE_REVIEW_URL", "") or "").strip()
