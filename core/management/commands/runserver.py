"""
Custom runserver command that displays all available local domains.
"""
from django.contrib.staticfiles.management.commands.runserver import (
    Command as RunserverCommand,
)
from azureproject.domains import DOMAINS, DEV_DEFAULT, DEV_DOMAIN_MAPPINGS


class Command(RunserverCommand):
    """Extended runserver command with domain information."""

    def inner_run(self, *args, **options):
        """Display available domains before starting the server."""

        # Display current default domain
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

        # Display all available local domains
        self.stdout.write(
            self.style.SUCCESS(
                f"\nAvailable local domains:\n"
                f"{'-'*70}\n"
            )
        )

        # localhost/127.0.0.1 routes to default
        self.stdout.write(
            f"  http://localhost:8000/  -> {current_config['name']}\n"
            f"  http://127.0.0.1:8000/ -> {current_config['name']}\n"
        )

        # Show all *.localhost domains
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

        # Show how to change default
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

        # Call parent implementation to start the server
        return super().inner_run(*args, **options)
