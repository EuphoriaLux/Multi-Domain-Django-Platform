"""Freshness, event dates, provenance and concurrent selection contracts."""

import importlib.util
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "ideas", Path(__file__).with_name("idea-feed.py")
)
ideas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ideas)


class IdeaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ideas.DB = Path(temp.name) / "ideas.sqlite"
        ideas.initialize()
        clock = patch.object(ideas, "today", return_value=date(2026, 10, 1))
        clock.start()
        self.addCleanup(clock.stop)

    def fact(self, key="a", when=None, checked=None, region="Moselle"):
        fact = {
            "id": key,
            "title": "Local place",
            "source_url": "https://www.visitluxembourg.com/place/remich",
            "source_id": key,
            "source_type": "event" if when else "place",
            "region": region,
            "first_party": False,
            "event_date": when,
            "priority": 70,
            "data_period": None,
            "checked_at": checked or datetime.now(ideas.TZ).isoformat(),
        }
        with ideas.database() as db:
            db.execute(
                "INSERT OR REPLACE INTO facts VALUES (?,?)", (key, json.dumps(fact))
            )
        return fact

    def select(self, run="wf:1", posting="2026-10-01", **kwargs):
        return ideas.select(
            {"kind": "carousel", "run_key": run, "posting_date": posting, **kwargs}
        )

    def test_select_uses_posting_date_and_rejects_expired_event(self):
        self.fact(when="2026-10-10")
        self.assertIsNone(self.select(posting="2026-10-11")["idea"])
        self.assertIsNotNone(self.select()["idea"])
        self.assertIsNone(self.select(run="other", posting="2026-10-10")["idea"])

    def test_event_freshness_and_evergreen_fallback(self):
        old = (datetime.now(ideas.TZ) - timedelta(days=3)).isoformat()
        self.fact(when="2026-10-10", checked=old)
        self.assertEqual(self.select()["status"], "evergreen_fallback")
        self.fact("evergreen", checked=old)
        self.assertEqual(self.select()["idea"]["fact_id"], "evergreen")

    def test_invalid_calendar_dates_past_dates_and_long_horizon(self):
        for value in ["2026-02-30", "2026-09-30", "2027-02-01", "10/01/2026", None]:
            with self.assertRaises(ValueError):
                self.select(posting=value)

    def test_explicit_topic_skips_feed(self):
        self.fact()
        self.assertEqual(self.select(skip_feed=True)["status"], "explicit_topic")
        self.assertEqual(ideas.inventory()["completed_selections"], 0)

    def test_replay_returns_same_reservation_and_completion_is_idempotent(self):
        self.fact()
        first = self.select()
        self.assertEqual(self.select(), first)
        self.assertIsNone(self.select(run="second")["idea"])
        ideas.complete({"run_key": "wf:1"})
        ideas.complete({"run_key": "wf:1"})
        self.assertEqual(ideas.inventory()["completed_selections"], 1)
        self.assertIsNone(self.select(run="third")["idea"])

    def test_failed_run_lease_expires_without_consuming_history(self):
        self.fact()
        self.select()
        with ideas.database() as db:
            db.execute("UPDATE selections SET selected=?", (time.time() - 3601,))
        self.assertIsNotNone(self.select(run="second")["idea"])
        self.assertEqual(ideas.inventory()["completed_selections"], 0)

    def test_parallel_runs_do_not_reserve_same_fact(self):
        self.fact()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(lambda run: self.select(run=run), ["run1", "run2"])
            )
        self.assertEqual(sum(r["idea"] is not None for r in results), 1)

    def test_own_event_parser_and_midnight_range_use_start_date(self):
        self.assertEqual(
            ideas.event_date(
                "Date & Time\nSaturday, Oct. 10, 2026\n8 p.m.\nLocation", True
            ),
            "2026-10-10",
        )
        self.assertEqual(
            ideas.event_date("Quand 10. – 11.10.2026\n17:00 – 01:00"), "2026-10-10"
        )
        self.assertIsNone(ideas.event_date("No confirmed event date"))

    def test_discovery_does_not_confuse_past_events_tab_with_section(self):
        source = ideas.SOURCES[0]
        found = ideas.discover(
            source,
            {
                "markdown": "Upcoming Past Events\n[Details](https://crush.lu/en/events/27/)\n[Details](https://crush.lu/en/events/27/)"
            },
        )
        self.assertEqual(len(found), 1)

    def test_allowlist_and_private_image_redaction(self):
        for url in [
            "http://www.mudam.com/a",
            "https://www.mudam.com.evil.test/a",
            "https://www.mudam.com/a?sig=secret",
            "https://user@www.mudam.com/a",
        ]:
            self.assertFalse(ideas.allowed(url, "www.mudam.com"))
        self.assertNotIn(
            "secret",
            ideas.plain(
                "![Coach](https://cdn.crush.lu/private.png?sig=secret)\nPublic text"
            ),
        )

    def test_multisite_event_deduplication_and_provenance(self):
        source = {
            "id": "one",
            "type": "event",
            "host": "www.mudam.com",
            "url": "https://www.mudam.com/agenda/event",
            "region": "Luxembourg City",
            "priority": 80,
        }
        data = {
            "markdown": "Nuit des Musées 2026\nQuand 10.10.2026",
            "metadata": {"title": "Nuit des Musées 2026"},
        }
        first = ideas.fact_from(source, data)
        second = ideas.fact_from(
            {**source, "id": "two"}, {**data, "metadata": {"title": "Nuit des Musees"}}
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["source_url"], source["url"])
        self.assertNotIn("markdown", first)

    def test_tourism_date_above_title_wins_over_unrelated_dates(self):
        source = {
            "id": "test",
            "type": "event",
            "host": "www.visitluxembourg.com",
            "url": "https://www.visitluxembourg.com/event/test",
            "region": "Luxembourg",
            "priority": 60,
        }
        data = {
            "metadata": {"title": "OKTOBERFEST 2026 - Visit Luxembourg"},
            "markdown": "**When? Friday 02.10.2026**\nOKTOBERFEST 2026\nDescription without a date\n##### Next events\n11.11.2026",
        }
        self.assertEqual(ideas.fact_from(source, data)["event_date"], "2026-10-02")
        data["markdown"] = (
            "OKTOBERFEST 2026\nDescription without a date\n##### Next events\n11.11.2026"
        )
        self.assertIsNone(ideas.fact_from(source, data))

    def test_article_heading_wins_over_navigation_links(self):
        source = next(s for s in ideas.SOURCES if s["id"] == "grund-walk")
        data = {
            "metadata": {"title": "Wenzel Walk - Visit Luxembourg City"},
            "markdown": "Wenzel Walk in pictures\nOther Tours\nWenzel Walk\n===========\nFollow the Corniche into Grund.\nOther Tours\nUnrelated recommendations",
        }
        fact = ideas.fact_from(source, data)
        self.assertEqual(fact["details"]["mentioned_places"], ["Corniche", "Grund"])


if __name__ == "__main__":
    unittest.main()
