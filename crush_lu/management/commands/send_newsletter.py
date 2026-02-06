"""
Send a Crush.lu newsletter to members.

Usage:
    # Send an existing draft newsletter
    python manage.py send_newsletter --newsletter-id 1

    # Create and send a new newsletter inline
    python manage.py send_newsletter --subject "New Event!" --body-file event_announce.html

    # Dry run (preview recipients without sending)
    python manage.py send_newsletter --newsletter-id 1 --dry-run

    # Limit number of emails
    python manage.py send_newsletter --newsletter-id 1 --limit 10

    # List available segments with user counts
    python manage.py send_newsletter --list-segments

    # Target a specific audience
    python manage.py send_newsletter --newsletter-id 1 --audience approved_profiles

    # Target a specific segment
    python manage.py send_newsletter --subject "Hey!" --body-file msg.html --audience segment --segment inactive_7d
"""
from django.core.management.base import BaseCommand, CommandError

from crush_lu.models.newsletter import Newsletter
from crush_lu.newsletter_service import get_newsletter_recipients, send_newsletter


class Command(BaseCommand):
    help = 'Send a Crush.lu newsletter to members'

    def add_arguments(self, parser):
        parser.add_argument(
            '--newsletter-id',
            type=int,
            help='Send an existing draft newsletter by PK',
        )
        parser.add_argument(
            '--subject',
            type=str,
            help='Subject line (creates a new newsletter)',
        )
        parser.add_argument(
            '--body-file',
            type=str,
            help='Path to HTML body file (creates a new newsletter)',
        )
        parser.add_argument(
            '--audience',
            type=str,
            choices=['all_users', 'all_profiles', 'approved_profiles', 'segment'],
            default='all_users',
            help='Audience for new newsletter (default: all_users)',
        )
        parser.add_argument(
            '--segment',
            type=str,
            default='',
            help='Segment key (when audience is "segment")',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview recipients without sending',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Maximum number of emails to send',
        )
        parser.add_argument(
            '--list-segments',
            action='store_true',
            help='Show available segments with user counts',
        )

    def handle(self, *args, **options):
        if options['list_segments']:
            return self._list_segments()

        newsletter = self._resolve_newsletter(options)

        results = send_newsletter(
            newsletter=newsletter,
            dry_run=options['dry_run'],
            limit=options['limit'],
            stdout=self.stdout,
        )

        self.counts = results

    def _resolve_newsletter(self, options):
        """Get or create the newsletter to send."""
        newsletter_id = options['newsletter_id']
        subject = options['subject']
        body_file = options['body_file']

        if newsletter_id:
            try:
                newsletter = Newsletter.objects.get(pk=newsletter_id)
            except Newsletter.DoesNotExist:
                raise CommandError(f"Newsletter #{newsletter_id} not found")

            if newsletter.status not in ('draft', 'sending'):
                raise CommandError(
                    f"Newsletter #{newsletter_id} has status '{newsletter.status}'. "
                    f"Only 'draft' or 'sending' newsletters can be sent."
                )
            return newsletter

        if subject and body_file:
            try:
                with open(body_file, 'r', encoding='utf-8') as f:
                    body_html = f.read()
            except FileNotFoundError:
                raise CommandError(f"Body file not found: {body_file}")

            newsletter = Newsletter.objects.create(
                subject=subject,
                body_html=body_html,
                audience=options['audience'],
                segment_key=options['segment'],
                status='draft',
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created newsletter #{newsletter.pk}")
            )
            return newsletter

        raise CommandError(
            "Provide either --newsletter-id or both --subject and --body-file"
        )

    def _list_segments(self):
        """Show available segments with user counts."""
        from crush_lu.admin.user_segments import get_segment_definitions

        segments = get_segment_definitions()

        self.stdout.write("\nAvailable segments:\n")
        for group_key, group in segments.items():
            try:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(
                        f"\n{group.get('icon', '')} {group['title']}"
                    )
                )
            except UnicodeEncodeError:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(f"\n{group['title']}")
                )
            for seg in group.get('segments', []):
                try:
                    self.stdout.write(
                        f"  {seg['key']:30s}  {seg['count']:>5d} users  "
                        f"- {seg['description']}"
                    )
                except UnicodeEncodeError:
                    self.stdout.write(
                        f"  {seg['key']:30s}  {seg['count']:>5d} users"
                    )
        self.stdout.write("")
