"""Click + UTM tracking tests for multi-channel campaigns.

Covers tracked-URL building, HTML link rewriting for the email leg, the
/c/<token>/ redirect view, and that standalone (non-campaign) newsletters
stay untouched.

Run with: pytest crush_lu/tests/test_campaign_tracking.py -v
"""
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase

from crush_lu.models import (
    Campaign,
    CampaignClick,
    CampaignLink,
    CrushProfile,
    EmailPreference,
    UserDataConsent,
)
from crush_lu.models.newsletter import Newsletter
from crush_lu.newsletter_service import send_newsletter
from crush_lu.services.campaigns import (
    CHANNEL_ADAPTERS,
    build_tracked_url,
    click_signer,
    rewrite_html_links,
)

User = get_user_model()


def make_user(email):
    user = User.objects.create_user(
        username=email, email=email, password='x',
        first_name=email.split('@')[0].title(),
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth='1995-01-01',
        gender='M',
        location='Luxembourg',
        is_approved=True,
    )
    # Audiences require Crush.lu consent (#1184); the signal default is False.
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    # Newsletters need explicit opt-in (#1185, Option A).
    EmailPreference.objects.update_or_create(
        user=user,
        defaults={'email_marketing': True, 'email_newsletter': True},
    )
    return user


class BuildTrackedUrlTests(TestCase):
    def setUp(self):
        self.user = make_user('click@example.com')
        self.campaign = Campaign.objects.create(
            name='Spring Party', channels=['email'], audience='all_users',
        )

    def test_creates_link_with_utm_destination(self):
        tracked = build_tracked_url(
            'https://crush.lu/events/', self.campaign, 'email',
        )
        link = CampaignLink.objects.get(campaign=self.campaign)
        self.assertIn(f'/c/{link.token}/', tracked)
        dest = urlsplit(link.tracked_url)
        params = parse_qs(dest.query)
        self.assertEqual(params['utm_source'], ['crush.lu'])
        self.assertEqual(params['utm_medium'], ['email'])
        self.assertEqual(params['utm_campaign'], [self.campaign.slug])

    def test_existing_query_and_utm_params_are_preserved(self):
        build_tracked_url(
            'https://crush.lu/events/?ref=abc&utm_source=partner',
            self.campaign, 'email',
        )
        link = CampaignLink.objects.get(campaign=self.campaign)
        params = parse_qs(urlsplit(link.tracked_url).query)
        self.assertEqual(params['ref'], ['abc'])
        # An explicit utm_source in the destination wins over ours.
        self.assertEqual(params['utm_source'], ['partner'])
        self.assertEqual(params['utm_medium'], ['email'])

    def test_repeated_query_keys_are_preserved(self):
        build_tracked_url(
            'https://crush.lu/e/?category=a&category=b',
            self.campaign, 'email',
        )
        link = CampaignLink.objects.get(campaign=self.campaign)
        query = urlsplit(link.tracked_url).query
        self.assertIn('category=a', query)
        self.assertIn('category=b', query)

    def test_same_destination_reuses_link(self):
        build_tracked_url('https://crush.lu/a/', self.campaign, 'email')
        build_tracked_url('https://crush.lu/a/', self.campaign, 'email',
                          user=self.user)
        self.assertEqual(CampaignLink.objects.count(), 1)

    def test_user_attribution_is_signed_and_bound_to_link(self):
        tracked = build_tracked_url(
            'https://crush.lu/a/', self.campaign, 'email', user=self.user,
        )
        link = CampaignLink.objects.get()
        params = parse_qs(urlsplit(tracked).query)
        self.assertEqual(
            click_signer().unsign(params['r'][0]),
            f'{self.user.pk}:{link.token}',
        )


class RewriteHtmlLinksTests(TestCase):
    def setUp(self):
        self.user = make_user('rewrite@example.com')
        self.campaign = Campaign.objects.create(
            name='Rewrite', channels=['email'], audience='all_users',
        )

    def test_rewrites_absolute_links(self):
        html = '<a href="https://crush.lu/events/">Events</a>'
        result = rewrite_html_links(html, self.campaign, 'email', self.user)
        link = CampaignLink.objects.get()
        self.assertIn(f'/c/{link.token}/', result)
        self.assertNotIn('href="https://crush.lu/events/"', result)

    def test_unsubscribe_links_stay_direct(self):
        html = (
            '<a href="https://crush.lu/en/unsubscribe/abc-123/">Unsubscribe</a>'
        )
        result = rewrite_html_links(html, self.campaign, 'email', self.user)
        self.assertEqual(result, html)
        self.assertEqual(CampaignLink.objects.count(), 0)

    def test_mailto_and_anchors_untouched(self):
        html = '<a href="mailto:love@crush.lu">Mail</a><a href="#top">Top</a>'
        result = rewrite_html_links(html, self.campaign, 'email', self.user)
        self.assertEqual(result, html)

    def test_escaped_ampersands_survive_round_trip(self):
        html = '<a href="https://crush.lu/e/?a=1&amp;b=2">X</a>'
        rewrite_html_links(html, self.campaign, 'email', self.user)
        link = CampaignLink.objects.get()
        self.assertEqual(link.original_url, 'https://crush.lu/e/?a=1&b=2')


class ClickRedirectViewTests(TestCase):
    def setUp(self):
        self.user = make_user('redirect@example.com')
        self.campaign = Campaign.objects.create(
            name='Redirect', channels=['email'], audience='all_users',
        )
        self.tracked = build_tracked_url(
            'https://crush.lu/events/', self.campaign, 'email', self.user,
        )
        self.link = CampaignLink.objects.get()
        self.client = Client(HTTP_HOST='crush.lu')

    def _path(self, url):
        parts = urlsplit(url)
        return f"{parts.path}?{parts.query}" if parts.query else parts.path

    def test_redirects_to_utm_destination_and_records_user(self):
        response = self.client.get(self._path(self.tracked))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.link.tracked_url)
        click = CampaignClick.objects.get()
        self.assertEqual(click.user, self.user)
        self.assertEqual(click.link, self.link)

    def test_erased_or_unconsented_recipient_counts_anonymous_click(self):
        """An old signed link must not re-attribute clicks to an erased user."""
        from crush_lu.models import UserDataConsent

        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=False, crushlu_banned=True,
        )
        response = self.client.get(self._path(self.tracked))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(CampaignClick.objects.get().user)

    def test_tampered_signature_counts_anonymous_click(self):
        response = self.client.get(
            f'/c/{self.link.token}/?r={self.user.pk}:forged-signature'
        )
        self.assertEqual(response.status_code, 302)
        click = CampaignClick.objects.get()
        self.assertIsNone(click.user)

    def test_missing_recipient_param_counts_anonymous_click(self):
        response = self.client.get(f'/c/{self.link.token}/')
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(CampaignClick.objects.get().user)

    def test_signature_from_other_link_counts_anonymous(self):
        """A valid ?r= lifted onto another campaign's URL must not attribute."""
        other_campaign = Campaign.objects.create(
            name='Other', channels=['email'], audience='all_users',
        )
        other_tracked = build_tracked_url(
            'https://crush.lu/other/', other_campaign, 'email', self.user,
        )
        stolen_r = parse_qs(urlsplit(other_tracked).query)['r'][0]
        response = self.client.get(f'/c/{self.link.token}/?r={stolen_r}')
        self.assertEqual(response.status_code, 302)
        click = CampaignClick.objects.get(link=self.link)
        self.assertIsNone(click.user)

    def test_unknown_token_404s(self):
        response = self.client.get('/c/does-not-exist/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(CampaignClick.objects.count(), 0)

    def test_clicks_feed_campaign_stats(self):
        self.client.get(self._path(self.tracked))
        self.client.get(self._path(self.tracked))
        self.client.get(f'/c/{self.link.token}/')  # anonymous
        stats = self.campaign.stats
        self.assertEqual(stats['clicks']['total'], 3)
        self.assertEqual(stats['clicks']['unique_users'], 1)


class EmailLegRewritingTests(TestCase):
    def setUp(self):
        self.user = make_user('leg@example.com')

    def test_campaign_email_bodies_get_tracked_links(self):
        from crush_lu.services.campaigns import create_campaign

        campaign = create_campaign(
            name='Tracked email',
            channels=['email'],
            audience='all_users',
            email_content={
                'subject': 'Hi',
                'body_html': '<a href="https://crush.lu/events/">Come!</a>',
                'body_text': 'Come directly: https://crush.lu/events/',
            },
        )
        result = CHANNEL_ADAPTERS['email'].send_batch(campaign, limit=10)
        self.assertEqual(result.sent, 1)
        # Every absolute link in the rendered template is tracked (body CTA
        # plus the template's own nav/footer links).
        link = CampaignLink.objects.get(
            campaign=campaign, original_url='https://crush.lu/events/',
        )
        html_body = mail.outbox[0].alternatives[0].content
        self.assertIn(f'/c/{link.token}/', html_body)
        self.assertNotIn('href="https://crush.lu/events/"', html_body)
        self.assertIn('https://crush.lu/events/', mail.outbox[0].body)
        self.assertNotIn('/c/', mail.outbox[0].body)
        # The unsubscribe link stays direct.
        self.assertIn('unsubscribe', html_body)
        self.assertFalse(
            CampaignLink.objects.filter(
                original_url__contains='unsubscribe',
            ).exists()
        )

    def test_standalone_newsletter_untouched(self):
        newsletter = Newsletter.objects.create(
            subject='Solo',
            body_html='<a href="https://crush.lu/events/">Come!</a>',
            audience='all_users',
        )
        send_newsletter(newsletter)
        self.assertEqual(CampaignLink.objects.count(), 0)
        html_body = mail.outbox[0].alternatives[0].content
        self.assertIn('href="https://crush.lu/events/"', html_body)
        self.assertNotIn('/c/', html_body)


class ClickSweepOnDeletionTests(TestCase):
    def test_deletion_clears_attribution_of_late_clicks(self):
        from unittest.mock import patch

        from crush_lu.views import delete_crushlu_profile_only

        user = make_user('late@example.com')
        campaign = Campaign.objects.create(
            name='Late', channels=['email'], audience='all_users',
        )
        build_tracked_url('https://crush.lu/x/', campaign, 'email', user)
        link = CampaignLink.objects.get()
        # A click attributed in the redirect's check-to-insert window.
        CampaignClick.objects.create(link=link, user=user)
        with patch('crush_lu.storage.delete_user_storage', return_value=(True, 0)):
            delete_crushlu_profile_only(user)
        self.assertIsNone(CampaignClick.objects.get().user)


class LockedClickAttributionTests(TestCase):
    def test_revocation_between_check_and_insert_degrades_to_anonymous(self):
        from unittest.mock import patch

        from crush_lu import views_campaign_click

        user = make_user('race@example.com')
        UserDataConsent.objects.filter(user=user).update(
            crushlu_consent_given=True
        )
        campaign = Campaign.objects.create(
            name='Race', channels=['email'], audience='all_users',
        )
        tracked = build_tracked_url('https://crush.lu/x/', campaign, 'email', user)
        link = CampaignLink.objects.get()
        real_locked = views_campaign_click.locked_consent_holds

        def revoke_then_check(member):
            # Deletion lands after the unlocked check, before the insert.
            UserDataConsent.objects.filter(user=member).update(
                crushlu_consent_given=False, crushlu_banned=True
            )
            return real_locked(member)

        parts = urlsplit(tracked)
        with patch.object(
            views_campaign_click, 'locked_consent_holds', side_effect=revoke_then_check
        ):
            response = Client(HTTP_HOST='crush.lu').get(
                f"{parts.path}?{parts.query}"
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CampaignClick.objects.get(link=link).user, None)
