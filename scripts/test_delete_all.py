#!/usr/bin/env python
"""
Test script to delete all contacts from Outlook using the new function.
This runs locally without needing deployment.

Usage:
    python test_delete_all.py
"""

import os
import sys
import django

# Load .env file first
from dotenv import load_dotenv
load_dotenv()

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

django.setup()

from crush_lu.services.graph_contacts import GraphContactsService

def main():
    print("\n" + "="*60)
    print("OUTLOOK CONTACT DELETION TEST")
    print("="*60 + "\n")

    try:
        # Temporarily override DEBUG for testing
        from django.conf import settings
        original_debug = settings.DEBUG
        settings.DEBUG = False  # Bypass DEBUG check

        service = GraphContactsService()
        print(f"Connected to mailbox: {service.mailbox}\n")

        # Step 1: List all contacts
        print("Step 1: Listing all contacts from Outlook...")
        contacts = service.list_all_contacts_from_outlook()
        print(f"[OK] Found {len(contacts)} contacts\n")

        if len(contacts) > 0:
            print("Sample contacts:")
            for i, contact in enumerate(contacts[:5], 1):
                print(f"  {i}. {contact.get('displayName', 'Unknown')} ({contact.get('id')})")
            if len(contacts) > 5:
                print(f"  ... and {len(contacts) - 5} more\n")

        # Step 2: Ask for confirmation
        print("\n" + "-"*60)
        print(f"WARNING: This will delete ALL {len(contacts)} contacts!")
        print("-"*60)

        # Check if --confirm flag is passed
        if len(sys.argv) > 1 and sys.argv[1] == '--confirm':
            print("\n[AUTO-CONFIRMED via --confirm flag]")
        else:
            try:
                confirmation = input("\nType 'DELETE' to confirm: ")
                if confirmation != 'DELETE':
                    print("[ABORTED] No contacts were deleted.")
                    return
            except EOFError:
                print("\n[ABORTED] No input provided.")
                return

        # Step 3: Delete all contacts
        print("\nStep 2: Deleting all contacts from Outlook...")
        stats = service.delete_all_contacts_from_outlook()

        print("\n" + "="*60)
        print("DELETION COMPLETE")
        print("="*60)
        print(f"Total found:    {stats['total']}")
        print(f"Deleted:        {stats['deleted']}")
        print(f"Errors:         {stats['errors']}")
        print("="*60 + "\n")

        if stats['errors'] > 0:
            print("[WARNING] Some deletions failed. Check logs for details.")
        else:
            print("[SUCCESS] All contacts deleted successfully!")

        # Restore DEBUG setting
        settings.DEBUG = original_debug

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
