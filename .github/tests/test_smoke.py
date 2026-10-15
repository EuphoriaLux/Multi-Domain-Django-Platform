"""Run scripts/smoke.py's stylesheet check against a local stub server.

Run without Django: python -m unittest discover -s .github/tests -v
"""

import importlib.util
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("smoke", ROOT / "scripts" / "smoke.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

PAGE = '<html lang="{lang}"><link href="/static/crush_lu/css/{css}"></html>'
FULL_CSS = b"a{}" * 20_000


class StubSite:
    """A slot that answers /en/ with ``pages`` in turn (the last one repeats)
    and serves only the stylesheets in ``styles``; anything else 500s, the
    way WhiteNoise does for an indexed file the deploy has deleted."""

    def __init__(self, pages, styles):
        self.pages = list(pages)
        self.styles = styles
        self.page_hits = 0
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/en/", "/de/", "/fr/"):
                    lang = self.path.strip("/")
                    if lang == "en":
                        css = site.pages[min(site.page_hits, len(site.pages) - 1)]
                        site.page_hits += 1
                    else:
                        css = site.pages[-1]
                    self._send(200, PAGE.format(lang=lang, css=css).encode())
                elif self.path.startswith("/static/crush_lu/css/"):
                    name = self.path.rsplit("/", 1)[1]
                    if name in site.styles:
                        self._send(200, site.styles[name])
                    else:
                        self._send(500, b"Internal Server Error")
                else:
                    self._send(404, b"")

            def _send(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class StylesheetCheckTests(unittest.TestCase):
    def setUp(self):
        smoke.failures.clear()
        smoke.passes = 0
        self._saved = (smoke.STYLESHEET_RETRY_SECONDS, smoke.STYLESHEET_RETRY_INTERVAL)
        smoke.STYLESHEET_RETRY_SECONDS = 1.0
        smoke.STYLESHEET_RETRY_INTERVAL = 0.05

    def tearDown(self):
        smoke.STYLESHEET_RETRY_SECONDS, smoke.STYLESHEET_RETRY_INTERVAL = self._saved
        smoke.failures.clear()

    def run_check(self, pages, styles):
        site = StubSite(pages, styles)
        self.addCleanup(site.close)
        smoke.check_generated_assets(site.base)
        return site

    def stylesheet_failures(self):
        return [f for f in smoke.failures if f.startswith("tailwind.css")]

    def test_passes_once_the_new_process_takes_over(self):
        # The old process links its deleted stylesheet twice, then the new
        # container answers with the new build's HTML.
        site = self.run_check(
            ["tailwind.0ld0ld0ld0ld.css"] * 2 + ["tailwind.feedfeedfeed.css"],
            {"tailwind.feedfeedfeed.css": FULL_CSS},
        )
        self.assertEqual(self.stylesheet_failures(), [])
        self.assertEqual(smoke.passes, 3)  # de, fr, stylesheet
        self.assertEqual(site.page_hits, 3)

    def test_a_stylesheet_that_never_appears_still_fails(self):
        site = self.run_check(["tailwind.0ld0ld0ld0ld.css"], {})
        [failure] = self.stylesheet_failures()
        # The hashed URL is in the report: it tells an old process's
        # stylesheet from a broken new build.
        self.assertIn("/static/crush_lu/css/tailwind.0ld0ld0ld0ld.css", failure)
        self.assertIn("HTTP Error 500", failure)
        self.assertGreater(site.page_hits, 1)

    def test_a_truncated_stylesheet_fails_without_waiting(self):
        site = self.run_check(
            ["tailwind.feedfeedfeed.css"], {"tailwind.feedfeedfeed.css": b"a{}"}
        )
        [failure] = self.stylesheet_failures()
        self.assertIn("only 3 bytes", failure)
        self.assertEqual(site.page_hits, 1)

    def test_a_page_without_the_stylesheet_fails_without_waiting(self):
        site = self.run_check(["other.css"], {})
        [failure] = self.stylesheet_failures()
        self.assertIn("links no crush_lu tailwind stylesheet", failure)
        self.assertEqual(site.page_hits, 1)


if __name__ == "__main__":
    unittest.main()
