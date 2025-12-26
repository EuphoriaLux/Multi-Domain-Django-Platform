"""
Tailwind Migration Verification Tests

These tests verify that Bootstrap classes and attributes have been removed
from templates and that Tailwind/Alpine.js/HTMX are properly integrated.

Run with: python manage.py test crush_lu.tests.test_tailwind_migration
Or with pytest: pytest crush_lu/tests/test_tailwind_migration.py -v
"""
import re
from pathlib import Path
from django.test import TestCase


class TailwindMigrationTests(TestCase):
    """Verify Bootstrap classes have been removed from templates."""

    TEMPLATE_DIR = Path('crush_lu/templates/crush_lu')

    # Bootstrap patterns that should be removed
    BOOTSTRAP_PATTERNS = [
        (r'\bdata-bs-[a-z]+', 'Bootstrap data attributes'),
        (r'\bbtn-primary\b(?!.*class=["\'][^"\']*btn-crush)', 'Bootstrap btn-primary (use btn-crush-primary)'),
        (r'\bbtn-secondary\b(?!.*class=["\'][^"\']*btn-crush)', 'Bootstrap btn-secondary'),
        (r'\bform-control\b', 'Bootstrap form-control (use Tailwind form classes)'),
        (r'\bform-check\b', 'Bootstrap form-check'),
        (r'\balert-dismissible\b', 'Bootstrap alert-dismissible (use Alpine.js)'),
        (r'\bmodal-dialog\b', 'Bootstrap modal (use Alpine.js modal)'),
        (r'\bfade\s+show\b', 'Bootstrap fade show classes'),
    ]

    # Bootstrap utility classes that should be replaced with Tailwind
    BOOTSTRAP_UTILITY_PATTERNS = [
        (r'\bd-none\b', 'd-none (use hidden)'),
        (r'\bd-block\b', 'd-block (use block)'),
        (r'\bd-flex\b', 'd-flex (use flex)'),
        (r'\bd-grid\b', 'd-grid (use grid)'),
        (r'\bflex-column\b', 'flex-column (use flex-col)'),
        (r'\bflex-row\b', 'flex-row (use flex-row)'),
        (r'\bjustify-content-', 'justify-content-* (use justify-*)'),
        (r'\balign-items-', 'align-items-* (use items-*)'),
        (r'\btext-center\b(?=[^"\']*(?:class|className))', 'text-center in class attr'),
        (r'\bmt-\d\b', 'Bootstrap mt-* (use Tailwind mt-*)'),
        (r'\bmb-\d\b', 'Bootstrap mb-* (use Tailwind mb-*)'),
        (r'\bpy-\d\b', 'Bootstrap py-* (use Tailwind py-*)'),
        (r'\bpx-\d\b', 'Bootstrap px-* (use Tailwind px-*)'),
    ]

    # Templates that are exempt from checking (still being migrated)
    # Remove from this list as each template is converted
    EXEMPT_TEMPLATES = [
        'create_profile.html',  # Phase 2.1 - new version created as create_profile_new.html
        # Coach dashboard templates - still have Bootstrap modals and tables
        'coach_edit_journey.html',
        'coach_invitation_dashboard.html',  # Has Bootstrap modals
        'coach_journey_dashboard.html',
        'coach_screening_dashboard.html',  # Has Bootstrap modals and tables
        # Event templates
        'event_activity_vote.html',
        'event_activity_vote_old.html',
        'voting_demo.html',
        # Misc
        'oauth_popup_error.html',  # Standalone with inline CSS, doesn't need conversion
        'qr_scanner.html',  # In advent/ subdirectory
        'verify_phone.html',  # Has form-control in intl-tel-input CSS override
        # Partials (in partials/ subdirectory)
        'edit_profile_form.html',  # partials/edit_profile_form.html - has Bootstrap forms
        # Advent calendar templates (in advent/ subdirectory)
        'door_default.html',
        'door_gift.html',
        'door_photo.html',
        'door_poem.html',
        'door_audio.html',
        'door_challenge.html',
        'door_countdown.html',
        'door_memory.html',
        'door_quiz.html',
        'door_video.html',
    ]

    # Templates that may contain inline styles or dynamic classes
    PARTIALLY_EXEMPT = [
        'base.html',  # Contains Bootstrap fallback until migration complete
    ]

    def get_all_templates(self):
        """Get all HTML template files in the crush_lu templates directory."""
        if not self.TEMPLATE_DIR.exists():
            self.fail(f"Template directory not found: {self.TEMPLATE_DIR}")
        return list(self.TEMPLATE_DIR.rglob('*.html'))

    def test_no_bootstrap_data_attributes(self):
        """Verify no Bootstrap data-bs-* attributes in templates."""
        violations = []
        pattern = r'data-bs-[a-z]+'

        for template_path in self.get_all_templates():
            if template_path.name in self.EXEMPT_TEMPLATES:
                continue
            if template_path.name in self.PARTIALLY_EXEMPT:
                continue

            content = template_path.read_text(encoding='utf-8')
            matches = re.findall(pattern, content)
            if matches:
                violations.append(
                    f"{template_path.name}: data-bs-* attributes ({set(matches)})"
                )

        self.assertEqual(
            violations, [],
            f"Bootstrap data attributes found - replace with Alpine.js:\n" +
            "\n".join(violations)
        )

    def test_no_bootstrap_component_classes(self):
        """Verify Bootstrap component classes have been replaced."""
        violations = []

        for template_path in self.get_all_templates():
            if template_path.name in self.EXEMPT_TEMPLATES:
                continue
            if template_path.name in self.PARTIALLY_EXEMPT:
                continue

            content = template_path.read_text(encoding='utf-8')

            for pattern, description in self.BOOTSTRAP_PATTERNS:
                matches = re.findall(pattern, content)
                if matches:
                    violations.append(
                        f"{template_path.name}: {description}"
                    )

        self.assertEqual(
            violations, [],
            f"Bootstrap component classes found:\n" + "\n".join(violations)
        )

    def test_alpine_js_loaded_in_base(self):
        """Verify Alpine.js is loaded in base template."""
        base_path = self.TEMPLATE_DIR / 'base.html'
        if not base_path.exists():
            self.skipTest("base.html not found")

        content = base_path.read_text(encoding='utf-8')

        # Check for Alpine.js script tag
        self.assertTrue(
            'alpinejs' in content.lower() or 'alpine' in content.lower(),
            "Alpine.js script not found in base.html"
        )

    def test_htmx_loaded_in_base(self):
        """Verify HTMX is loaded in base template."""
        base_path = self.TEMPLATE_DIR / 'base.html'
        if not base_path.exists():
            self.skipTest("base.html not found")

        content = base_path.read_text(encoding='utf-8')

        # Check for HTMX script tag
        self.assertTrue(
            'htmx' in content.lower(),
            "HTMX script not found in base.html"
        )

    def test_htmx_csrf_configured(self):
        """Verify HTMX CSRF token is configured in base template."""
        base_path = self.TEMPLATE_DIR / 'base.html'
        if not base_path.exists():
            self.skipTest("base.html not found")

        content = base_path.read_text(encoding='utf-8')

        # Check for HTMX CSRF configuration
        has_csrf_config = (
            'hx-headers' in content or
            'htmx:configRequest' in content or
            'X-CSRFToken' in content
        )

        self.assertTrue(
            has_csrf_config,
            "HTMX CSRF configuration not found in base.html"
        )

    def test_tailwind_css_loaded(self):
        """Verify Tailwind CSS is loaded in base template."""
        base_path = self.TEMPLATE_DIR / 'base.html'
        if not base_path.exists():
            self.skipTest("base.html not found")

        content = base_path.read_text(encoding='utf-8')

        # Check for Tailwind CSS file
        self.assertTrue(
            'tailwind' in content.lower(),
            "Tailwind CSS not found in base.html"
        )

    def test_htmx_partial_templates_exist(self):
        """Verify HTMX partial templates follow naming convention."""
        partials = list(self.TEMPLATE_DIR.glob('_*.html'))
        partials_dir = self.TEMPLATE_DIR / 'partials'

        # Count partials in main directory and partials subdirectory
        if partials_dir.exists():
            partials.extend(partials_dir.glob('*.html'))

        self.assertGreater(
            len(partials), 0,
            "No HTMX partial templates found (files starting with _ or in partials/)"
        )

    def test_alpine_directives_used(self):
        """Verify Alpine.js directives are used in templates."""
        alpine_directives = ['x-data', 'x-show', 'x-on', '@click', 'x-transition']
        found_directives = set()

        for template_path in self.get_all_templates():
            content = template_path.read_text(encoding='utf-8')

            for directive in alpine_directives:
                if directive in content:
                    found_directives.add(directive)

        self.assertGreater(
            len(found_directives), 2,
            f"Too few Alpine.js directives found. Only found: {found_directives}. "
            "Expected at least x-data, x-show, and event handlers."
        )

    def test_htmx_attributes_used(self):
        """Verify HTMX attributes are used in templates."""
        htmx_attributes = ['hx-get', 'hx-post', 'hx-target', 'hx-swap', 'hx-trigger']
        found_attributes = set()

        for template_path in self.get_all_templates():
            content = template_path.read_text(encoding='utf-8')

            for attr in htmx_attributes:
                if attr in content:
                    found_attributes.add(attr)

        self.assertGreater(
            len(found_attributes), 2,
            f"Too few HTMX attributes found. Only found: {found_attributes}. "
            "Expected at least hx-post, hx-target, and hx-swap."
        )


class TailwindClassCoverageTests(TestCase):
    """Test that Tailwind utility classes are being used."""

    TEMPLATE_DIR = Path('crush_lu/templates/crush_lu')

    # Common Tailwind patterns that should be present
    TAILWIND_PATTERNS = [
        (r'\bflex\b', 'Flexbox'),
        (r'\bgrid\b', 'CSS Grid'),
        (r'\bhidden\b', 'Hidden utility'),
        (r'\btext-(?:sm|base|lg|xl|2xl)', 'Text sizing'),
        (r'\bfont-(?:normal|medium|semibold|bold)', 'Font weight'),
        (r'\bbg-(?:white|gray|purple|pink)', 'Background colors'),
        (r'\brounded(?:-\w+)?', 'Border radius'),
        (r'\bshadow(?:-\w+)?', 'Box shadow'),
        (r'\bp-\d+|px-\d+|py-\d+', 'Padding utilities'),
        (r'\bm-\d+|mx-\d+|my-\d+', 'Margin utilities'),
        (r'\bw-(?:full|auto|\d+)', 'Width utilities'),
        (r'\bmax-w-', 'Max-width utilities'),
    ]

    def test_tailwind_utilities_used(self):
        """Verify Tailwind utility classes are used across templates."""
        found_patterns = set()

        for template_path in self.TEMPLATE_DIR.rglob('*.html'):
            content = template_path.read_text(encoding='utf-8')

            for pattern, name in self.TAILWIND_PATTERNS:
                if re.search(pattern, content):
                    found_patterns.add(name)

        # Should find at least 8 different Tailwind pattern categories
        self.assertGreaterEqual(
            len(found_patterns), 8,
            f"Tailwind adoption seems low. Found only: {found_patterns}"
        )

    def test_responsive_classes_used(self):
        """Verify responsive prefixes are being used."""
        responsive_patterns = [
            r'\bsm:',
            r'\bmd:',
            r'\blg:',
            r'\bxl:',
        ]

        found_responsive = False

        for template_path in self.TEMPLATE_DIR.rglob('*.html'):
            content = template_path.read_text(encoding='utf-8')

            for pattern in responsive_patterns:
                if re.search(pattern, content):
                    found_responsive = True
                    break

            if found_responsive:
                break

        self.assertTrue(
            found_responsive,
            "No responsive Tailwind classes (sm:, md:, lg:, xl:) found in templates"
        )


class GridMigrationTests(TestCase):
    """Test that Bootstrap grid classes have been converted to Tailwind."""

    TEMPLATE_DIR = Path('crush_lu/templates/crush_lu')

    # Bootstrap grid patterns that should be replaced with Tailwind
    BOOTSTRAP_GRID_PATTERNS = [
        (r'\brow\b(?=.*class)', 'Bootstrap .row (use grid or flex)'),
        (r'\bcol-lg-\d+\b', 'Bootstrap .col-lg-* (use grid or w-*)'),
        (r'\bcol-md-\d+\b', 'Bootstrap .col-md-* (use md:grid-cols-* or md:w-*)'),
        (r'\bcol-sm-\d+\b', 'Bootstrap .col-sm-* (use sm:grid-cols-* or sm:w-*)'),
        (r'\bcol-\d+\b', 'Bootstrap .col-* (use grid-cols-* or w-*)'),
    ]

    # Templates already migrated from Bootstrap grid
    MIGRATED_TEMPLATES = [
        'coach_presentation_control.html',
        'event_voting_lobby.html',
        'event_voting_results.html',
        'voting_demo.html',
        'my_presentation_scores.html',
    ]

    # Templates still exempt from grid checks
    EXEMPT_TEMPLATES = [
        'partials/edit_profile_form.html',  # Pending migration
        'coach_edit_journey.html',
        'coach_screening_dashboard.html',
        'coach_invitation_dashboard.html',
    ]

    def test_no_bootstrap_grid_in_migrated_templates(self):
        """Verify migrated templates don't have Bootstrap grid classes."""
        violations = []

        for template_name in self.MIGRATED_TEMPLATES:
            template_path = self.TEMPLATE_DIR / template_name
            if not template_path.exists():
                continue

            content = template_path.read_text(encoding='utf-8')

            for pattern, description in self.BOOTSTRAP_GRID_PATTERNS:
                matches = re.findall(pattern, content)
                if matches:
                    violations.append(
                        f"{template_name}: {description} ({len(matches)} occurrences)"
                    )

        self.assertEqual(
            violations, [],
            f"Bootstrap grid classes found in migrated templates:\n" +
            "\n".join(violations)
        )

    def test_tailwind_grid_used_in_migrated(self):
        """Verify Tailwind grid classes are used in migrated templates."""
        tailwind_grid_patterns = [
            r'\bgrid\b',
            r'\bgrid-cols-',
            r'\bgap-\d+',
            r'\bmd:grid-cols-',
        ]

        for template_name in self.MIGRATED_TEMPLATES:
            template_path = self.TEMPLATE_DIR / template_name
            if not template_path.exists():
                continue

            content = template_path.read_text(encoding='utf-8')

            found_grid = any(
                re.search(pattern, content)
                for pattern in tailwind_grid_patterns
            )

            self.assertTrue(
                found_grid,
                f"{template_name}: No Tailwind grid classes found"
            )


class InlineStyleExtractionTests(TestCase):
    """Test that inline styles have been extracted to CSS files."""

    TEMPLATE_DIR = Path('crush_lu/templates/crush_lu')
    CSS_DIR = Path('static/crush_lu/css')

    # Templates that should NOT have inline <style> blocks
    STYLE_FREE_TEMPLATES = [
        'event_voting_lobby.html',
        'event_voting_results.html',
        'voting_demo.html',
        'my_presentation_scores.html',
        'coach_presentation_control.html',
    ]

    # CSS files that should exist for page-specific styles
    EXPECTED_CSS_FILES = [
        'pages/voting.css',
        'pages/scoring.css',
        'pages/coach-presentation.css',
    ]

    def test_no_inline_styles_in_migrated_templates(self):
        """Verify migrated templates don't have inline <style> blocks."""
        violations = []

        for template_name in self.STYLE_FREE_TEMPLATES:
            template_path = self.TEMPLATE_DIR / template_name
            if not template_path.exists():
                continue

            content = template_path.read_text(encoding='utf-8')

            # Check for <style> blocks (excluding empty/comment blocks)
            style_matches = re.findall(
                r'<style[^>]*>(?!\s*{#|\s*$)(.+?)</style>',
                content,
                re.DOTALL | re.IGNORECASE
            )

            # Filter out CSS file references (comments indicating where styles moved)
            real_styles = [
                m for m in style_matches
                if m.strip() and not m.strip().startswith('{#')
            ]

            if real_styles:
                violations.append(
                    f"{template_name}: Contains inline <style> blocks"
                )

        self.assertEqual(
            violations, [],
            f"Inline styles found in migrated templates:\n" +
            "\n".join(violations)
        )

    def test_page_css_files_exist(self):
        """Verify expected page-specific CSS files exist."""
        missing_files = []

        for css_file in self.EXPECTED_CSS_FILES:
            css_path = self.CSS_DIR / css_file
            if not css_path.exists():
                missing_files.append(css_file)

        self.assertEqual(
            missing_files, [],
            f"Missing CSS files:\n" + "\n".join(missing_files)
        )

    def test_css_imported_in_modular(self):
        """Verify page CSS files are imported in crush-modular.css."""
        modular_path = self.CSS_DIR / 'crush-modular.css'
        if not modular_path.exists():
            self.skipTest("crush-modular.css not found")

        content = modular_path.read_text(encoding='utf-8')

        for css_file in self.EXPECTED_CSS_FILES:
            css_name = css_file.split('/')[-1]
            self.assertIn(
                css_name,
                content,
                f"CSS file {css_file} not imported in crush-modular.css"
            )
