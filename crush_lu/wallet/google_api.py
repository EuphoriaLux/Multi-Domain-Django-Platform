"""
Google Wallet REST API client for updating existing passes.

This module provides functions to update Google Wallet passes when user data changes
(points, tier, event registrations, etc.) and to send push notifications via
the messages array on pass objects.
"""
import json
import logging
import time
import uuid

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import translation
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .google_wallet import _load_private_key, _base64url_encode
from ..wallet_pass import build_wallet_pass_data

logger = logging.getLogger(__name__)

GOOGLE_WALLET_API_BASE = "https://walletobjects.googleapis.com/walletobjects/v1"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Per-phase ceiling for every call in this module. A bulk refresh lowers it to
# whatever is left of its wall-clock budget; this is the floor-level cap for
# the single-profile callers that have no budget of their own.
GOOGLE_WALLET_HTTP_TIMEOUT = 30.0


def _timeout_kwargs(timeout):
    """Build the per-request timeout kwarg, omitting it when unset.

    httpx reads an explicit ``timeout=None`` as "no timeout at all", so an
    absent override must be left out entirely rather than forwarded as None —
    passing it through would drop the client default and let one hung request
    hold the caller forever.

    What a value here does and does NOT bound: httpx applies a timeout PER
    PHASE — connect, write, pool, and read, the last of which restarts on every
    chunk received — so this limits how long any single phase may stall, not
    the end-to-end duration of a request. A batch's wall-clock budget is
    therefore enforced by the loop that stops STARTING requests once the
    deadline passes; one pathologically slow endpoint can still overrun that
    budget by roughly a single request's phases.

    That residual is accepted rather than fixed, because sync httpx offers no
    total-deadline or cancellation knob: the alternative is running each PATCH
    on a thread abandoned at the deadline, and a thread blocked on a socket
    read leaks for the life of the worker — strictly worse than a bounded
    overrun well inside gunicorn's 120s timeout.
    """
    if timeout is None:
        return {}
    # Constructed explicitly so the per-phase semantics above are visible here
    # rather than hidden inside httpx's float coercion.
    return {"timeout": httpx.Timeout(timeout)}


def _remaining_timeout(deadline):
    """Seconds left before `deadline`, capped at the per-phase ceiling.

    Returns a non-positive value once the budget is spent, which callers treat
    as "stop" — never as "no timeout".
    """
    return min(GOOGLE_WALLET_HTTP_TIMEOUT, deadline - time.monotonic())


def _get_access_token(client=None, timeout=None):
    """
    Generate an OAuth2 access token using service account credentials.
    Uses JWT bearer token flow for server-to-server authentication.

    Args:
        client: optional httpx.Client to borrow. A batch mints ONE token for
            its whole fan-out and reuses a single connection; without this,
            every pass update paid for its own exchange plus its own handshake.
        timeout: optional per-request timeout, so a caller working to a
            wall-clock budget is not held for the full default by a hanging
            OAuth endpoint.
    """
    service_account_email = getattr(settings, "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL", None)
    if not service_account_email:
        raise ValueError("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL not configured")

    issued_at = int(time.time())
    expires_at = issued_at + 3600  # 1 hour

    # JWT claims for Google OAuth2
    claims = {
        "iss": service_account_email,
        "sub": service_account_email,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": issued_at,
        "exp": expires_at,
        "scope": "https://www.googleapis.com/auth/wallet_object.issuer",
    }

    header = {"alg": "RS256", "typ": "JWT"}

    # Sign the JWT
    signing_input = b".".join([
        _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _base64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
    ])

    private_key = _load_private_key()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    signed_jwt = b".".join([signing_input, _base64url_encode(signature)]).decode("utf-8")

    # Exchange JWT for access token
    owned_client = None
    if client is None:
        owned_client = httpx.Client(timeout=GOOGLE_WALLET_HTTP_TIMEOUT)
        client = owned_client
    try:
        response = client.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
            **_timeout_kwargs(timeout),
        )

        if response.status_code != 200:
            logger.error("Failed to get access token: %s", response.text)
            raise ValueError(f"Failed to get access token: {response.status_code}")

        return response.json()["access_token"]
    finally:
        if owned_client is not None:
            owned_client.close()


def _build_generic_object_payload(profile, object_id, class_id):
    """
    Build the generic object payload for updating a pass.
    Mirrors the Buffalo Grill-inspired structure in google_wallet.py.
    """
    pass_data = build_wallet_pass_data(profile)

    # Map tier to emoji and promotional message
    tier = pass_data["membership_tier"] or "basic"
    tier_config = {
        "basic": {"emoji": "💜", "promo": "Invite friends & earn rewards! 🎁"},
        "bronze": {"emoji": "🥉", "promo": "You're on fire! Keep inviting! 🔥"},
        "silver": {"emoji": "🥈", "promo": "Almost Gold! Just a few more! ⭐"},
        "gold": {"emoji": "🥇", "promo": "VIP Status! Exclusive perks! 👑"},
    }
    tier_info = tier_config.get(tier, tier_config["basic"])

    # Get logo URL
    logo_url = getattr(settings, "WALLET_GOOGLE_LOGO_URL", None)
    if not logo_url:
        logo_url = "https://crush.lu/static/crush_lu/icons/android-launchericon-192-192.png"

    # Get hero/promo image URL
    hero_url = getattr(settings, "WALLET_GOOGLE_HERO_URL", None)
    if not hero_url:
        hero_url = "https://crush.lu/static/crush_lu/images/wallet-promo-banner.png"

    # Extract referral code
    referral_code = pass_data["referral_url"].split("/")[-2] if pass_data["referral_url"] else ""

    # Build text modules - Buffalo Grill style
    text_modules = [
        {
            "id": "promo_message",
            "header": "🎯 Your Rewards",
            "body": tier_info["promo"],
        },
        {
            "id": "points_counter",
            "header": "POINTS",
            "body": f"{pass_data['referral_points']:,}",
        },
    ]

    if pass_data["next_event"]:
        text_modules.append({
            "id": "next_event",
            "header": "📅 Next Event",
            "body": f"{pass_data['next_event']['title']}\n{pass_data['next_event']['date']}",
        })
    else:
        text_modules.append({
            "id": "next_event",
            "header": "📅 Upcoming Events",
            "body": "Browse events on crush.lu 💜",
        })

    # Build the object
    generic_object = {
        "id": object_id,
        "classId": class_id,
        "state": "active",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu Logo"}
            },
        },
        "header": {
            "defaultValue": {
                "language": "en-US",
                "value": f"Meet & get rewarded! {tier_info['emoji']}",
            }
        },
        "subheader": {
            "defaultValue": {
                "language": "en-US",
                "value": f"{pass_data['display_name']} • {pass_data['tier_display']}",
            }
        },
        "textModulesData": text_modules,
        "barcode": {
            "type": "QR_CODE",
            "value": pass_data["referral_url"],
            "alternateText": "Share me to earn points! 🤳",
        },
        "cardTitle": {
            "defaultValue": {"language": "en-US", "value": "Crush.lu"}
        },
        "hexBackgroundColor": "#9B59B6",
    }

    # Hero image - profile photo or promo banner
    if pass_data["photo_url"]:
        generic_object["heroImage"] = {
            "sourceUri": {"uri": pass_data["photo_url"]},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Your Profile"}
            },
        }
    else:
        generic_object["heroImage"] = {
            "sourceUri": {"uri": hero_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Invite friends, earn rewards!"}
            },
        }

    # Info module - back of card
    generic_object["infoModuleData"] = {
        "showLastUpdateTime": True,
        "labelValueRows": [
            {"columns": [{"label": "🔗 Your Referral Code", "value": referral_code}]},
            {"columns": [{"label": "🎁 How to Earn", "value": "Invite friends → +100 pts per signup!"}]},
            {"columns": [{"label": "🏆 Tier Levels", "value": "🥉 200 | 🥈 500 | 🥇 1000 pts"}]},
            {"columns": [{"label": "💰 Redeem Points", "value": "Event discounts & exclusive perks!"}]},
            {"columns": [{"label": "🗓️ Member Since", "value": pass_data["member_since"] or "Welcome!"}]},
        ],
    }

    # Links module
    generic_object["linksModuleData"] = {
        "uris": [
            {"uri": "https://crush.lu/dashboard/", "description": "👤 My Account"},
            {"uri": "https://crush.lu/events/", "description": "📅 Browse Events"},
            {"uri": pass_data["referral_url"], "description": "📋 Share Referral Link"},
            {"uri": "https://crush.lu", "description": "💜 Visit Crush.lu"},
            {"uri": "https://instagram.com/crush.lu", "description": "📸 Instagram"},
        ]
    }

    # Share functionality
    generic_object["appLinkData"] = {
        "androidAppLinkInfo": {
            "appTarget": {
                "targetUri": {
                    "uri": pass_data["referral_url"],
                    "description": "Open in Crush.lu",
                }
            }
        }
    }

    generic_object["shareData"] = {
        "displayName": f"Join me on Crush.lu! 💜 Use my code: {referral_code}",
        "url": pass_data["referral_url"],
    }

    return generic_object


def _patch_generic_object(profile, class_id, access_token, client, timeout=None):
    """PATCH one member object with a caller-supplied token and HTTP client.

    Split out of update_google_wallet_pass so a batch can mint ONE token and
    reuse ONE connection across its whole fan-out. The single-profile entry
    point below still owns both when called on its own, so its behaviour and
    return shape are unchanged.
    """
    # Render in the HOLDER's language, not the caller's. The next-event title
    # is modeltranslated, and every path that reaches here in bulk runs from
    # the admin — which is forced to English — so without this override a
    # French or German holder's card is rewritten in English by an edit they
    # had nothing to do with. Mirrors the Apple member-pass rebuild in
    # apple_pass.provide_pass_for_serial; None leaves the active language.
    with translation.override(getattr(profile, "preferred_language", "") or None):
        object_payload = _build_generic_object_payload(
            profile,
            profile.google_wallet_object_id,
            class_id,
        )

    # URL encode the object ID (it contains dots)
    encoded_object_id = profile.google_wallet_object_id.replace(".", "%2E")
    url = f"{GOOGLE_WALLET_API_BASE}/genericObject/{encoded_object_id}"

    response = client.patch(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=object_payload,
        **_timeout_kwargs(timeout),
    )

    if response.status_code == 200:
        logger.info(
            "Updated Google Wallet pass for user %s (object: %s)",
            profile.user_id,
            profile.google_wallet_object_id,
        )
        return {"success": True, "message": "Pass updated successfully"}

    if response.status_code == 404:
        # Pass doesn't exist in Google's system (user may have deleted it)
        logger.warning(
            "Google Wallet pass not found for user %s (object: %s)",
            profile.user_id,
            profile.google_wallet_object_id,
        )
        return {"success": False, "message": "Pass not found (may have been deleted)"}

    logger.error(
        "Failed to update Google Wallet pass: %s - %s",
        response.status_code,
        response.text,
    )
    return {"success": False, "message": f"API error: {response.status_code}"}


def update_google_wallet_pass(profile):
    """
    Update an existing Google Wallet pass for a user.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set

    Returns:
        dict: {"success": bool, "message": str}
    """
    if not profile.google_wallet_object_id:
        return {"success": False, "message": "User has no Google Wallet pass"}

    class_id = getattr(settings, "WALLET_GOOGLE_CLASS_ID", None)
    if not class_id:
        return {"success": False, "message": "WALLET_GOOGLE_CLASS_ID not configured"}

    try:
        with httpx.Client(timeout=GOOGLE_WALLET_HTTP_TIMEOUT) as client:
            access_token = _get_access_token(client=client)
            return _patch_generic_object(profile, class_id, access_token, client)

    except Exception as e:
        logger.exception("Error updating Google Wallet pass for user %s: %s", profile.user_id, e)
        return {"success": False, "message": str(e)}


def refresh_google_wallet_objects(profiles, context=""):
    """Bulk-refresh Google Wallet member objects under ONE shared budget.

    The member object prints the holder's next event (see the "Next Event" text
    module in _build_generic_object_payload, fed by get_next_event_for_pass), so
    an event-level change makes every attendee's card wrong. Nothing else fixes
    it: a Google object is server-side state that only changes when we PATCH it,
    and update_google_wallet_pass() mints its own OAuth token and opens its own
    connection per profile — so a naive per-attendee fan-out cost two round
    trips each and let request time grow with attendance.

    This mirrors passkit_service.refresh_ticket_serials — deferred to commit,
    bounded by BOTH a count limit and a wall-clock deadline, per-item failures
    isolated — with two differences that come from Google's model, not ours:

      * ONE token exchange and ONE keep-alive client serve the whole batch, so
        each extra pass costs a single PATCH rather than two requests. Each
        request's phase timeouts are also lowered to the remaining budget,
        since a 30s default is no kind of bound inside a 10s batch — though
        see _timeout_kwargs for what that does and does not guarantee: the
        budget is enforced by refusing to START work past the deadline, not by
        cancelling work already in flight.
      * There is NO Google equivalent of Wallet's periodic poll. An Apple pass
        that misses its push still updates, because the bulk query advanced its
        tag; a Google object that misses its PATCH stays stale until something
        else saves that profile. The cap is therefore a real last-resort brake
        rather than a downgrade to "slower" — it is logged at WARNING with the
        object ids, and the default limit is sized for a full event.

    Deferred to commit for the same reason as the Apple path: the update must
    not be visible to Google before the event change itself has committed.
    on_commit runs inline outside a transaction, and inline INSIDE the admin
    request either way — production leaves DJANGO_TASKS_BACKEND unset, so TASKS
    falls back to ImmediateBackend and enqueuing would not move the work off it.

    Returns the number of profiles scheduled; the work itself runs on commit.
    """
    profiles = [p for p in profiles if getattr(p, "google_wallet_object_id", "")]
    if not profiles:
        return 0

    class_id = getattr(settings, "WALLET_GOOGLE_CLASS_ID", None)
    if not class_id:
        logger.warning(
            "%s: skipping Google Wallet refresh for %s pass(es) — "
            "WALLET_GOOGLE_CLASS_ID is not configured",
            context or "bulk refresh",
            len(profiles),
        )
        return 0

    update_limit = getattr(settings, "WALLET_GOOGLE_BULK_UPDATE_LIMIT", 50)
    update_budget = getattr(
        settings, "WALLET_GOOGLE_BULK_UPDATE_BUDGET_SECONDS", 10.0
    )

    def _after_commit():
        deadline = time.monotonic() + update_budget
        updated = 0
        failed = 0
        # 404 means the holder deleted the pass — classified apart from a real
        # failure, matching update_all_google_wallet_passes.
        not_found = 0
        attempted = 0

        # A non-positive budget means there is no room to start anything; fall
        # straight through to the stale accounting rather than handing httpx a
        # zero timeout.
        token_timeout = _remaining_timeout(deadline)
        if token_timeout > 0:
            try:
                with httpx.Client(timeout=GOOGLE_WALLET_HTTP_TIMEOUT) as client:
                    # One exchange for the batch, and inside the budget as
                    # well: a stalled OAuth endpoint left at the 30s default
                    # would burn the whole allowance before a single pass had
                    # been touched.
                    access_token = _get_access_token(
                        client=client, timeout=token_timeout
                    )

                    for profile in profiles[:update_limit]:
                        remaining = _remaining_timeout(deadline)
                        if remaining <= 0:
                            break
                        attempted += 1
                        try:
                            result = _patch_generic_object(
                                profile,
                                class_id,
                                access_token,
                                client,
                                timeout=remaining,
                            )
                        except Exception:
                            # One unreachable object must not strand the rest.
                            failed += 1
                            logger.exception(
                                "Failed refreshing Google Wallet object %s",
                                profile.google_wallet_object_id,
                            )
                            continue
                        if result["success"]:
                            updated += 1
                        elif "not found" in result["message"].lower():
                            not_found += 1
                        else:
                            failed += 1
            except Exception:
                # Nothing can be PATCHed without a token, so a failed exchange
                # takes the whole batch with it. Never propagate — this runs
                # from on_commit inside a request whose write already
                # succeeded — and fall through, so the passes it could not
                # reach are reported as stale like any other skipped ones.
                logger.exception(
                    "%s: Google Wallet refresh could not run",
                    context or "bulk refresh",
                )

        logger.info(
            "%s: updated %s of %s Google Wallet pass(es) "
            "(%s failed, %s already deleted by their holder)",
            context or "bulk refresh",
            updated,
            len(profiles),
            failed,
            not_found,
        )

        skipped = len(profiles) - attempted
        if skipped > 0:
            # Never silent, and never merely "delayed": unlike an Apple pass
            # past the push cap, these do not heal on a later poll.
            stale = [p.google_wallet_object_id for p in profiles[attempted:]]
            logger.warning(
                "%s: %s Google Wallet pass(es) left STALE (limit=%s, "
                "budget=%ss) — Google has no client poll to heal them, so they "
                "keep showing the old details until the profile changes again: "
                "%s",
                context or "bulk refresh",
                skipped,
                update_limit,
                update_budget,
                ", ".join(stale[:10]) + ("…" if len(stale) > 10 else ""),
            )

    transaction.on_commit(_after_commit)
    return len(profiles)


def update_all_google_wallet_passes():
    """
    Update all existing Google Wallet passes.
    Useful for batch updates after design changes.

    Returns:
        dict: {"updated": int, "failed": int, "skipped": int}
    """
    from ..models import CrushProfile

    profiles = CrushProfile.objects.exclude(
        google_wallet_object_id__isnull=True
    ).exclude(
        google_wallet_object_id=""
    )

    results = {"updated": 0, "failed": 0, "skipped": 0}

    for profile in profiles:
        result = update_google_wallet_pass(profile)
        if result["success"]:
            results["updated"] += 1
        elif "not found" in result["message"].lower():
            results["skipped"] += 1
        else:
            results["failed"] += 1

    logger.info(
        "Batch Google Wallet update complete: %d updated, %d failed, %d skipped",
        results["updated"],
        results["failed"],
        results["skipped"],
    )

    return results


def send_wallet_notification_to_user(profile, header, body):
    """
    Send a notification to a single user's Google Wallet pass by PATCHing
    a new message onto the pass object.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set
        header: Notification title
        body: Notification body text

    Returns:
        dict: {"success": bool, "message": str}
    """
    if not profile.google_wallet_object_id:
        return {"success": False, "message": "User has no Google Wallet pass"}

    try:
        access_token = _get_access_token()

        message_payload = {
            "messages": [
                {
                    "header": header,
                    "body": body,
                    "id": f"msg-{uuid.uuid4().hex[:12]}",
                    "messageType": "TEXT",
                }
            ]
        }

        encoded_object_id = profile.google_wallet_object_id.replace(".", "%2E")
        url = f"{GOOGLE_WALLET_API_BASE}/genericObject/{encoded_object_id}"

        with httpx.Client(timeout=30.0) as client:
            response = client.patch(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=message_payload,
            )

            if response.status_code == 200:
                logger.info(
                    "Sent wallet notification to user %s (object: %s)",
                    profile.user_id,
                    profile.google_wallet_object_id,
                )
                return {"success": True, "message": "Notification sent"}

            elif response.status_code == 404:
                logger.warning(
                    "Google Wallet pass not found for user %s (object: %s)",
                    profile.user_id,
                    profile.google_wallet_object_id,
                )
                return {"success": False, "message": "Pass not found (may have been deleted)"}

            else:
                logger.error(
                    "Failed to send wallet notification: %s - %s",
                    response.status_code,
                    response.text,
                )
                return {"success": False, "message": f"API error: {response.status_code}"}

    except Exception as e:
        logger.exception(
            "Error sending wallet notification to user %s: %s", profile.user_id, e
        )
        return {"success": False, "message": str(e)}


def send_wallet_notification(header, body, tier_filter=None):
    """
    Send a notification to all Google Wallet pass holders by PATCHing
    a message onto each pass object.

    Args:
        header: Notification title
        body: Notification body text
        tier_filter: Optional membership tier to filter by (basic/bronze/silver/gold)

    Returns:
        dict: {"sent": int, "failed": int, "skipped": int}
    """
    from ..models import CrushProfile

    profiles = CrushProfile.objects.exclude(
        google_wallet_object_id__isnull=True
    ).exclude(
        google_wallet_object_id=""
    )

    if tier_filter:
        profiles = profiles.filter(membership_tier=tier_filter)

    results = {"sent": 0, "failed": 0, "skipped": 0}

    for profile in profiles:
        result = send_wallet_notification_to_user(profile, header, body)
        if result["success"]:
            results["sent"] += 1
        elif "not found" in result["message"].lower():
            results["skipped"] += 1
        else:
            results["failed"] += 1

    logger.info(
        "Batch wallet notification complete: %d sent, %d failed, %d skipped",
        results["sent"],
        results["failed"],
        results["skipped"],
    )

    return results