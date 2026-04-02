"""
Management command to validate timeline event structures across all journey challenges.

Usage:
    python manage.py validate_timeline_events
    python manage.py validate_timeline_events --fix-legacy  # Attempt to migrate legacy formats
"""
import json
from django.core.management.base import BaseCommand
from django.db import transaction
from crush_lu.models import JourneyChallenge
from crush_lu.utils.i18n import get_supported_language_codes


class Command(BaseCommand):
    help = "Validate timeline event structures and detect legacy formats"

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-legacy',
            action='store_true',
            help='Attempt to migrate legacy nested formats to clean structure'
        )
        parser.add_argument(
            '--journey-id',
            type=int,
            help='Only check challenges for specific journey ID'
        )

    def handle(self, *args, **options):
        fix_legacy = options['fix_legacy']
        journey_id = options.get('journey_id')

        # Get all timeline challenges
        challenges = JourneyChallenge.objects.filter(challenge_type='timeline_events')
        if journey_id:
            challenges = challenges.filter(chapter__journey_id=journey_id)

        total_challenges = challenges.count()
        self.stdout.write(f"\nValidating {total_challenges} timeline challenges...\n")

        issues_found = []
        legacy_found = []
        clean_found = []
        errors_found = []
        mixed_found = []

        for challenge in challenges:
            challenge_info = f"Challenge {challenge.id} (Journey: {challenge.chapter.journey.journey_name}, Chapter: {challenge.chapter.chapter_number})"

            supported_langs = get_supported_language_codes()
            challenge_issues = []

            for lang in supported_langs:
                options = getattr(challenge, f'options_{lang}', None)

                if not options:
                    challenge_issues.append(f"  [{lang}] No options found")
                    continue

                if not isinstance(options, dict):
                    challenge_issues.append(f"  [{lang}] Options is not a dict: {type(options)}")
                    errors_found.append((challenge_info, lang, "Invalid options type"))
                    continue

                # Check structure type
                has_clean = 'events' in options
                has_nested = any(k.startswith('events_') for k in options.keys())

                if has_clean and has_nested:
                    # Mixed structure
                    challenge_issues.append(
                        f"  [{lang}] MIXED STRUCTURE: Both 'events' and nested 'events_*' keys"
                    )
                    mixed_found.append((challenge_info, lang))
                elif has_nested:
                    # Legacy structure
                    nested_keys = [k for k in options.keys() if k.startswith('events_')]
                    challenge_issues.append(
                        f"  [{lang}] LEGACY nested structure: {nested_keys}"
                    )
                    legacy_found.append((challenge_info, lang, nested_keys))

                    # Attempt fix if requested
                    if fix_legacy and len(nested_keys) == 1:
                        self._migrate_to_clean(challenge, lang, options, nested_keys[0])
                elif has_clean:
                    # Clean structure
                    events = options['events']
                    if isinstance(events, list):
                        # Validate event objects
                        for idx, event in enumerate(events):
                            if not isinstance(event, dict):
                                challenge_issues.append(
                                    f"  [{lang}] Event {idx} is not a dict: {type(event)}"
                                )
                                errors_found.append((challenge_info, lang, f"Event {idx} invalid"))
                        clean_found.append((challenge_info, lang))
                    else:
                        challenge_issues.append(
                            f"  [{lang}] 'events' key is not a list: {type(events)}"
                        )
                        errors_found.append((challenge_info, lang, "Events not a list"))
                else:
                    # No events found
                    challenge_issues.append(f"  [{lang}] No 'events' or 'events_*' keys found")
                    errors_found.append((challenge_info, lang, "No events found"))

            if challenge_issues:
                issues_found.append((challenge_info, challenge_issues))

        # Print summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("\nVALIDATION SUMMARY\n"))
        self.stdout.write("=" * 80 + "\n")

        self.stdout.write(f"Total timeline challenges: {total_challenges}")
        self.stdout.write(f"Clean structures: {len(clean_found)}")
        self.stdout.write(f"Legacy structures: {len(legacy_found)}")
        self.stdout.write(f"Mixed structures: {len(mixed_found)}")
        self.stdout.write(f"Errors found: {len(errors_found)}\n")

        # Print details
        if clean_found:
            self.stdout.write(self.style.SUCCESS(f"\n✓ {len(clean_found)} Clean Structures Found"))
            if options.get('verbosity', 1) >= 2:
                for info, lang in clean_found:
                    self.stdout.write(f"  {info} [{lang}]")

        if legacy_found:
            self.stdout.write(self.style.WARNING(f"\n⚠ {len(legacy_found)} Legacy Structures Found"))
            for info, lang, keys in legacy_found:
                self.stdout.write(f"  {info} [{lang}] - Keys: {keys}")
            if fix_legacy:
                self.stdout.write(self.style.SUCCESS("\n  Attempted migration to clean structure"))

        if mixed_found:
            self.stdout.write(self.style.ERROR(f"\n✗ {len(mixed_found)} Mixed Structures Found"))
            for info, lang in mixed_found:
                self.stdout.write(f"  {info} [{lang}]")

        if errors_found:
            self.stdout.write(self.style.ERROR(f"\n✗ {len(errors_found)} Errors Found"))
            for info, lang, error in errors_found:
                self.stdout.write(f"  {info} [{lang}] - {error}")

        if issues_found:
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.WARNING("\nDETAILED ISSUES\n"))
            self.stdout.write("=" * 80 + "\n")
            for challenge_info, issues in issues_found:
                self.stdout.write(f"\n{challenge_info}:")
                for issue in issues:
                    self.stdout.write(issue)

        # Recommendations
        if legacy_found or mixed_found:
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.WARNING("\nRECOMMENDATIONS\n"))
            self.stdout.write("=" * 80 + "\n")
            if legacy_found:
                self.stdout.write(
                    "Consider migrating legacy nested structures to clean format:\n"
                    "  python manage.py validate_timeline_events --fix-legacy\n"
                )
            if mixed_found:
                self.stdout.write(
                    "Mixed structures need manual review. Remove either 'events' or nested 'events_*' keys.\n"
                )

        self.stdout.write("")

    def _migrate_to_clean(self, challenge, lang, options, nested_key):
        """
        Migrate legacy nested structure to clean structure.

        Converts:
            {'events_en': [...]} -> {'events': [...]}
        """
        try:
            with transaction.atomic():
                # Extract events from nested key
                events = options[nested_key]

                # Create new clean structure
                new_options = {'events': events}

                # Update the language-specific field
                setattr(challenge, f'options_{lang}', new_options)
                challenge.save(update_fields=[f'options_{lang}'])

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Migrated Challenge {challenge.id} [{lang}] from '{nested_key}' to clean structure"
                    )
                )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"  ✗ Failed to migrate Challenge {challenge.id} [{lang}]: {e}"
                )
            )
