"""
Custom runserver command that displays all available local domains.

When REDIS_URL is set, automatically launches Uvicorn (ASGI) instead of
the default WSGI server, enabling WebSocket support via Django Channels.
"""
import os
import sys

from django.conf import settings
from django.core.management.commands.runserver import Command as RunserverCommand
from azureproject.domains import DOMAINS, DEV_DEFAULT, DEV_DOMAIN_MAPPINGS


class Command(RunserverCommand):
    """Extended runserver command with domain information and ASGI support."""

    def _print_domain_info(self):
        """Display available domains before starting the server."""
        current_config = DOMAINS[DEV_DEFAULT]
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*70}\n"
                f"Multi-Domain Django Platform\n"
                f"{'='*70}\n"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                f"Default domain: {DEV_DEFAULT} ({current_config['name']})\n"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nAvailable local domains:\n"
                f"{'-'*70}\n"
            )
        )

        self.stdout.write(
            f"  http://localhost:8000/  -> {current_config['name']}\n"
            f"  http://127.0.0.1:8000/ -> {current_config['name']}\n"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nUse *.localhost domains to test other platforms:\n"
            )
        )

        for local_domain, real_domain in sorted(DEV_DOMAIN_MAPPINGS.items()):
            platform_name = DOMAINS[real_domain]['name']
            highlight = " (current)" if real_domain == DEV_DEFAULT else ""
            self.stdout.write(
                f"  http://{local_domain}:8000/ -> {platform_name}{highlight}\n"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTo change the default domain:\n"
                f"{'-'*70}\n"
            )
        )
        self.stdout.write(
            f"  Edit azureproject/domains.py and set:\n"
            f"  DEV_DEFAULT = 'vinsdelux.com'  # or any other domain\n"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'='*70}\n"
            )
        )

    def handle(self, *args, **options):
        # When REDIS_URL is set, use Uvicorn for ASGI + WebSocket support
        if os.environ.get("REDIS_URL"):
            self._print_domain_info()
            self._run_uvicorn(options)
        else:
            super().handle(*args, **options)

    def inner_run(self, *args, **options):
        """Display available domains before starting the WSGI server."""
        self._print_domain_info()
        return super().inner_run(*args, **options)

    def _run_uvicorn(self, options):
        """Start Uvicorn ASGI server with WebSocket support."""
        import uvicorn

        addrport = options.get("addrport", "") or "8000"
        if ":" in addrport:
            host, port = addrport.rsplit(":", 1)
        else:
            host, port = "127.0.0.1", addrport

        use_reloader = options.get("use_reloader", True)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nASGI mode (Uvicorn) — WebSocket support enabled\n"
                f"Redis: {os.environ['REDIS_URL']}\n"
                f"{'='*70}\n"
            )
        )

        uvicorn.run(
            "azureproject.asgi:application",
            host=host,
            port=int(port),
            reload=use_reloader,
            log_level="info",
        )
