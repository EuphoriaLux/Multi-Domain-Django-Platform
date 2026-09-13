"""The ``text/plain`` alternative of HTML emails.

Every sender in this codebase built its plain-text part with
``strip_tags(html_message)``. That is wrong in three ways at once, and all
three are invisible to anyone whose client prefers ``text/html``:

1. ``strip_tags`` removes the *tags* but keeps the text between them, so the
   ``<style>`` block in ``base_email.html`` was emitted verbatim — a rendered
   feedback email came out as 3,313 characters of plain text whose first ~2,500
   were CSS, before a single readable word.
2. Every ``href`` was dropped, so a call-to-action arrived as bare words with
   nowhere to go — which is how the Google review link was found, in review of
   PR #973.
3. Block elements were concatenated without whitespace, running separate
   paragraphs into one line.

``html_to_plain_text`` fixes all three. These tests pin the behaviour; the
sender-level consequence is asserted in ``test_google_review_ask.py``.

Run with: pytest crush_lu/tests/test_email_plain_text.py -v
"""

from django.test import TestCase

from azureproject.email_utils import html_to_plain_text


class DroppedElementsTests(TestCase):
    def test_style_content_never_reaches_the_reader(self):
        html = "<html><head><style>body { font-family: Arial; }</style></head><body><p>Hello</p></body></html>"
        plain = html_to_plain_text(html)
        self.assertNotIn("font-family", plain)
        self.assertNotIn("Arial", plain)
        self.assertEqual(plain, "Hello")

    def test_script_content_never_reaches_the_reader(self):
        plain = html_to_plain_text("<script>var x = 1;</script><p>Hi</p>")
        self.assertNotIn("var x", plain)
        self.assertEqual(plain, "Hi")

    def test_title_does_not_become_body_text(self):
        """<head> as a whole has nothing a plain-text reader wants; the title
        previously appeared as a stray first line above the greeting."""
        plain = html_to_plain_text(
            "<head><title>How was the event? - Crush.lu</title></head><p>Hey Gaby,</p>"
        )
        self.assertNotIn("Crush.lu</title>", plain)
        self.assertEqual(plain, "Hey Gaby,")


class LinkDestinationTests(TestCase):
    def test_anchor_keeps_its_destination(self):
        plain = html_to_plain_text('<a href="https://crush.lu/x/">Book now</a>')
        self.assertEqual(plain, "Book now (https://crush.lu/x/)")

    def test_anchor_with_attributes_after_href(self):
        plain = html_to_plain_text(
            '<a href="https://crush.lu/x/" class="button" target="_blank">Book now</a>'
        )
        self.assertEqual(plain, "Book now (https://crush.lu/x/)")

    def test_single_quoted_href(self):
        plain = html_to_plain_text("<a href='https://crush.lu/x/'>Book</a>")
        self.assertEqual(plain, "Book (https://crush.lu/x/)")

    def test_query_string_survives_intact(self):
        """The review link is a query-string URL; losing the placeid would make
        it point at a review composer with no business attached."""
        url = "https://search.google.com/local/writereview?placeid=ChIJ6a9_t1dy"
        plain = html_to_plain_text(f'<a href="{url}">Leave a Google review</a>')
        self.assertIn(url, plain)

    def test_self_describing_link_is_not_repeated(self):
        plain = html_to_plain_text('<a href="https://crush.lu">https://crush.lu</a>')
        self.assertEqual(plain, "https://crush.lu")

    def test_anchor_without_href_degrades_to_its_label(self):
        plain = html_to_plain_text("<a>Just text</a>")
        self.assertEqual(plain, "Just text")

    def test_nested_markup_inside_the_label(self):
        plain = html_to_plain_text('<a href="https://crush.lu/x/"><strong>Go</strong></a>')
        self.assertEqual(plain, "Go (https://crush.lu/x/)")


class LayoutTests(TestCase):
    def test_paragraphs_do_not_run_together(self):
        plain = html_to_plain_text("<p>First</p><p>Second</p>")
        self.assertEqual(plain, "First\n\nSecond")

    def test_line_breaks_become_newlines(self):
        plain = html_to_plain_text("Sincerely,<br><strong>The team</strong>")
        self.assertEqual(plain, "Sincerely,\nThe team")

    def test_entities_are_unescaped(self):
        plain = html_to_plain_text("<p>Caf&eacute; &amp; bar &mdash; 19:00</p>")
        self.assertEqual(plain, "Café & bar — 19:00")

    def test_blank_line_runs_are_collapsed(self):
        plain = html_to_plain_text("<div><p>A</p></div><div><div><p>B</p></div></div>")
        self.assertNotIn("\n\n\n", plain)

    def test_empty_input_is_handled(self):
        self.assertEqual(html_to_plain_text(""), "")
        self.assertEqual(html_to_plain_text(None), "")
