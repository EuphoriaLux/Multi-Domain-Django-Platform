"""Custom Uvicorn worker that forces the asyncio event loop.

uvloop's run_in_executor is incompatible with asgiref's
CurrentThreadExecutor, causing RuntimeError under Django Channels.
See: https://github.com/django/channels/issues/1959

UvicornWorker.CONFIG_KWARGS defaults to {"loop": "auto"}, which
selects uvloop when installed. The UVICORN_LOOP env var only affects
the CLI, not the gunicorn worker class. This subclass is the
documented fix.
"""

from uvicorn.workers import UvicornWorker


class AsyncioUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {"loop": "asyncio", "http": "auto"}
