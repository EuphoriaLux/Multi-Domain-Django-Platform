"""The one gate for the coach playground on test.crush.lu.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

Every playground entry point (view, service, management command, context
processor) checks :func:`playground_enabled` first. It is the same rule as the
Quiz Night rehearsal: on the staging slot (``DJANGO_ENV=staging``, slot-sticky)
or in local DEBUG. The production slot never passes it, so the playground code
that swaps into production stays inert there.
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404

from crush_lu.services.quiz_rehearsal import rehearsal_enabled


def playground_enabled():
    """True on the staging slot or in local DEBUG; never on production."""
    return rehearsal_enabled()


def require_playground():
    """Raise ``PermissionDenied`` off staging (services and commands)."""
    if not playground_enabled():
        raise PermissionDenied("The coach playground is only available on staging.")


def playground_or_404():
    """Raise ``Http404`` off staging, so production looks like it has no page."""
    if not playground_enabled():
        raise Http404
