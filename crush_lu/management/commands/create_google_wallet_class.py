"""
Management command to create or update the Google Wallet class for Crush.lu member passes.

The class defines the template for all Crush.lu member passes in Google Wallet,
including branding (logo, colors), field structure, and display options.

Usage:
    python manage.py create_google_wallet_class
    python manage.py create_google_wallet_class --update

Requirements:
    - Google Wallet API credentials configured in settings:
      - WALLET_GOOGLE_ISSUER_ID
      - WALLET_GOOGLE_CLASS_ID
      - WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL
      - WALLET_GOOGLE_PRIVATE_KEY or WALLET_GOOGLE_PRIVATE_KEY_PATH
"""

import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    import google.auth.exceptions
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    GOOGLE_AUTH_AVAILABLE = False

import httpx


GOOGLE_WALLET_API_BASE = "https://walletobjects.googleapis.com/walletobjects/v1"


class Command(BaseCommand):
    help = "Create or update the Google Wallet class for Crush.lu member passes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="Update existing class instead of creating new one",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the class JSON without making API calls",
        )

    def handle(self, *args, **options):
        # Validate settings
        issuer_id = getattr(settings, "WALLET_GOOGLE_ISSUER_ID", None)
        class_suffix = getattr(settings, "WALLET_GOOGLE_CLASS_SUFFIX", "crush-member")

        if not issuer_id:
            raise CommandError(
                "WALLET_GOOGLE_ISSUER_ID is not configured in settings. "
                "Please configure Google Wallet settings first."
            )

        class_id = f"{issuer_id}.{class_suffix}"

        # Build the class definition
        wallet_class = self._build_class_definition(issuer_id, class_id)

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("Dry run - class JSON:"))
            self.stdout.write(json.dumps(wallet_class, indent=2))
            return

        # Get credentials
        credentials = self._get_credentials()
        if not credentials:
            raise CommandError(
                "Could not obtain Google credentials. "
                "Ensure WALLET_GOOGLE_PRIVATE_KEY or WALLET_GOOGLE_PRIVATE_KEY_PATH is configured."
            )

        # Create or update the class
        if options["update"]:
            self._update_class(class_id, wallet_class, credentials)
        else:
            self._create_class(class_id, wallet_class, credentials)

    def _build_class_definition(self, issuer_id, class_id):
        """
        Build the Google Wallet Generic class definition.

        This defines the template for all Crush.lu member passes:
        - Branding (logo, colors, card title)
        - Field structure (text modules, info module)
        - Display options
        """
        return {
            "id": class_id,
            "issuerName": "Crush.lu",
            "classTemplateInfo": {
                "cardTemplateOverride": {
                    "cardRowTemplateInfos": [
                        {
                            "twoItems": {
                                "startItem": {
                                    "firstValue": {
                                        "fields": [
                                            {
                                                "fieldPath": "object.textModulesData['member_status']"
                                            }
                                        ]
                                    }
                                },
                                "endItem": {
                                    "firstValue": {
                                        "fields": [
                                            {
                                                "fieldPath": "object.textModulesData['points']"
                                            }
                                        ]
                                    }
                                },
                            }
                        },
                        {
                            "oneItem": {
                                "item": {
                                    "firstValue": {
                                        "fields": [
                                            {
                                                "fieldPath": "object.textModulesData['next_event']"
                                            }
                                        ]
                                    }
                                }
                            }
                        },
                    ]
                }
            },
            # Crush.lu brand colors
            "hexBackgroundColor": "#9B59B6",  # crush-purple
            # Enable multiple users to save the same class
            "multipleDevicesAndHoldersAllowedStatus": "ONE_USER_ALL_DEVICES",
            # Review state - use UNDER_REVIEW for production, DRAFT for testing
            "reviewStatus": "UNDER_REVIEW",
            # Links shown on the pass
            "linksModuleData": {
                "uris": [
                    {
                        "uri": "https://crush.lu",
                        "description": "Visit Crush.lu",
                        "id": "website",
                    },
                    {
                        "uri": "https://crush.lu/events/",
                        "description": "Browse Events",
                        "id": "events",
                    },
                ]
            },
            # Hero image for the class (optional - can be overridden per object)
            # "heroImage": {
            #     "sourceUri": {
            #         "uri": "https://crush.lu/static/crush_lu/images/wallet-hero.png"
            #     },
            #     "contentDescription": {
            #         "defaultValue": {
            #             "language": "en-US",
            #             "value": "Crush.lu"
            #         }
            #     }
            # },
            # Class-level info module (shown on all passes)
            "infoModuleData": {
                "labelValueRows": [
                    {
                        "columns": [
                            {
                                "label": "Membership Benefits",
                                "value": "Event access, referral rewards, priority booking",
                            }
                        ]
                    },
                    {
                        "columns": [
                            {
                                "label": "Contact",
                                "value": "support@crush.lu",
                            }
                        ]
                    },
                ]
            },
            # Callback URL for pass updates (optional)
            # "callbackOptions": {
            #     "url": "https://crush.lu/api/wallet/google/callback/"
            # },
        }

    def _get_credentials(self):
        """
        Get Google credentials for API authentication.

        Tries to load from:
        1. WALLET_GOOGLE_PRIVATE_KEY (inline key)
        2. WALLET_GOOGLE_PRIVATE_KEY_PATH (key file path)
        """
        if not GOOGLE_AUTH_AVAILABLE:
            self.stderr.write(
                self.style.ERROR(
                    "google-auth library not installed. "
                    "Install with: pip install google-auth"
                )
            )
            return None

        service_account_email = getattr(settings, "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL", None)
        private_key = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY", None)
        private_key_path = getattr(settings, "WALLET_GOOGLE_PRIVATE_KEY_PATH", None)

        if not service_account_email:
            self.stderr.write(
                self.style.ERROR("WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL is not configured")
            )
            return None

        # Build credentials info
        credentials_info = {
            "type": "service_account",
            "client_email": service_account_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }

        # Get private key
        if private_key:
            # Handle escaped newlines in environment variable
            if isinstance(private_key, str):
                private_key = private_key.replace("\\n", "\n")
            credentials_info["private_key"] = private_key
        elif private_key_path:
            try:
                with open(private_key_path, "r") as f:
                    # Read the full service account JSON if it's a JSON file
                    content = f.read()
                    if content.strip().startswith("{"):
                        full_credentials = json.loads(content)
                        return service_account.Credentials.from_service_account_info(
                            full_credentials,
                            scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
                        )
                    credentials_info["private_key"] = content
            except FileNotFoundError:
                self.stderr.write(
                    self.style.ERROR(f"Private key file not found: {private_key_path}")
                )
                return None
        else:
            self.stderr.write(
                self.style.ERROR(
                    "Neither WALLET_GOOGLE_PRIVATE_KEY nor WALLET_GOOGLE_PRIVATE_KEY_PATH is configured"
                )
            )
            return None

        try:
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=["https://www.googleapis.com/auth/wallet_object.issuer"],
            )
            return credentials
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error creating credentials: {e}"))
            return None

    def _get_auth_token(self, credentials):
        """Get OAuth2 access token from credentials."""
        try:
            credentials.refresh(Request())
            return credentials.token
        except google.auth.exceptions.RefreshError as e:
            self.stderr.write(self.style.ERROR(f"Error refreshing credentials: {e}"))
            return None

    def _create_class(self, class_id, wallet_class, credentials):
        """Create a new Google Wallet class."""
        token = self._get_auth_token(credentials)
        if not token:
            raise CommandError("Could not obtain access token")

        url = f"{GOOGLE_WALLET_API_BASE}/genericClass"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        self.stdout.write(f"Creating Google Wallet class: {class_id}")

        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=wallet_class)

            if response.status_code == 200:
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully created class: {class_id}")
                )
                return
            elif response.status_code == 409:
                self.stdout.write(
                    self.style.WARNING(
                        f"Class {class_id} already exists. Use --update to modify it."
                    )
                )
                return
            else:
                self.stderr.write(
                    self.style.ERROR(
                        f"Failed to create class (HTTP {response.status_code}): {response.text}"
                    )
                )
                raise CommandError("Failed to create Google Wallet class")

    def _update_class(self, class_id, wallet_class, credentials):
        """Update an existing Google Wallet class."""
        token = self._get_auth_token(credentials)
        if not token:
            raise CommandError("Could not obtain access token")

        url = f"{GOOGLE_WALLET_API_BASE}/genericClass/{class_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        self.stdout.write(f"Updating Google Wallet class: {class_id}")

        with httpx.Client(timeout=30.0) as client:
            # Use PATCH for partial update, PUT for full replacement
            response = client.put(url, headers=headers, json=wallet_class)

            if response.status_code == 200:
                self.stdout.write(
                    self.style.SUCCESS(f"Successfully updated class: {class_id}")
                )
                return
            elif response.status_code == 404:
                self.stdout.write(
                    self.style.WARNING(
                        f"Class {class_id} does not exist. Creating new class..."
                    )
                )
                self._create_class(class_id, wallet_class, credentials)
                return
            else:
                self.stderr.write(
                    self.style.ERROR(
                        f"Failed to update class (HTTP {response.status_code}): {response.text}"
                    )
                )
                raise CommandError("Failed to update Google Wallet class")
