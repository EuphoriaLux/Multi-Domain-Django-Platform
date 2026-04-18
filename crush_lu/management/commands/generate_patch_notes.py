"""
Generate draft PatchRelease + PatchNote rows from the Git history.

Reads crush_lu/data/release_milestones.json to decide how to group commits
into named releases. Commits that don't fall into a named milestone window
can be bucketed into monthly catch-up releases via --include-catchups.

All generated rows are created with is_published=False. An editor must
review and polish copy in Django admin before publishing.

Examples:
    python manage.py generate_patch_notes --since 2025-10-01 --dry-run
    python manage.py generate_patch_notes --since 2025-10-01
    python manage.py generate_patch_notes --since 2025-10-01 --purge-drafts
    python manage.py generate_patch_notes --milestones-only
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crush_lu.models import PatchNote, PatchNoteCategory, PatchRelease


# ---------------------------------------------------------------------------
# Filters & classification
# ---------------------------------------------------------------------------

# Commit paths / subjects that identify a crush.lu-relevant commit
CRUSH_PATHS = ("crush_lu/", "azureproject/urls_crush.py", "azureproject/domains.py")
CRUSH_KEYWORDS = re.compile(
    r"\b(crush|quiz|matching|trait|qualit|defect|luxid|daily\s*crush|spark|"
    r"wonderland|journey|advent|wallet|event|meetup|coach|profile|pwa|"
    r"newsletter|referral|connection)\b",
    re.IGNORECASE,
)

# Commits to silently drop
NOISE_PATTERNS = [
    re.compile(r"^merge\b", re.IGNORECASE),
    re.compile(r"^chore\(deps", re.IGNORECASE),
    re.compile(r"^chore:\s*bump\b", re.IGNORECASE),
    re.compile(r"^chore:\s*rebuild\s+tailwind", re.IGNORECASE),
    re.compile(r"^chore:\s*update\s+translations", re.IGNORECASE),
    re.compile(r"^ci\b", re.IGNORECASE),
    re.compile(r"^style\b", re.IGNORECASE),
    re.compile(r"^wip\b", re.IGNORECASE),
    re.compile(r"^update$", re.IGNORECASE),
    re.compile(r"potential fix for code scanning alert", re.IGNORECASE),
]

# Category assignment heuristics on the conventional-commit type prefix
CATEGORY_BY_TYPE = {
    "feat": PatchNoteCategory.FEATURE,
    "feature": PatchNoteCategory.FEATURE,
    "add": PatchNoteCategory.FEATURE,
    "fix": PatchNoteCategory.FIX,
    "bug": PatchNoteCategory.FIX,
    "hotfix": PatchNoteCategory.FIX,
    "perf": PatchNoteCategory.IMPROVEMENT,
    "refactor": PatchNoteCategory.IMPROVEMENT,
    "improve": PatchNoteCategory.IMPROVEMENT,
    "enhance": PatchNoteCategory.IMPROVEMENT,
    "ui": PatchNoteCategory.IMPROVEMENT,
    "ux": PatchNoteCategory.IMPROVEMENT,
    "security": PatchNoteCategory.UNDER_HOOD,
    "build": PatchNoteCategory.UNDER_HOOD,
    "infra": PatchNoteCategory.UNDER_HOOD,
    "deps": PatchNoteCategory.UNDER_HOOD,
    "storage": PatchNoteCategory.UNDER_HOOD,
}

# Scopes -> canonical buckets used for grouping notes inside a release
SCOPE_BUCKETS = {
    "quiz": "Quiz Night",
    "matching": "Matching",
    "match": "Matching",
    "trait": "Matching",
    "spark": "Crush Spark",
    "journey": "The Wonderland Journey",
    "wonderland": "The Wonderland Journey",
    "advent": "Advent calendar",
    "wallet": "Event tickets & wallet passes",
    "passkit": "Event tickets & wallet passes",
    "event": "Events",
    "meetup": "Events",
    "coach": "Coach tools",
    "profile": "Profiles",
    "auth": "Sign-in",
    "oauth": "Sign-in",
    "luxid": "Sign-in",
    "oidc": "Sign-in",
    "pwa": "App experience",
    "mobile": "App experience",
    "i18n": "Languages",
    "translation": "Languages",
    "newsletter": "Email updates",
    "email": "Email updates",
    "push": "Notifications",
    "notification": "Notifications",
    "referral": "Referrals",
    "connection": "Connections",
    "consent": "Privacy & consent",
    "privacy": "Privacy & consent",
    "gdpr": "Privacy & consent",
    "csp": "Under the hood",
    "security": "Under the hood",
    "storage": "Under the hood",
    "cache": "Under the hood",
    "deploy": "Under the hood",
    "infra": "Under the hood",
    "db": "Under the hood",
    "migration": "Under the hood",
    "logging": "Under the hood",
}

# Red-line filter patterns — we never surface these in public copy
SECRET_PATTERNS = [
    re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),                       # emails
    re.compile(r"https?://[\w\.-]+\.(azurewebsites|azure)\.net"),  # internal hosts
    re.compile(r"https?://claude\.ai/code/\S+"),                  # co-author session urls
    re.compile(r"co-authored-by:?[^\n]*", re.IGNORECASE),
    re.compile(r"signed-off-by:?[^\n]*", re.IGNORECASE),
]

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<subject>.+)$",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    """Strip red-line patterns and collapse whitespace."""
    cleaned = text or ""
    for pat in SECRET_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def classify_commit(subject: str):
    """Return (category, scope_bucket, human_subject) for a commit subject."""
    m = CONVENTIONAL_RE.match(subject.strip())
    if m:
        ctype = (m.group("type") or "").lower()
        scope = (m.group("scope") or "").lower()
        human = m.group("subject").strip()
    else:
        ctype, scope, human = "", "", subject.strip()

    category = CATEGORY_BY_TYPE.get(ctype, PatchNoteCategory.IMPROVEMENT)

    bucket = None
    for key, label in SCOPE_BUCKETS.items():
        if key in scope or key in subject.lower():
            bucket = label
            break
    return category, bucket or "Everything else", human


def humanize_subject(subject: str) -> str:
    """Mild rewrite of a commit subject into a user-facing sentence fragment."""
    s = subject.strip()
    s = re.sub(r"^add(?:s|ed)?\s+", "Added ", s, flags=re.IGNORECASE)
    s = re.sub(r"^fix(?:es|ed)?\s+", "Fixed ", s, flags=re.IGNORECASE)
    s = re.sub(r"^remove(?:s|d)?\s+", "Removed ", s, flags=re.IGNORECASE)
    s = re.sub(r"^improve(?:s|d)?\s+", "Improved ", s, flags=re.IGNORECASE)
    s = re.sub(r"^update(?:s|d)?\s+", "Updated ", s, flags=re.IGNORECASE)
    s = re.sub(r"^refactor(?:s|ed)?\s+", "Rebuilt ", s, flags=re.IGNORECASE)
    s = re.sub(r"^enhance(?:s|d)?\s+", "Enhanced ", s, flags=re.IGNORECASE)
    s = s[0].upper() + s[1:] if s else s
    return s.rstrip(".") + "."


# ---------------------------------------------------------------------------
# Git interaction
# ---------------------------------------------------------------------------


def git_log(since: str, until: str | None = None):
    """Return a list of (sha, iso_date, subject, paths_csv) tuples."""
    fmt = "%H%x1f%ad%x1f%s"
    cmd = ["git", "log", f"--since={since}", f"--pretty=format:{fmt}", "--date=short", "--name-only"]
    if until:
        cmd.append(f"--until={until}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = result.stdout
    entries = []
    for block in out.split("\n\n"):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        if len(lines) < 1 or "\x1f" not in lines[0]:
            continue
        sha, iso, subject = lines[0].split("\x1f", 2)
        paths = [ln for ln in lines[1:] if ln.strip()]
        entries.append((sha.strip(), iso.strip(), subject.strip(), paths))
    return entries


def touches_crush(paths, subject):
    if any(p.startswith(CRUSH_PATHS) for p in paths):
        return True
    return bool(CRUSH_KEYWORDS.search(subject))


def is_noise(subject):
    return any(p.search(subject) for p in NOISE_PATTERNS)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Generate draft PatchRelease + PatchNote rows from the Git history."

    def add_arguments(self, parser):
        parser.add_argument("--since", default="2025-10-01",
                            help="Earliest commit date to include (YYYY-MM-DD).")
        parser.add_argument("--until", default=None,
                            help="Latest commit date to include (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the proposed output without writing to the database.")
        parser.add_argument("--purge-drafts", action="store_true",
                            help="Delete existing drafts (is_published=False) before generating.")
        parser.add_argument("--milestones-only", action="store_true",
                            help="Skip commits that do not fall inside a milestone window.")
        parser.add_argument("--milestones-file", default=None,
                            help="Path to release_milestones.json (defaults to crush_lu/data/).")

    def handle(self, *args, **opts):
        milestones = self._load_milestones(opts["milestones_file"])
        commits = git_log(opts["since"], opts["until"])
        self.stdout.write(self.style.NOTICE(
            f"Scanned {len(commits)} commits between {opts['since']} and "
            f"{opts['until'] or 'today'}."
        ))

        relevant = [c for c in commits if touches_crush(c[3], c[2]) and not is_noise(c[2])]
        self.stdout.write(self.style.NOTICE(
            f"Kept {len(relevant)} crush.lu-relevant non-noise commits."
        ))

        buckets = self._bucket_commits(relevant, milestones, opts["milestones_only"])

        if opts["dry_run"]:
            self._print_plan(buckets)
            return

        if opts["purge_drafts"]:
            deleted = PatchRelease.objects.filter(is_published=False).delete()
            self.stdout.write(self.style.WARNING(f"Purged drafts: {deleted}"))

        with transaction.atomic():
            for meta, commits_ in buckets:
                self._write_release(meta, commits_)
        self.stdout.write(self.style.SUCCESS("Drafts written. Review in Django admin."))

    # ----- helpers --------------------------------------------------------

    def _load_milestones(self, path_override):
        base = Path(path_override) if path_override else (
            Path(__file__).resolve().parent.parent.parent / "data" / "release_milestones.json"
        )
        if not base.exists():
            raise CommandError(f"Milestones file not found: {base}")
        with base.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        for m in data:
            for key in ("since", "until", "released_on"):
                m[key] = datetime.strptime(m[key], "%Y-%m-%d").date()
        return data

    def _bucket_commits(self, commits, milestones, milestones_only):
        """Return list of (release_meta, [commits]) pairs."""
        bucketed = []
        used = set()
        for m in milestones:
            window = [
                c for c in commits
                if m["since"] <= date.fromisoformat(c[1]) < m["until"]
            ]
            if window:
                bucketed.append((m, window))
                used.update(c[0] for c in window)

        if milestones_only:
            return bucketed

        # Monthly catch-ups for anything not captured by a milestone
        orphans = [c for c in commits if c[0] not in used]
        by_month = defaultdict(list)
        for c in orphans:
            iso = c[1][:7]  # YYYY-MM
            by_month[iso].append(c)

        for month_key in sorted(by_month.keys()):
            month_commits = by_month[month_key]
            year, month = month_key.split("-")
            released = date(int(year), int(month), 28)
            meta = {
                "version": f"catchup-{month_key}",
                "slug": f"catchup-{month_key}",
                "title": f"{date(int(year), int(month), 1).strftime('%B %Y')} \u2014 small moves",
                "hero_summary": "A month of smaller updates and polish.",
                "released_on": released,
                "since": date(int(year), int(month), 1),
                "until": released + timedelta(days=4),
            }
            bucketed.append((meta, month_commits))
        return bucketed

    def _aggregate_into_notes(self, commits):
        """Turn a list of commit tuples into category + bucket grouped notes."""
        grouped = defaultdict(lambda: defaultdict(list))
        # grouped[category][scope_bucket] -> [ (sha, human_subject) ]
        for sha, iso, subject, _paths in commits:
            category, bucket, human = classify_commit(subject)
            grouped[category][bucket].append((sha, scrub(human)))

        notes = []
        order = 0
        for category in [
            PatchNoteCategory.FEATURE,
            PatchNoteCategory.IMPROVEMENT,
            PatchNoteCategory.FIX,
            PatchNoteCategory.UNDER_HOOD,
        ]:
            for bucket, items in grouped.get(category, {}).items():
                shas = [sha for sha, _ in items]
                subjects = [s for _, s in items if s]
                title = bucket
                if len(subjects) == 1:
                    body = humanize_subject(subjects[0])
                else:
                    bullets = [f"\u2022 {humanize_subject(s)}" for s in subjects[:8]]
                    if len(subjects) > 8:
                        bullets.append(f"\u2022 \u2026and {len(subjects) - 8} more small improvements.")
                    body = "\n".join(bullets)
                notes.append({
                    "category": category,
                    "title": title,
                    "body": body,
                    "related_commits": shas,
                    "order": order,
                })
                order += 1
        return notes

    def _write_release(self, meta, commits):
        release, _ = PatchRelease.objects.update_or_create(
            slug=meta["slug"],
            defaults={
                "version": meta["version"],
                "title": meta["title"],
                "hero_summary": meta.get("hero_summary", ""),
                "released_on": meta["released_on"],
                "is_published": False,
                "commit_range_start": commits[-1][0] if commits else "",
                "commit_range_end": commits[0][0] if commits else "",
            },
        )
        # Reset notes on regenerate so edits stay idempotent
        release.notes.all().delete()
        for note_data in self._aggregate_into_notes(commits):
            PatchNote.objects.create(release=release, **note_data)
        self.stdout.write(f"  {release.version} \u2014 {release.title} "
                          f"({release.notes.count()} notes, {len(commits)} commits)")

    def _print_plan(self, buckets):
        for meta, commits in buckets:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n{meta['version']} \u2014 {meta['title']} ({meta['released_on']}) "
                f"[{len(commits)} commits]"
            ))
            if meta.get("hero_summary"):
                self.stdout.write(f"  {meta['hero_summary']}")
            for note in self._aggregate_into_notes(commits):
                self.stdout.write(
                    f"  [{note['category']}] {note['title']} "
                    f"({len(note['related_commits'])} commits)"
                )
                for line in note["body"].splitlines()[:4]:
                    self.stdout.write(f"      {line}")
