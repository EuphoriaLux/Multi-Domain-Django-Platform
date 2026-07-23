# azureproject/urls_game.py
"""
URL configuration for Crush Empire (game.crush.lu).

Deliberately does NOT use `base_patterns`. That bundle mounts
`accounts/ -> allauth.urls` on every domain that includes it, which would give
this host a login form whose social buttons all 404 — every OAuth provider's
redirect URI points at crush.lu. Keeping allauth off this host is the whole
reason the cross-host handoff in views_empire_auth.py exists, so the omission
is load-bearing, not an oversight.

The auth callback is language-neutral: the return leg from crush.lu is a machine
redirect and carries no /en/ prefix.
"""

from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from crush_empire import api_game

from .urls_shared import health_check
from .views import csp_report
from .views_empire_auth import empire_auth_callback, empire_auth_start

# Language-neutral patterns (no /en/, /de/, /fr/ prefix)
urlpatterns = [
    path("healthz/", health_check, name="health_check"),
    path("csp-report/", csp_report, name="csp_report"),

    # Game API. Called from empire.js with hardcoded paths, so it must NOT sit
    # inside i18n_patterns. Every one of these POSTs — they all bank idle
    # production on the server clock, so none are safe to GET.
    path("api/game/sync/", api_game.sync, name="empire_api_sync"),
    path("api/game/deck/draw/", api_game.draw, name="empire_api_draw"),
    path("api/game/deck/resolve/", api_game.resolve, name="empire_api_resolve"),
    path("api/game/buy/", api_game.buy, name="empire_api_buy"),
    path("api/game/prestige/", api_game.prestige, name="empire_api_prestige"),
    path("api/game/clear-debuff/", api_game.clear_debuff, name="empire_api_clear_debuff"),

    # Cross-host session handoff. `auth_start` stashes a state nonce and bounces
    # to crush.lu; `auth_callback` consumes the returned code. Both must stay
    # language-neutral — crush.lu redirects here without a locale prefix.
    path("auth/start/", empire_auth_start, name="empire_auth_start"),
    path("auth/callback/", empire_auth_callback, name="empire_auth_callback"),

    # gettext() for the game's standalone JS bundle.
    path(
        "jsi18n/",
        JavaScriptCatalog.as_view(packages=["crush_empire"]),
        name="javascript-catalog",
    ),
]

# Language-prefixed patterns (user-facing pages)
urlpatterns += i18n_patterns(
    path("", include("crush_empire.urls", namespace="crush_empire")),
    prefix_default_language=True,
)
