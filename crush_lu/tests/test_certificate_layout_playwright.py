"""Browser regression for certificate statistics and narrow-screen containment.

Render the real certificate content with the built shared + journey styles. No
journey/database setup is needed to expose a cascade or intrinsic-width defect.
"""

import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template import Context, Template
from django.utils import translation

pytest.importorskip("playwright")
pytestmark = pytest.mark.playwright

ROOT = Path(__file__).resolve().parents[2]
LAYOUT_JS = """
() => {
    const certificate = document.querySelector('.certificate');
    const body = document.querySelector('.certificate-body');
    const stats = body.querySelector('[class$="stats-section"]');
    const rect = (el) => {
        const r = el.getBoundingClientRect();
        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    };
    return {
        viewport: innerWidth,
        docWidth: document.documentElement.scrollWidth,
        certificate: rect(certificate),
        body: rect(body),
        stats: rect(stats),
        boxes: Array.from(stats.children).map(el => ({
            ...rect(el), width: el.clientWidth, scrollWidth: el.scrollWidth,
            text: el.textContent.trim()
        })),
        statsWidth: stats.clientWidth,
        statsScrollWidth: stats.scrollWidth,
    };
}
"""


def _certificate_markup(language):
    source = (ROOT / "crush_lu/templates/crush_lu/journey/certificate.html").read_text(
        encoding="utf-8"
    )
    content = re.search(
        r"\{% block journey_content %\}(.*?)\{% endblock %\}", source, re.S
    ).group(1)
    context = {
        "journey": SimpleNamespace(
            journey_name="Our Journey", final_message="A beautiful new beginning."
        ),
        "journey_progress": SimpleNamespace(
            user=SimpleNamespace(crushprofile=SimpleNamespace(display_name="Marie"))
        ),
        "total_chapters": 6,
        "total_points": 12345,
        "hours_spent": 4,
        "minutes_spent": 30,
        "completion_date": date(2026, 10, 11),
    }
    with translation.override(language):
        return Template("{% load i18n static %}" + content).render(Context(context))


def _render_certificate(page, language, theme, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(
        f'<!doctype html><html lang="{language}" class="{theme}"><head>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '</head><body class="journey-bg"><main id="main-content" '
        'class="container pt-8 mobile-main has-bottom-nav">'
        '<div class="journey-container">'
        + _certificate_markup(language)
        + "</div></main></body></html>"
    )
    for bundle in ("tailwind", "journey"):
        page.add_style_tag(
            content=(ROOT / f"crush_lu/static/crush_lu/css/{bundle}.css").read_text(
                encoding="utf-8"
            )
        )
    # base.html's critical, unlayered container rule wins over Tailwind.
    page.add_style_tag(
        content=".container{max-width:1200px;margin-inline:auto;padding-inline:1rem}"
        "*,*::before,*::after{animation:none!important;transition:none!important}"
    )


@pytest.mark.parametrize("language", ["en", "de", "fr"])
@pytest.mark.parametrize("theme", ["", "dark"], ids=["light", "dark"])
@pytest.mark.parametrize("width", [320, 390, 1280])
def test_certificate_stays_inside_viewport(page, language, theme, width):
    _render_certificate(page, language, theme, width)
    measures = page.evaluate(LAYOUT_JS)
    assert measures["docWidth"] <= width, measures
    assert measures["statsScrollWidth"] <= measures["statsWidth"] + 1, measures
    for box in measures["boxes"]:
        assert box["left"] >= measures["body"]["left"] - 1, measures
        assert box["right"] <= measures["body"]["right"] + 1, measures
        assert box["scrollWidth"] <= box["width"] + 1, measures
    if width == 1280:
        assert len({round(box["top"]) for box in measures["boxes"]}) == 1, measures


@pytest.mark.parametrize("theme", ["", "dark"], ids=["light", "dark"])
def test_certificate_statistics_fit_when_printed(page, theme):
    _render_certificate(page, "de", theme, 794)
    page.emulate_media(media="print")
    measures = page.evaluate(LAYOUT_JS)
    assert measures["docWidth"] <= measures["viewport"], measures
    assert measures["statsScrollWidth"] <= measures["statsWidth"] + 1, measures
    for box in measures["boxes"]:
        assert box["left"] >= measures["body"]["left"] - 1, measures
        assert box["right"] <= measures["body"]["right"] + 1, measures
