"""
Generate the deck's portrait SVGs from each card's avatar_seed.

Dev-time only: needs Node and the repo's devDependencies (@dicebear/*).
The SVGs land in crush_empire/static/crush_empire/avatars/ and are committed —
production serves them, it never generates. Deterministic per seed, so
re-running is a no-op diff unless a seed changed.
"""
import json
import shutil
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from crush_empire.models import GameProfile

APP_DIR = Path(__file__).resolve().parents[2]
SCRIPT = APP_DIR / "scripts" / "generate_empire_avatars.mjs"
OUT_DIR = APP_DIR / "static" / "crush_empire" / "avatars"


class Command(BaseCommand):
    help = "Generate portrait SVGs for every active card with an avatar_seed."

    def handle(self, *args, **options):
        node = shutil.which("node")
        if node is None:
            raise CommandError("node not found on PATH — this is a dev-time command")

        items = [
            {"seed": p.avatar_seed, "file": Path(p.avatar_static_path).name}
            for p in GameProfile.objects.filter(is_active=True).exclude(avatar_seed="")
        ]
        if not items:
            self.stdout.write("no active cards carry an avatar_seed; nothing to do")
            return

        result = subprocess.run(
            [node, str(SCRIPT), str(OUT_DIR)],
            input=json.dumps(items),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise CommandError(result.stderr or "avatar generation failed")

        self.stdout.write(self.style.SUCCESS(result.stdout.strip()))
        self.stdout.write("Commit the SVGs — production never generates them.")
