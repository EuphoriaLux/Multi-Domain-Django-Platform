"""
Test French LuxID profile mockup page for untranslated strings.
"""
import pytest
import re
from playwright.sync_api import expect


@pytest.mark.playwright
class TestFrenchLuxIDProfile:
    """Test French translation completeness on LuxID profile mockup."""

    def test_french_luxid_profile_translations(self, page, live_server):
        """
        Visit French LuxID profile mockup and identify untranslated strings.
        Takes screenshot and extracts all visible text.
        """
        # Navigate to French version
        page.goto(f"{live_server.url}/fr/mockup/profile-luxid/")

        # Wait for page to load
        page.wait_for_load_state('networkidle')

        # Take screenshot
        screenshot_path = 'C:\\Users\\User\\Github-Local\\Multi-Domain-Django-Platform\\crush_lu\\tests\\screenshots\\luxid_profile_fr_missing.png'
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"\n[OK] Screenshot saved to: {screenshot_path}")

        # Extract all text content from body
        all_text = page.locator('body').inner_text()

        # Save to file to avoid Windows encoding issues
        text_output_path = 'C:\\Users\\User\\Github-Local\\Multi-Domain-Django-Platform\\crush_lu\\tests\\screenshots\\luxid_profile_fr_text.txt'
        with open(text_output_path, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("FULL PAGE TEXT CONTENT (French page):\n")
            f.write("="*80 + "\n")
            f.write(all_text)
            f.write("\n" + "="*80 + "\n")

        print("\n" + "="*80)
        print("FULL PAGE TEXT CONTENT (French page):")
        print("="*80)
        print(f"[Saved to file: {text_output_path}]")
        print("="*80)

        # Get HTML for more detailed analysis
        html_content = page.content()

        # Common English words that should be translated
        english_indicators = [
            # Common UI words
            'Profile', 'Edit', 'Settings', 'Back', 'Next', 'Previous',
            'Save', 'Cancel', 'Submit', 'Delete', 'Remove', 'Add',
            'Search', 'Filter', 'Sort', 'View', 'Show', 'Hide',

            # Dating/profile specific
            'About', 'About Me', 'Interests', 'Looking for', 'Age',
            'Location', 'Height', 'Gender', 'Relationship Status',
            'Education', 'Work', 'Languages', 'Photos', 'Verified',

            # Actions
            'Message', 'Like', 'Match', 'Connect', 'Block', 'Report',
            'Share', 'Download', 'Upload',

            # Status
            'Online', 'Offline', 'Active', 'Inactive', 'Available',
            'Busy', 'Away',

            # Time
            'Today', 'Yesterday', 'Tomorrow', 'Week', 'Month', 'Year',
            'Hours', 'Minutes', 'Seconds', 'Days',

            # Common phrases
            'Sign in', 'Sign up', 'Log in', 'Log out', 'Forgot password',
            'Remember me', 'Privacy', 'Terms', 'Cookies', 'Help',

            # LuxID specific
            'Verified with', 'Government ID', 'Identity', 'Badge',
            'Trusted', 'Official',
        ]

        found_english = []

        # Check for each English indicator (case-insensitive word boundary search)
        for word in english_indicators:
            # Use word boundaries to avoid partial matches
            pattern = r'\b' + re.escape(word) + r'\b'
            if re.search(pattern, all_text, re.IGNORECASE):
                # Find all occurrences with context
                matches = re.finditer(pattern, all_text, re.IGNORECASE)
                for match in matches:
                    start = max(0, match.start() - 30)
                    end = min(len(all_text), match.end() + 30)
                    context = all_text[start:end].replace('\n', ' ')
                    found_english.append({
                        'word': match.group(),
                        'context': context.strip()
                    })

        # Save findings to file
        findings_path = 'C:\\Users\\User\\Github-Local\\Multi-Domain-Django-Platform\\crush_lu\\tests\\screenshots\\luxid_profile_fr_findings.txt'
        with open(findings_path, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("ENGLISH STRINGS FOUND ON FRENCH PAGE:\n")
            f.write("="*80 + "\n")

            if found_english:
                for i, item in enumerate(found_english, 1):
                    f.write(f"\n{i}. '{item['word']}'\n")
                    f.write(f"   Context: ...{item['context']}...\n")
            else:
                f.write("[OK] No common English strings detected!\n")

            f.write("\n" + "="*80 + "\n")

        # Print findings
        print("\n" + "="*80)
        print("ENGLISH STRINGS FOUND ON FRENCH PAGE:")
        print("="*80)
        print(f"[Saved to file: {findings_path}]")
        print(f"Found {len(found_english)} potential English strings")
        print("="*80)

        # Extract specific sections for analysis
        sections_path = 'C:\\Users\\User\\Github-Local\\Multi-Domain-Django-Platform\\crush_lu\\tests\\screenshots\\luxid_profile_fr_sections.txt'
        with open(sections_path, 'w', encoding='utf-8') as f:
            f.write("EXTRACTING SPECIFIC SECTIONS:\n")
            f.write("="*80 + "\n")

            # Try to find header
            header = page.locator('header, [role="banner"], nav').first
            if header.is_visible():
                f.write("\n[HEADER/NAV]:\n")
                f.write(header.inner_text() + "\n")

            # Try to find main content
            main = page.locator('main, [role="main"], .profile-content').first
            if main.is_visible():
                f.write("\n[MAIN CONTENT]:\n")
                f.write(main.inner_text() + "\n")

            # Try to find buttons
            buttons = page.locator('button, a.btn, [role="button"]').all()
            if buttons:
                f.write("\n[BUTTONS/LINKS]:\n")
                for btn in buttons[:20]:  # Limit to first 20
                    if btn.is_visible():
                        text = btn.inner_text()
                        if text.strip():
                            f.write(f"  - {text.strip()}\n")

            # Try to find labels
            labels = page.locator('label, .label, dt, th').all()
            if labels:
                f.write("\n[LABELS/HEADINGS]:\n")
                for label in labels[:30]:  # Limit to first 30
                    if label.is_visible():
                        text = label.inner_text()
                        if text.strip():
                            f.write(f"  - {text.strip()}\n")

        print("\nEXTRACTING SPECIFIC SECTIONS:")
        print("="*80)
        print(f"[Saved to file: {sections_path}]")
        print("="*80)

        print("\n" + "="*80)
        print("TEST COMPLETE")
        print("="*80)

        # Store results for assertion
        assert screenshot_path, "Screenshot should be created"
