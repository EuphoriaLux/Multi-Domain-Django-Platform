"""UX Wave 4 · WP4: accessible event polls and the timeline-sort challenge.

Findings 7-08 (poll options as a native radio/checkbox fieldset, labelled
result bars, canonical submit), 7-09 (fetch voting: JSON with the rendered
results partial, a no-JS form fallback, translated errors, the "Voting opens"
state) and 7-16 (timeline move up/down buttons and a polite announcer).
The JS behaviour is covered in test_ux_wave4_polls_timeline_playwright.py.
"""

import json
from datetime import date, timedelta
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone, translation

from crush_lu.models import (
    ChapterProgress,
    CrushProfile,
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
    JourneyProgress,
    SpecialUserExperience,
    UserDataConsent,
)
from crush_lu.models.event_polls import EventPoll, EventPollOption, EventPollVote

HOST = "crush.lu"


class _Tags(HTMLParser):
    """Collect (tag, attrs, ancestors) for every start tag."""

    VOID = {"input", "img", "br", "hr", "meta", "link", "source", "path"}

    def __init__(self, html):
        super().__init__()
        self.tags = []
        self._stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs, list(self._stack)))
        if tag not in self.VOID:
            self._stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs), list(self._stack)))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def find(self, tag, **match):
        return [
            (attrs, ancestors)
            for t, attrs, ancestors in self.tags
            if t == tag and all(attrs.get(k) == v for k, v in match.items())
        ]


def make_member(username="poller@example.com"):
    user = get_user_model().objects.create_user(
        username=username, email=username, password="testpass123", first_name="Pia"
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg",
        is_approved=True,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def make_poll(multi=False, starts_in=None, results_early=False):
    now = timezone.now()
    start = now + starts_in if starts_in else now - timedelta(hours=1)
    poll = EventPoll.objects.create(
        title="Which night next?",
        start_date=start,
        end_date=now + timedelta(days=7),
        is_published=True,
        allow_multiple_choices=multi,
        show_results_before_close=results_early,
    )
    EventPollOption.objects.create(poll=poll, name="Wine Night", sort_order=1)
    EventPollOption.objects.create(poll=poll, name="Board Games", sort_order=2)
    return poll


class PollBallotMarkupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)

    def _ballot(self, poll):
        response = self.client.get(f"/en/polls/{poll.id}/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        return response, _Tags(response.content.decode())

    def test_single_choice_options_are_label_wrapped_radios_in_a_fieldset(self):
        poll = make_poll()
        response, tags = self._ballot(poll)
        inputs = tags.find("input", name="option_ids")
        self.assertEqual(len(inputs), 2)
        for attrs, ancestors in inputs:
            self.assertEqual(attrs["type"], "radio")
            names = [tag for tag, _attrs in ancestors]
            self.assertIn("label", names)
            self.assertIn("fieldset", names)
            self.assertIn("form", names)
        self.assertContains(
            response, '<legend class="sr-only">Which night next?</legend>', html=True
        )
        # No more click-only divs.
        self.assertNotContains(response, "handleOptionClick")

    def test_multi_choice_options_are_checkboxes(self):
        poll = make_poll(multi=True)
        _response, tags = self._ballot(poll)
        types = {attrs["type"] for attrs, _a in tags.find("input", name="option_ids")}
        self.assertEqual(types, {"checkbox"})

    def test_ballot_form_posts_to_vote_endpoint_with_canonical_submit(self):
        poll = make_poll()
        _response, tags = self._ballot(poll)
        forms = tags.find("form", action=f"/api/polls/{poll.id}/vote/")
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0][0]["method"], "post")
        buttons = tags.find("button", type="submit")
        classes = [attrs.get("class", "").split() for attrs, _a in buttons]
        self.assertIn(["btn-crush-primary", "btn-block"], classes)

    def test_header_vote_count_has_the_hook_the_ballot_updates(self):
        poll = make_poll()
        _response, tags = self._ballot(poll)
        counts = [
            attrs for attrs, _a in tags.find("span") if "data-poll-total-votes" in attrs
        ]
        self.assertEqual(len(counts), 1)

    def test_error_region_is_announced(self):
        poll = make_poll()
        _response, tags = self._ballot(poll)
        alerts = tags.find("p", role="alert")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0]["x-text"], "errorMessage")

    def test_upcoming_poll_says_when_voting_opens_and_disables_the_ballot(self):
        poll = make_poll(starts_in=timedelta(days=2), results_early=True)
        response, tags = self._ballot(poll)
        self.assertContains(response, "Voting opens ")
        self.assertContains(response, "Upcoming")
        fieldsets = [a for a, _anc in tags.find("fieldset") if "disabled" in a]
        self.assertEqual(len(fieldsets), 1)
        inputs = tags.find("input", name="option_ids")
        self.assertEqual(len(inputs), 2)
        for _attrs, ancestors in inputs:
            self.assertTrue(
                any(t == "fieldset" and "disabled" in a for t, a in ancestors)
            )
        self.assertNotContains(response, "btn-crush-primary btn-block")

    def test_upcoming_date_is_localized(self):
        poll = make_poll(starts_in=timedelta(days=2))
        response = self.client.get(f"/de/polls/{poll.id}/", HTTP_HOST=HOST)
        self.assertContains(response, "Die Abstimmung startet am")
        response = self.client.get(f"/fr/polls/{poll.id}/", HTTP_HOST=HOST)
        self.assertContains(response, "Le vote ouvre le")


class PollResultsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)
        self.poll = make_poll()
        self.wine, self.games = list(self.poll.options.all())

    def _vote_json(self, option_ids):
        return self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": option_ids}),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def test_vote_json_carries_the_rendered_results_partial(self):
        response = self._vote_json([self.wine.id])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        # The existing JSON contract is unchanged.
        self.assertEqual(data["total_votes"], 1)
        self.assertEqual(len(data["results"]), 2)
        html = data["results_html"]
        self.assertIn("Your vote has been recorded. Thank you!", html)
        bars = _Tags(html).find("div", role="img")
        labels = [attrs["aria-label"] for attrs, _a in bars]
        self.assertEqual(
            labels, ["Wine Night: 100%, 1 vote", "Board Games: 0%, 0 votes"]
        )
        self.assertIn("Your vote", html)

    def test_results_page_bars_have_text_labels(self):
        EventPollVote.objects.create(poll=self.poll, option=self.games, user=self.user)
        response = self.client.get(f"/en/polls/{self.poll.id}/", HTTP_HOST=HOST)
        labels = [
            attrs["aria-label"]
            for attrs, _a in _Tags(response.content.decode()).find("div", role="img")
        ]
        self.assertEqual(
            labels, ["Wine Night: 0%, 0 votes", "Board Games: 100%, 1 vote"]
        )

    def test_vote_errors_are_translated(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": []}),
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="de",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Keine Option ausgewählt")


class PollNoJsFallbackTests(TestCase):
    """The ballot is a real form: without JS it still votes."""

    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)
        self.poll = make_poll()
        self.wine = self.poll.options.get(name="Wine Night")

    def test_form_post_records_the_vote_and_returns_to_the_poll(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            {"option_ids": [str(self.wine.id)]},
            HTTP_HOST=HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(f"/polls/{self.poll.id}/"))
        self.assertTrue(
            EventPollVote.objects.filter(user=self.user, option=self.wine).exists()
        )

    def test_form_post_without_a_choice_redirects_with_an_error(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/", {}, HTTP_HOST=HOST, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No options selected")
        self.assertFalse(EventPollVote.objects.filter(user=self.user).exists())


class PollVoteLanguageTests(TestCase):
    """The vote URL is language-neutral: it answers in the ballot page's
    language (the posted ``lang``), not the browser's Accept-Language."""

    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)
        self.poll = make_poll()
        self.wine = self.poll.options.get(name="Wine Night")

    def test_ballot_carries_the_page_language(self):
        response = self.client.get(
            f"/de/polls/{self.poll.id}/", HTTP_HOST=HOST, HTTP_ACCEPT_LANGUAGE="en"
        )
        inputs = _Tags(response.content.decode()).find("input", name="lang")
        self.assertEqual([attrs["value"] for attrs, _a in inputs], ["de"])

    def test_json_vote_renders_results_in_the_page_language(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.wine.id], "lang": "de"}),
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.status_code, 200)
        html = response.json()["results_html"]
        self.assertIn("Deine Stimme wurde aufgezeichnet. Danke!", html)
        self.assertNotIn("Your vote has been recorded", html)

    def test_json_vote_error_uses_the_page_language(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [], "lang": "fr"}),
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Aucune option sélectionnée")

    def test_unknown_lang_falls_back_to_the_request_language(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [], "lang": "xx"}),
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="de",
        )
        self.assertEqual(response.json()["error"], "Keine Option ausgewählt")

    def test_non_string_lang_falls_back_to_the_request_language(self):
        # A list or object lang must not crash the vote (it once hit a set
        # lookup and raised "unhashable type").
        for bad_lang in (["fr"], {}):
            response = self.client.post(
                f"/api/polls/{self.poll.id}/vote/",
                data=json.dumps({"option_ids": [], "lang": bad_lang}),
                content_type="application/json",
                HTTP_HOST=HOST,
                HTTP_ACCEPT_LANGUAGE="de",
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], "Keine Option ausgewählt")

    def test_malformed_json_option_ids_are_rejected_not_a_500(self):
        for bad_ids in (5, "1", [[1]], ["1"], [True], {"a": 1}):
            response = self.client.post(
                f"/api/polls/{self.poll.id}/vote/",
                data=json.dumps({"option_ids": bad_ids}),
                content_type="application/json",
                HTTP_HOST=HOST,
            )
            self.assertEqual(response.status_code, 400, bad_ids)
            self.assertEqual(response.json()["error"], "Invalid option")
        self.assertFalse(EventPollVote.objects.exists())

    def test_non_string_gender_is_ignored(self):
        CrushProfile.objects.filter(user=self.user).update(gender="")
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.wine.id], "gender": []}),
            content_type="application/json",
            HTTP_HOST=HOST,
        )
        self.assertEqual(response.status_code, 200)
        vote = EventPollVote.objects.get(user=self.user)
        self.assertEqual(vote.voter_gender, "")

    def test_no_js_vote_returns_to_the_page_language(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            {"option_ids": [str(self.wine.id)], "lang": "de"},
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"/de/polls/{self.poll.id}/")
        page = self.client.get(response.url, HTTP_HOST=HOST, HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(page, "Deine Stimme wurde aufgezeichnet. Danke!")

    def test_no_js_error_flash_uses_the_page_language(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            {"lang": "fr"},
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
            follow=True,
        )
        self.assertEqual(response.redirect_chain[-1][0], f"/fr/polls/{self.poll.id}/")
        self.assertContains(response, "Aucune option sélectionnée")

    def test_rate_limited_no_js_vote_gets_a_page(self):
        for _i in range(10):
            self.client.post(
                f"/api/polls/{self.poll.id}/vote/",
                {"option_ids": [str(self.wine.id)]},
                HTTP_HOST=HOST,
            )
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            {"option_ids": [str(self.wine.id)]},
            HTTP_HOST=HOST,
        )
        self.assertIn("Retry-After", response.headers)
        self.assertContains(
            response,
            "temporarily paused this action.",
            status_code=429,
        )

    def _exhaust_rate_limit(self, lang):
        for _i in range(10):
            self.client.post(
                f"/api/polls/{self.poll.id}/vote/",
                {"option_ids": [str(self.wine.id)], "lang": lang},
                HTTP_HOST=HOST,
                HTTP_ACCEPT_LANGUAGE="en",
            )

    def test_rate_limited_no_js_vote_uses_the_ballot_language(self):
        self._exhaust_rate_limit("de")
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            {"option_ids": [str(self.wine.id)], "lang": "de"},
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.status_code, 429)
        with translation.override("de"):
            paused = translation.gettext(
                "For your security, we've temporarily paused this action."
            )
        self.assertNotEqual(
            paused, "For your security, we've temporarily paused this action."
        )
        self.assertContains(response, paused.replace("'", "&#x27;"), status_code=429)
        self.assertNotContains(
            response, "temporarily paused this action.", status_code=429
        )

    def test_rate_limited_json_vote_uses_the_ballot_language(self):
        self._exhaust_rate_limit("fr")
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.wine.id], "lang": "fr"}),
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertEqual(response.status_code, 429)
        with translation.override("fr"):
            expected = translation.gettext("Too many attempts. Please try again later.")
        self.assertNotEqual(expected, "Too many attempts. Please try again later.")
        self.assertEqual(response.json()["error"], expected)


class PollGenderSplitExplanationTests(TestCase):
    """The women/men explanation travels with the results partial, so the
    fetch swap after a vote renders it exactly like a full reload."""

    EXPLANATION = "Women/men shares are the percentage of all women"

    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)
        self.poll = make_poll()
        EventPoll.objects.filter(pk=self.poll.pk).update(is_public=True)
        self.poll.refresh_from_db()
        self.wine = self.poll.options.get(name="Wine Night")
        User = get_user_model()
        for gender in ("F", "M"):
            for i in range(5):
                voter = User.objects.create_user(
                    username=f"{gender}{i}@example.com",
                    email=f"{gender}{i}@example.com",
                    password="x",
                )
                EventPollVote.objects.create(
                    poll=self.poll,
                    option=self.wine,
                    user=voter,
                    voter_gender=gender,
                )

    def test_ballot_page_before_voting_has_no_explanation(self):
        response = self.client.get(f"/en/polls/{self.poll.id}/", HTTP_HOST=HOST)
        self.assertNotContains(response, self.EXPLANATION)

    def test_vote_json_results_html_carries_the_explanation(self):
        response = self.client.post(
            f"/api/polls/{self.poll.id}/vote/",
            data=json.dumps({"option_ids": [self.wine.id], "lang": "en"}),
            content_type="application/json",
            HTTP_HOST=HOST,
        )
        self.assertEqual(response.status_code, 200)
        html = response.json()["results_html"]
        self.assertIn("Women 100% · Men 100%", html)
        self.assertIn(self.EXPLANATION, html)
        self.assertIn("once a theme has 5 votes from women and 5 from men", html)

    def test_reload_after_voting_renders_the_explanation_once(self):
        EventPollVote.objects.create(
            poll=self.poll, option=self.wine, user=self.user, voter_gender="F"
        )
        response = self.client.get(f"/en/polls/{self.poll.id}/", HTTP_HOST=HOST)
        self.assertContains(response, "Women 100% · Men 100%")
        self.assertContains(response, self.EXPLANATION, count=1)


class UpcomingPollResultsTests(TestCase):
    """show_results_before_close shows results on an upcoming poll too."""

    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)

    def _results_bars(self, poll):
        response = self.client.get(f"/en/polls/{poll.id}/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Voting opens ")
        return _Tags(response.content.decode()).find("div", role="img")

    def test_upcoming_poll_with_early_results_renders_the_results(self):
        poll = make_poll(starts_in=timedelta(days=2), results_early=True)
        self.assertEqual(len(self._results_bars(poll)), 2)

    def test_upcoming_poll_without_early_results_hides_them(self):
        poll = make_poll(starts_in=timedelta(days=2), results_early=False)
        self.assertEqual(self._results_bars(poll), [])


class RateLimitCopyTranslationTests(TestCase):
    """The ballot's explicit 429 copy is compiled for DE and FR (not fuzzy)."""

    MSGID = "Too many attempts. Take a breath and try again in a minute."

    def test_rate_limit_copy_is_translated(self):
        for lang, expected in (
            (
                "de",
                "Zu viele Versuche. Atme kurz durch und versuch es in einer "
                "Minute noch mal.",
            ),
            (
                "fr",
                "Trop de tentatives. Prenez une pause et réessayez dans une " "minute.",
            ),
        ):
            with translation.override(lang):
                got = translation.gettext(self.MSGID)
            self.assertEqual(got, expected)


class TimelineMoveButtonsTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_user(
            username="timeline-wp4@example.com",
            email="timeline-wp4@example.com",
            password="testpass123",
            first_name="Tim",
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            is_approved=True,
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        experience = SpecialUserExperience.objects.create(
            first_name="Tim", last_name="Line", linked_user=user, is_active=True
        )
        journey = JourneyConfiguration.objects.create(
            special_experience=experience,
            journey_type="wonderland",
            journey_name="Tim's Journey",
            total_chapters=1,
            is_active=True,
        )
        chapter = JourneyChapter.objects.create(
            journey=journey,
            chapter_number=1,
            title="Chapter",
            theme="Mystery",
            story_introduction="Once upon a time",
            completion_message="Well done",
        )
        self.challenge = JourneyChallenge.objects.create(
            chapter=chapter,
            challenge_order=1,
            challenge_type="timeline_sort",
            question="Put these in order",
            options={"events": ["First", "Second", "Third"]},
            correct_answer="0,1,2",
            points_awarded=100,
        )
        progress = JourneyProgress.objects.create(
            user=user, journey=journey, current_chapter=1
        )
        ChapterProgress.objects.create(journey_progress=progress, chapter=chapter)
        self.client.force_login(user)

    def _page(self, lang="en"):
        response = self.client.get(
            f"/{lang}/journey/chapter/1/challenge/{self.challenge.id}/", HTTP_HOST=HOST
        )
        self.assertEqual(response.status_code, 200)
        return _Tags(response.content.decode())

    def test_each_item_has_named_move_up_and_down_buttons(self):
        tags = self._page()
        ups = tags.find("button", type="button", **{"@click": "moveUp"})
        downs = tags.find("button", type="button", **{"@click": "moveDown"})
        self.assertEqual(
            [a["aria-label"] for a, _anc in ups],
            ["Move “First” up", "Move “Second” up", "Move “Third” up"],
        )
        self.assertEqual(
            [a["aria-label"] for a, _anc in downs],
            ["Move “First” down", "Move “Second” down", "Move “Third” down"],
        )

    def test_page_has_a_polite_position_announcer(self):
        tags = self._page()
        live = tags.find("p", **{"aria-live": "polite"})
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0][0]["x-text"], "positionAnnouncement")
        article = tags.find("article", **{"x-data": "timelineSort"})[0][0]
        self.assertEqual(
            article["data-i18n-moved"],
            "{item} moved to position {position} of {total}",
        )

    def test_move_labels_and_announcer_are_translated(self):
        tags = self._page("de")
        ups = tags.find("button", **{"@click": "moveUp"})
        self.assertEqual(ups[0][0]["aria-label"], "„First“ nach oben verschieben")
        article = tags.find("article", **{"x-data": "timelineSort"})[0][0]
        self.assertIn("{position}", article["data-i18n-moved"])
        self.assertIn("Position", article["data-i18n-moved"])
