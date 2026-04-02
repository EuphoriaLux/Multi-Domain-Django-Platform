#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    """Run administrative tasks."""
    # If WEBSITE_HOSTNAME is defined as an environment variable, then we're running on Azure App Service

    # Only for Local Development - Load environment variables from the .env file
    if "WEBSITE_HOSTNAME" not in os.environ:
        from dotenv import load_dotenv

        if os.environ.get("RUN_MAIN"):  # Only print in main process
            print("Loading environment variables for .env file")
        load_dotenv("./.env")

    # When running on Azure App Service you should use the production settings.
    settings_module = (
        "azureproject.production"
        if "WEBSITE_HOSTNAME" in os.environ
        else "azureproject.settings"
    )
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    # Auto-switch to Uvicorn ASGI when REDIS_URL is set and running 'runserver'
    if (
        len(sys.argv) >= 2
        and sys.argv[1] == "runserver"
        and os.environ.get("REDIS_URL")
    ):
        import uvicorn

        # Parse address/port from args (e.g., "0.0.0.0:8000" or "8000")
        host, port = "127.0.0.1", 8000
        for arg in sys.argv[2:]:
            if not arg.startswith("-"):
                if ":" in arg:
                    host, port = arg.rsplit(":", 1)
                    port = int(port)
                else:
                    port = int(arg)
                break

        noreload = "--noreload" in sys.argv

        print(f"\n{'='*60}")
        print("ASGI mode (Uvicorn) — WebSocket support enabled")
        print(f"Redis: {os.environ['REDIS_URL']}")
        print(f"Listening: http://{host}:{port}/")
        print(f"{'='*60}\n")

        uvicorn.run(
            "azureproject.asgi:application",
            host=host,
            port=port,
            reload=not noreload,
            log_level="info",
        )
        return

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
