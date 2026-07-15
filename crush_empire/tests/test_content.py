"""
Content hygiene: the seeded deck and its committed portrait assets must stay
in sync. Portraits are generated at dev time (manage.py generate_empire_avatars)
and committed; a card whose SVG was never generated renders as its emoji, so a
drifted deck fails here rather than shipping half-illustrated.
"""
from io import StringIO

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import TestCase

from crush_empire.models import GameProfile


class SeededAvatarTests(TestCase):
    def _seed(self):
        call_command("seed_empire_deck", stdout=StringIO())

    def test_seed_assigns_a_portrait_seed_to_every_card(self):
        self._seed()
        self.assertFalse(
            GameProfile.objects.filter(is_active=True, avatar_seed="").exists(),
            "seed_empire_deck left a card without a portrait seed",
        )

    def test_every_seeded_card_has_its_portrait_committed(self):
        self._seed()
        missing = [
            p.display_name
            for p in GameProfile.objects.filter(is_active=True).exclude(avatar_seed="")
            if finders.find(p.avatar_static_path) is None
        ]
        self.assertEqual(
            missing,
            [],
            "run `manage.py generate_empire_avatars` and commit the SVGs",
        )
