from django.shortcuts import render

from .decorators import crush_empire_enabled, empire_login_required


def teaser(request):
    """Public landing page. Shown when the flag is off, and to logged-out visitors."""
    return render(request, "crush_empire/teaser.html")


@crush_empire_enabled
@empire_login_required
def play(request):
    """The game itself. Flag-gated (staff bypass), session required."""
    return render(request, "crush_empire/play.html")
