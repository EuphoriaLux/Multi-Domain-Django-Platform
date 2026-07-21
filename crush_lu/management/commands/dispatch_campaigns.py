"""
Run one bounded dispatch tick for multi-channel campaigns.

Invoked by the CampaignDispatch Azure Function timer (via the
``/api/admin/campaigns/dispatch/`` endpoint) every few minutes in production,
and runnable manually from a dev/SSH shell.

Usage:
    # One dispatch tick (promote due campaigns + send bounded batches)
    python manage.py dispatch_campaigns

    # Preview what a tick would target, without sending
    python manage.py dispatch_campaigns --dry-run

    # Focus on a single campaign
    python manage.py dispatch_campaigns --campaign-id 3

    # Override per-channel tick limits
    python manage.py dispatch_campaigns --limit-email 10 --limit-push 50
"""
from django.core.management.base import BaseCommand, CommandError

from crush_lu.models import Campaign
from crush_lu.services import campaigns as campaign_service


class Command(BaseCommand):
    help = 'Run one bounded dispatch tick for multi-channel campaigns'

    def add_arguments(self, parser):
        parser.add_argument(
            '--campaign-id',
            type=int,
            help='Dispatch only this campaign',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show per-channel eligible counts without sending',
        )
        parser.add_argument(
            '--limit-email', type=int, default=None,
            help='Max emails this tick (default: %d)'
                 % campaign_service.EMAIL_LIMIT_PER_TICK,
        )
        parser.add_argument(
            '--limit-whatsapp', type=int, default=None,
            help='Max WhatsApp sends this tick (default: %d)'
                 % campaign_service.WHATSAPP_LIMIT_PER_TICK,
        )
        parser.add_argument(
            '--limit-push', type=int, default=None,
            help='Max push sends this tick (default: %d)'
                 % campaign_service.PUSH_LIMIT_PER_TICK,
        )
        parser.add_argument(
            '--time-budget', type=int, default=None,
            help='Wall-clock budget in seconds (default: %d)'
                 % campaign_service.DISPATCH_TIME_BUDGET_SECONDS,
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            return self._dry_run(options)

        limits = {}
        for channel in Campaign.CHANNEL_KEYS:
            value = options.get(f'limit_{channel}')
            if value is not None:
                limits[channel] = value

        if options['campaign_id']:
            self._check_campaign(options['campaign_id'])

        summary = campaign_service.dispatch_campaigns(
            limits=limits or None,
            time_budget=options['time_budget'],
            stdout=self.stdout,
        )

        if options['campaign_id']:
            summary['campaigns'] = [
                c for c in summary['campaigns']
                if c['id'] == options['campaign_id']
            ]

        if not summary['campaigns'] and not summary['promoted']:
            self.stdout.write("No campaigns due for dispatch.")
        self.summary = summary

    def _check_campaign(self, campaign_id):
        try:
            campaign = Campaign.objects.get(pk=campaign_id)
        except Campaign.DoesNotExist:
            raise CommandError(f"Campaign #{campaign_id} not found")
        if campaign.status not in ('scheduled', 'sending'):
            raise CommandError(
                f"Campaign #{campaign_id} has status '{campaign.status}' — "
                f"only 'scheduled' or 'sending' campaigns dispatch."
            )

    def _dry_run(self, options):
        qs = Campaign.objects.filter(status__in=['scheduled', 'sending'])
        if options['campaign_id']:
            qs = qs.filter(pk=options['campaign_id'])
        if not qs.exists():
            self.stdout.write("No scheduled or sending campaigns.")
            return

        for campaign in qs:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\nCampaign #{campaign.pk}: {campaign.name} "
                f"[{campaign.status}]"
            ))
            for channel in campaign.channels:
                adapter = campaign_service.CHANNEL_ADAPTERS.get(channel)
                if adapter is None:
                    self.stdout.write(f"  {channel}: unknown channel")
                    continue
                count = adapter.eligible_users(campaign).count()
                self.stdout.write(f"  {channel}: {count} eligible recipients")
