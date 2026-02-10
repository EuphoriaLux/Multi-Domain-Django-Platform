# crush_lu/services/graph_contacts.py
"""
Microsoft Graph API service for syncing Crush.lu profiles to Outlook contacts.

Syncs user contact information to the noreply@crush.lu shared mailbox,
allowing caller ID recognition when Crush.lu users call.

Environment:
- Only enabled in production when OUTLOOK_CONTACT_SYNC_ENABLED=true
- Reuses existing Graph API credentials from email backend

Graph API endpoints used:
- POST /users/{mailbox}/contacts - Create contact
- PATCH /users/{mailbox}/contacts/{id} - Update contact
- DELETE /users/{mailbox}/contacts/{id} - Delete contact
- GET /users/{mailbox}/contacts - List contacts
"""

import logging
import os
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# Graph API endpoint
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


def is_sync_enabled(request=None) -> bool:
    """
    Check if Outlook contact sync is enabled for this environment.

    Returns True only in production when explicitly enabled AND for Crush.lu only.
    This prevents test data from syncing to Outlook contacts and ensures sync
    only happens for the Crush.lu platform, not other platforms in the multi-domain app.

    Args:
        request: Optional HttpRequest object. If provided, uses request.urlconf
                 to determine the platform. If not provided, assumes Crush.lu.

    Returns:
        bool: True if sync is enabled, False otherwise
    """
    import sys

    # NEVER sync during tests (check for actual test execution, not just imported modules)
    # pytest sets sys._called_from_test, Django sets DJANGO_TEST_PROCESSES during testing
    if hasattr(sys, "_called_from_test") or os.getenv("DJANGO_TEST_PROCESSES"):
        return False

    # Never sync in DEBUG mode (local development)
    if settings.DEBUG:
        return False

    # ONLY sync for Crush.lu platform (not VinsDelux, Entreprinder, etc.)
    # Check request.urlconf (set by DomainURLRoutingMiddleware) or fallback to ROOT_URLCONF
    if request is not None:
        current_urlconf = getattr(request, "urlconf", None) or getattr(
            settings, "ROOT_URLCONF", ""
        )
    else:
        # When called without request (e.g., from management command or Azure Function),
        # assume it's for Crush.lu since this is the crush_lu app
        current_urlconf = "azureproject.urls_crush"

    if "urls_crush" not in current_urlconf:
        return False

    # Explicit opt-in required via environment variable
    return os.getenv("OUTLOOK_CONTACT_SYNC_ENABLED", "").lower() == "true"


class GraphContactsService:
    """
    Sync Crush.lu profiles to Outlook contacts via Microsoft Graph API.

    Uses the same credentials as the Graph email backend to access
    the noreply@crush.lu shared mailbox contacts.
    """

    def __init__(self):
        """Initialize with Graph API credentials from environment or settings."""
        # Try environment variables first (for local dev with .env),
        # then fall back to settings (for production)
        self.tenant_id = os.getenv("GRAPH_TENANT_ID") or getattr(
            settings, "GRAPH_TENANT_ID", None
        )
        self.client_id = os.getenv("GRAPH_CLIENT_ID") or getattr(
            settings, "GRAPH_CLIENT_ID", None
        )
        self.client_secret = os.getenv("GRAPH_CLIENT_SECRET") or getattr(
            settings, "GRAPH_CLIENT_SECRET", None
        )
        self.mailbox = (
            os.getenv("GRAPH_FROM_EMAIL")
            or getattr(settings, "GRAPH_FROM_EMAIL", None)
            or "noreply@crush.lu"
        )

        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError(
                "Microsoft Graph credentials not configured. "
                "Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET."
            )

    def get_access_token(self) -> str:
        """
        Get access token using client credentials flow (app-only authentication).

        Same pattern as graph_email_backend.py for consistency.

        Returns:
            str: Access token for Graph API calls

        Raises:
            ImportError: If msal package is not installed
            Exception: If token acquisition fails
        """
        try:
            import msal
        except ImportError:
            raise ImportError(
                "msal package is required for Graph API contact sync. "
                "Install with: pip install msal"
            )

        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        scope = ["https://graph.microsoft.com/.default"]

        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=authority,
            client_credential=self.client_secret,
        )

        # Try to get token from cache first
        result = app.acquire_token_silent(scope, account=None)
        if not result:
            # No cached token, acquire new one
            result = app.acquire_token_for_client(scopes=scope)

        if "access_token" in result:
            return result["access_token"]
        else:
            error = result.get(
                "error_description", result.get("error", "Unknown error")
            )
            logger.error(f"Failed to acquire Graph API access token: {error}")
            raise Exception(f"Failed to acquire access token: {error}")

    def _build_contact_payload(self, profile) -> dict:
        """
        Build the contact JSON payload from a CrushProfile.

        Maps CrushProfile fields to Outlook contact fields.

        Args:
            profile: CrushProfile instance

        Returns:
            dict: Contact payload for Graph API
        """
        user = profile.user

        # Build display name with suffix for identification
        display_name = f"{user.first_name} {user.last_name}".strip()
        if display_name:
            display_name = f"{display_name} (Crush.lu)"
        else:
            display_name = f"{user.email} (Crush.lu)"

        # Build notes with profile information
        notes_parts = [
            "Crush.lu User",
            "━" * 20,
            f"Profile ID: {profile.pk}",
        ]

        # Profile status
        if profile.is_approved:
            notes_parts.append("Status: Approved")
        else:
            # Check submission status (only if profile has been saved)
            if profile.pk:
                from crush_lu.models import ProfileSubmission

                submission = ProfileSubmission.objects.filter(profile=profile).first()
                if submission:
                    notes_parts.append(f"Status: {submission.get_status_display()}")
                else:
                    notes_parts.append("Status: Not Submitted")
            else:
                notes_parts.append("Status: Not Submitted")

        # Gender
        if profile.gender:
            gender_display = dict(profile.GENDER_CHOICES).get(
                profile.gender, profile.gender
            )
            notes_parts.append(f"Gender: {gender_display}")

        # Age (with defensive check for corrupted date_of_birth data)
        try:
            if profile.age:
                notes_parts.append(f"Age: {profile.age}")
        except (AttributeError, TypeError):
            # date_of_birth might be corrupted (stored as string instead of date)
            logger.warning(
                f"Profile {profile.pk} has invalid date_of_birth: "
                f"{type(profile.date_of_birth).__name__}"
            )

        # Location
        if profile.location:
            notes_parts.append(f"Location: {profile.location}")

        notes_parts.append("━" * 20)

        # Admin URL
        admin_url = (
            f"https://crush.lu/crush-admin/crush_lu/crushprofile/{profile.pk}/change/"
        )
        notes_parts.append(f"Admin: {admin_url}")

        # Build the contact payload
        payload = {
            "displayName": display_name,
            "categories": ["Crush.lu"],
            "personalNotes": "\n".join(notes_parts),
            "businessHomePage": admin_url,
        }

        # Given name and surname
        if user.first_name:
            payload["givenName"] = user.first_name
        if user.last_name:
            payload["surname"] = user.last_name

        # Mobile phone (primary identifier for caller ID)
        if profile.phone_number:
            payload["mobilePhone"] = profile.phone_number

        # Email
        if user.email:
            payload["emailAddresses"] = [
                {
                    "address": user.email,
                    "name": f"{user.first_name} {user.last_name}".strip() or user.email,
                }
            ]

        # Birthday (with defensive check for corrupted data)
        if profile.date_of_birth:
            try:
                # date_of_birth should be a date object, but may be corrupted as string
                if hasattr(profile.date_of_birth, "isoformat"):
                    payload["birthday"] = profile.date_of_birth.isoformat()
                else:
                    # It's already a string - try to use it directly
                    logger.warning(
                        f"Profile {profile.pk} date_of_birth is a string, not a date object"
                    )
                    payload["birthday"] = str(profile.date_of_birth)
            except (AttributeError, TypeError) as e:
                logger.warning(f"Profile {profile.pk} has invalid date_of_birth: {e}")

        # Location as business address city
        if profile.location:
            payload["businessAddress"] = {
                "city": profile.location,
                "countryOrRegion": "Luxembourg",
            }

        return payload

    def _upload_contact_photo(self, contact_id: str, profile, token: str) -> bool:
        """
        Upload profile photo to an Outlook contact.

        Args:
            contact_id: The Outlook contact ID
            profile: CrushProfile instance with photo_1
            token: Access token for Graph API

        Returns:
            bool: True if successful, False otherwise
        """
        import requests

        if not profile.photo_1:
            return False

        try:
            # Read the photo file
            photo_content = profile.photo_1.read()
            profile.photo_1.seek(0)  # Reset file pointer

            if not photo_content:
                logger.debug(f"Profile {profile.pk} photo_1 is empty")
                return False

            # Determine content type from filename
            filename = profile.photo_1.name.lower()
            if filename.endswith(".png"):
                content_type = "image/png"
            elif filename.endswith(".gif"):
                content_type = "image/gif"
            else:
                content_type = "image/jpeg"  # Default to JPEG

            # Upload photo via Graph API
            endpoint = f"{GRAPH_API_BASE}/users/{self.mailbox}/contacts/{contact_id}/photo/$value"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}

            response = requests.put(
                endpoint, headers=headers, data=photo_content, timeout=60
            )

            if response.status_code in [200, 204]:
                logger.info(f"Uploaded photo for contact {contact_id}")
                return True
            else:
                logger.warning(
                    f"Failed to upload photo for contact {contact_id}: "
                    f"HTTP {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.warning(f"Error uploading photo for contact {contact_id}: {e}")
            return False

    def create_contact(self, profile, force: bool = False) -> Optional[str]:
        """
        Create a new contact in Outlook.

        Args:
            profile: CrushProfile instance
            force: If True, bypass environment check (for local testing)

        Returns:
            str: Outlook contact ID if successful, None otherwise
        """
        import requests

        if not force and not is_sync_enabled():
            logger.debug("Outlook contact sync disabled for this environment")
            return None

        try:
            token = self.get_access_token()
            payload = self._build_contact_payload(profile)

            endpoint = f"{GRAPH_API_BASE}/users/{self.mailbox}/contacts"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            response = requests.post(
                endpoint, headers=headers, json=payload, timeout=30
            )

            if response.status_code in [200, 201]:
                data = response.json()
                contact_id = data.get("id")
                logger.info(
                    f"Created Outlook contact for profile {profile.pk}: {contact_id}"
                )

                # Upload photo if available
                if profile.photo_1:
                    self._upload_contact_photo(contact_id, profile, token)

                return contact_id
            else:
                logger.error(
                    f"Failed to create Outlook contact for profile {profile.pk}: "
                    f"HTTP {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logger.error(
                f"Error creating Outlook contact for profile {profile.pk}: {e}"
            )
            return None

    def update_contact(self, profile, force: bool = False) -> bool:
        """
        Update an existing contact in Outlook.

        Args:
            profile: CrushProfile instance with outlook_contact_id set
            force: If True, bypass environment check (for local testing)

        Returns:
            bool: True if successful, False otherwise
        """
        import requests

        if not force and not is_sync_enabled():
            logger.debug("Outlook contact sync disabled for this environment")
            return False

        if not profile.outlook_contact_id:
            logger.warning(
                f"Cannot update contact for profile {profile.pk}: no outlook_contact_id"
            )
            return False

        try:
            token = self.get_access_token()
            payload = self._build_contact_payload(profile)

            endpoint = f"{GRAPH_API_BASE}/users/{self.mailbox}/contacts/{profile.outlook_contact_id}"
            auth_header = {"Authorization": f"Bearer {token}"}

            # Fetch current ETag to avoid 412 concurrency conflicts
            get_response = requests.get(
                endpoint,
                headers=auth_header,
                params={"$select": "id"},
                timeout=30,
            )
            if get_response.status_code == 404:
                logger.warning(
                    f"Outlook contact {profile.outlook_contact_id} not found for "
                    f"profile {profile.pk}, will recreate"
                )
                profile.outlook_contact_id = ""
                profile.save(update_fields=["outlook_contact_id"])
                return False
            etag = get_response.headers.get("ETag")

            headers = {
                **auth_header,
                "Content-Type": "application/json",
            }
            if etag:
                headers["If-Match"] = etag

            response = requests.patch(
                endpoint, headers=headers, json=payload, timeout=30
            )

            if response.status_code in [200, 204]:
                logger.info(
                    f"Updated Outlook contact for profile {profile.pk}: "
                    f"{profile.outlook_contact_id}"
                )

                # Update photo if available
                if profile.photo_1:
                    self._upload_contact_photo(
                        profile.outlook_contact_id, profile, token
                    )

                return True
            elif response.status_code == 404:
                # Contact no longer exists - clear the ID and try to create
                logger.warning(
                    f"Outlook contact {profile.outlook_contact_id} not found for "
                    f"profile {profile.pk}, will recreate"
                )
                profile.outlook_contact_id = ""
                profile.save(update_fields=["outlook_contact_id"])
                return False
            elif response.status_code == 412:
                # ETag mismatch - contact was modified concurrently, retry once
                logger.warning(
                    f"Concurrency conflict updating contact for profile "
                    f"{profile.pk}, retrying"
                )
                get_response = requests.get(
                    endpoint,
                    headers=auth_header,
                    params={"$select": "id"},
                    timeout=30,
                )
                etag = get_response.headers.get("ETag")
                if etag:
                    headers["If-Match"] = etag
                response = requests.patch(
                    endpoint, headers=headers, json=payload, timeout=30
                )
                if response.status_code in [200, 204]:
                    logger.info(
                        f"Updated Outlook contact for profile {profile.pk} "
                        f"on retry: {profile.outlook_contact_id}"
                    )
                    if profile.photo_1:
                        self._upload_contact_photo(
                            profile.outlook_contact_id, profile, token
                        )
                    return True
                logger.error(
                    f"Failed to update Outlook contact for profile {profile.pk} "
                    f"after retry: HTTP {response.status_code} - {response.text}"
                )
                return False
            else:
                logger.error(
                    f"Failed to update Outlook contact for profile {profile.pk}: "
                    f"HTTP {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(
                f"Error updating Outlook contact for profile {profile.pk}: {e}"
            )
            return False

    def delete_contact(self, outlook_contact_id: str, force: bool = False) -> bool:
        """
        Delete a contact from Outlook.

        Args:
            outlook_contact_id: The Outlook contact ID to delete
            force: If True, bypass environment check (for local testing)

        Returns:
            bool: True if successful, False otherwise
        """
        import requests

        if not force and not is_sync_enabled():
            logger.debug("Outlook contact sync disabled for this environment")
            return False

        try:
            token = self.get_access_token()

            endpoint = (
                f"{GRAPH_API_BASE}/users/{self.mailbox}/contacts/{outlook_contact_id}"
            )
            headers = {
                "Authorization": f"Bearer {token}",
            }

            response = requests.delete(endpoint, headers=headers, timeout=30)

            if response.status_code in [200, 204]:
                logger.info(f"Deleted Outlook contact: {outlook_contact_id}")
                return True
            elif response.status_code == 404:
                # Already deleted
                logger.info(f"Outlook contact already deleted: {outlook_contact_id}")
                return True
            else:
                logger.error(
                    f"Failed to delete Outlook contact {outlook_contact_id}: "
                    f"HTTP {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Error deleting Outlook contact {outlook_contact_id}: {e}")
            return False

    def sync_profile(self, profile, force: bool = False) -> Optional[str]:
        """
        Sync a single profile to Outlook contacts.

        Creates a new contact if outlook_contact_id is empty,
        otherwise updates the existing contact.

        Args:
            profile: CrushProfile instance
            force: If True, bypass environment check (for local testing)

        Returns:
            str: Outlook contact ID if successful, None otherwise
        """
        if not force and not is_sync_enabled():
            logger.debug("Outlook contact sync disabled for this environment")
            return None

        # Skip profiles without phone numbers (can't identify callers)
        if not profile.phone_number:
            logger.debug(f"Skipping profile {profile.pk} - no phone number")
            return None

        if profile.outlook_contact_id:
            # Update existing contact
            success = self.update_contact(profile, force=force)
            if success:
                return profile.outlook_contact_id
            # If update failed (e.g., contact deleted), try to create
            if not profile.outlook_contact_id:
                # ID was cleared by update_contact due to 404
                contact_id = self.create_contact(profile, force=force)
                if contact_id:
                    profile.outlook_contact_id = contact_id
                    profile.save(update_fields=["outlook_contact_id"])
                    return contact_id
            return None
        else:
            # Create new contact
            contact_id = self.create_contact(profile, force=force)
            if contact_id:
                profile.outlook_contact_id = contact_id
                profile.save(update_fields=["outlook_contact_id"])
                return contact_id
            return None

    def sync_all_profiles(self, dry_run: bool = False) -> dict:
        """
        Sync all phone-verified CrushProfiles to Outlook contacts.

        Syncs profiles with verified phone numbers to enable caller ID,
        regardless of approval status.

        Args:
            dry_run: If True, only preview what would be synced

        Returns:
            dict: Statistics about the sync operation
                {
                    'total': int,
                    'synced': int,
                    'skipped': int,
                    'errors': int,
                    'dry_run': bool
                }
        """
        from crush_lu.models import CrushProfile
        from crush_lu.signals import is_test_user

        stats = {"total": 0, "synced": 0, "skipped": 0, "errors": 0, "dry_run": dry_run}

        if not is_sync_enabled() and not dry_run:
            logger.warning("Outlook contact sync disabled for this environment")
            return stats

        # Only sync phone-verified profiles (caller ID requires verified phone)
        profiles = CrushProfile.objects.select_related("user").filter(
            phone_verified=True,
            phone_number__isnull=False
        ).exclude(phone_number='')
        stats["total"] = profiles.count()

        for profile in profiles:
            # Skip test users
            if is_test_user(profile.user):
                stats["skipped"] += 1
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would skip profile {profile.pk} "
                        f"({profile.user.email}) - test user"
                    )
                continue

            # Skip profiles without phone numbers
            if not profile.phone_number:
                stats["skipped"] += 1
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would skip profile {profile.pk} "
                        f"({profile.user.email}) - no phone number"
                    )
                continue

            if dry_run:
                action = "update" if profile.outlook_contact_id else "create"
                # Log without PII - only use profile ID and user ID
                logger.info(
                    f"[DRY RUN] Would {action} contact for profile {profile.pk} (user ID: {profile.user.id})"
                )
                stats["synced"] += 1
            else:
                try:
                    result = self.sync_profile(profile)
                    if result:
                        stats["synced"] += 1
                    else:
                        stats["errors"] += 1
                except Exception as e:
                    logger.error(f"Error syncing profile {profile.pk}: {e}")
                    stats["errors"] += 1

        return stats

    def list_all_contacts_from_outlook(self) -> list:
        """
        List all contacts from Outlook mailbox via Graph API.

        Returns:
            list: List of contact objects with id and displayName
        """
        import requests

        if not is_sync_enabled():
            logger.warning("Outlook contact sync disabled for this environment")
            return []

        try:
            token = self.get_access_token()
            endpoint = f"{GRAPH_API_BASE}/users/{self.mailbox}/contacts"
            headers = {
                "Authorization": f"Bearer {token}",
            }

            contacts = []
            next_link = endpoint

            # Handle pagination
            while next_link:
                response = requests.get(next_link, headers=headers, timeout=30)

                if response.status_code != 200:
                    logger.error(
                        f"Failed to list Outlook contacts: "
                        f"HTTP {response.status_code} - {response.text}"
                    )
                    break

                data = response.json()
                contacts.extend(data.get("value", []))
                next_link = data.get("@odata.nextLink")

            logger.info(f"Found {len(contacts)} contacts in Outlook mailbox")
            return contacts

        except Exception as e:
            logger.error(f"Error listing Outlook contacts: {e}")
            return []

    def delete_all_contacts_from_outlook(self) -> dict:
        """
        Delete ALL contacts from Outlook mailbox via Graph API.

        This deletes contacts directly from Outlook, regardless of database state.
        Use with extreme caution - this is a destructive operation!

        Returns:
            dict: Statistics about the deletion
                {
                    'total': int,      # Total contacts found in Outlook
                    'deleted': int,    # Successfully deleted
                    'errors': int,     # Failed deletions
                }
        """
        stats = {"total": 0, "deleted": 0, "errors": 0}

        if not is_sync_enabled():
            logger.warning("Outlook contact sync disabled for this environment")
            return stats

        # Get all contacts from Outlook
        contacts = self.list_all_contacts_from_outlook()
        stats["total"] = len(contacts)

        logger.warning(f"Deleting {stats['total']} contacts from Outlook mailbox...")

        # Delete each contact
        for contact in contacts:
            contact_id = contact.get("id")
            display_name = contact.get("displayName", "Unknown")

            try:
                success = self.delete_contact(contact_id)
                if success:
                    stats["deleted"] += 1
                    logger.info(f"Deleted: {display_name} ({contact_id})")
                else:
                    stats["errors"] += 1
                    logger.error(f"Failed to delete: {display_name} ({contact_id})")
            except Exception as e:
                logger.error(f"Error deleting contact {display_name}: {e}")
                stats["errors"] += 1

        logger.info(
            f"Deletion complete: {stats['deleted']} deleted, "
            f"{stats['errors']} errors out of {stats['total']} total"
        )

        return stats

    def delete_all_contacts(self) -> dict:
        """
        Delete all Crush.lu contacts from Outlook.

        Use with caution - this removes all synced contacts.

        Returns:
            dict: Statistics about the deletion
                {
                    'total': int,
                    'deleted': int,
                    'errors': int,
                }
        """
        from crush_lu.models import CrushProfile

        stats = {"total": 0, "deleted": 0, "errors": 0}

        if not is_sync_enabled():
            logger.warning("Outlook contact sync disabled for this environment")
            return stats

        profiles = CrushProfile.objects.exclude(
            outlook_contact_id__isnull=True
        ).exclude(outlook_contact_id="")
        stats["total"] = profiles.count()

        for profile in profiles:
            try:
                success = self.delete_contact(profile.outlook_contact_id)
                if success:
                    profile.outlook_contact_id = ""
                    profile.save(update_fields=["outlook_contact_id"])
                    stats["deleted"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                logger.error(f"Error deleting contact for profile {profile.pk}: {e}")
                stats["errors"] += 1

        return stats
