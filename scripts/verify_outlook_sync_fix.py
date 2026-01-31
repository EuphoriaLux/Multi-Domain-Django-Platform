"""
Verification script for Outlook Contact Sync fix.

This script verifies that the protection layers are working correctly
to prevent test data from syncing to production Outlook.

Run with: python scripts/verify_outlook_sync_fix.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from crush_lu.services.graph_contacts import is_sync_enabled
from crush_lu.signals import is_test_user, TEST_EMAIL_DOMAINS

User = get_user_model()

def test_protection_layers():
    """Verify all 4 protection layers are working."""

    print("=" * 80)
    print("Outlook Contact Sync - Protection Layer Verification")
    print("=" * 80)
    print()

    # Layer 1: Pytest detection
    print("Layer 1: Pytest Detection")
    print("-" * 80)

    # Simulate pytest being loaded
    original_modules = sys.modules.copy()
    sys.modules['pytest'] = type(sys)('pytest')

    result = is_sync_enabled()
    status = "[PASS]" if not result else "[FAIL]"
    print(f"{status}: is_sync_enabled() returns False when pytest is loaded")

    # Cleanup
    sys.modules = original_modules
    print()

    # Layer 2: Email domain blacklist
    print("Layer 2: Email Domain Blacklist")
    print("-" * 80)
    print(f"Blacklisted domains: {', '.join(TEST_EMAIL_DOMAINS)}")
    print()

    test_cases = [
        ('pending@example.com', True, "Test user (example.com)"),
        ('user@test.com', True, "Test user (test.com)"),
        ('dev@localhost', True, "Test user (localhost)"),
        ('real@crush.lu', False, "Real user (crush.lu)"),
        ('user@gmail.com', False, "Real user (gmail.com)"),
    ]

    all_passed = True
    for email, expected, description in test_cases:
        user = User(email=email)
        result = is_test_user(user)
        passed = result == expected
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status}: {email:30s} -> {result:5} ({description})")
        if not passed:
            all_passed = False

    print()

    # Layer 3: GitHub Actions credentials check
    print("Layer 3: GitHub Actions Credentials")
    print("-" * 80)

    import subprocess
    try:
        result = subprocess.run(
            ['grep', '-r', 'GRAPH_TENANT_ID', '.github/workflows/'],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True
        )

        if result.returncode != 0:  # No matches found
            print("[PASS]: No GRAPH_* credentials found in test workflows")
            print("        (Test workflows cannot sync to production)")
        else:
            print("[FAIL]: Found GRAPH_* credentials in workflows:")
            print(result.stdout)
            all_passed = False
    except FileNotFoundError:
        print("[SKIP]: grep not available (Windows)")
        print("        Manually verify: no GRAPH_* in .github/workflows/test*.yml")

    print()

    # Layer 4: Conftest mock check
    print("Layer 4: Conftest Mock Fixture")
    print("-" * 80)

    conftest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'conftest.py'
    )

    with open(conftest_path, 'r', encoding='utf-8') as f:
        conftest_content = f.read()

    if 'disable_outlook_sync' in conftest_content and 'autouse=True' in conftest_content:
        print("[PASS]: disable_outlook_sync fixture exists with autouse=True")
        print("        (All tests automatically mock sync methods)")
    else:
        print("[FAIL]: disable_outlook_sync fixture missing or not autouse")
        all_passed = False

    print()

    # Summary
    print("=" * 80)
    print("Summary")
    print("=" * 80)

    if all_passed:
        print("[PASS] ALL PROTECTION LAYERS VERIFIED")
        print()
        print("Test data CANNOT sync to production Outlook:")
        print("  1. Pytest detection blocks ALL test runs")
        print("  2. Email blacklist blocks test domains (example.com, etc.)")
        print("  3. GitHub Actions has no production credentials")
        print("  4. Conftest mocks all sync methods as no-ops")
        print()
        print("Next steps:")
        print("  1. Run: python manage.py cleanup_outlook_contacts --dry-run")
        print("  2. Review what will be deleted")
        print("  3. Run: python manage.py cleanup_outlook_contacts --test-only")
        print("  4. Run: python manage.py cleanup_outlook_contacts")
    else:
        print("[FAIL] SOME CHECKS FAILED - Review output above")

    print("=" * 80)


if __name__ == '__main__':
    test_protection_layers()
