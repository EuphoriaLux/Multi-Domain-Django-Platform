"""Playwright: every text/background pair on the light journey pages (WP14).

The light journey theme re-points ``--color-white`` to dark ink inside
``#main-content``, so any surface or text colour that was tuned for the navy
page can end up unreadable. Fixing such pairs one review comment at a time
missed classes, so this is a sweep rather than a list.

Method: every template under ``templates/crush_lu/journey/`` is reduced to its
static markup (Django tags stripped, both branches of every ``{% if %}`` kept,
``<script>`` dropped), injected into ``#main-content`` of a real journey page
rendered in the light theme with the built ``tailwind.css``, and each visible
text node is measured against its composited background. Gradients (surfaces
and ``background-clip: text`` titles) are measured at every colour stop, so the
worst stop decides. Anything under 4.5:1 (3:1 for large text) fails, unless it
is a deliberate exception in ``ALLOW`` with a reason.

Add a class to a journey template and it is covered automatically.

Excluded from the default run (``-m "not playwright"`` in pytest.ini); CI runs
it in the axe-smoke job (``-m "playwright and axe_smoke"``). Run:
    pytest -m playwright crush_lu/tests/test_journey_light_contrast_playwright.py -n 0
"""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

# axe_smoke puts this sweep in CI's accessibility job (#1147).
pytestmark = [
    pytest.mark.playwright,
    pytest.mark.axe_smoke,
    pytest.mark.django_db(transaction=True),
]

DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
JOURNEY_TEMPLATES = (
    Path(__file__).resolve().parents[1] / "templates" / "crush_lu" / "journey"
)
VIEWPORTS = {"phone": (390, 844), "desktop": (1280, 900)}

# JS-toggled states (Alpine getters, classList.add in journey.js and the gift
# wizard): (classes, hosts) - the classes are put on the elements the page's JS
# puts them on, hidden ones are shown, then everything is measured again.
STATES = [
    ([], ""),
    (["completed"], ".step-indicator, .journey-selector-card, .journey-challenge-card"),
    (["active"], ".step-indicator, .step-content"),
    (["has-file"], ".file-upload-wrapper"),
    (["show"], ".file-upload-preview"),
    (["selected"], ".journey-mc-option-card, .journey-wyr-option-card"),
    (["incorrect"], ".journey-mc-option-card"),
    (["is-invalid"], "input, textarea, select"),
    (["copied"], ".share-btn"),
    (["warning"], ".char-counter"),
    (["error"], ".char-counter"),
    (["journey-status-success"], '[x-bind\\:class="statusClass"]'),
    (["journey-status-error"], '[x-bind\\:class="statusClass"]'),
    (["journey-status-info"], '[x-bind\\:class="statusClass"]'),
    (
        ["journey-message-success", "p-6", "text-center"],
        '[x-bind\\:class="feedbackClass"]',
    ),
    (
        ["journey-message-error", "p-6", "text-center"],
        '[x-bind\\:class="feedbackClass"]',
    ),
]

# class-signature -> reason. Keep this empty unless a pair is genuinely
# decorative (e.g. aria-hidden glyphs).
ALLOW = {}

SWEEP_JS = r"""
({markup, classes, hosts}) => {
    const main = document.querySelector('#main-content');
    main.innerHTML = markup;
    // Dynamic text sources (x-text / x-html) are empty until Alpine fills them.
    main.querySelectorAll('[x-text], [x-html]').forEach(e => {
        if (!e.textContent.trim() && !e.children.length) e.textContent = 'Sample';
    });
    // JS-toggled states (step .completed, upload .has-file / .show, selected,
    // incorrect, status and counter variants...): put the classes on every
    // element, un-hide them, and measure the result.
    // Settled state: no entrance animations, scroll-reveals shown.
    if (!document.getElementById('sweep-settle')) {
        const st = document.createElement('style');
        st.id = 'sweep-settle';
        st.textContent = '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;animation-iteration-count:1!important;animation-fill-mode:forwards!important;transition:none!important}';
        document.head.appendChild(st);
    }
    main.querySelectorAll('.animate-on-scroll').forEach(e => e.classList.add('visible'));
    if (classes.length) {
        main.querySelectorAll(hosts).forEach(e => {
            e.classList.remove('hidden');
            classes.forEach(c => e.classList.add(c));
            if (!e.textContent.trim() && !e.children.length) e.textContent = 'Sample';
        });
    }
    const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
    const rgbaCache = new Map();
    const toRgba = (css) => {
        if (!rgbaCache.has(css)) rgbaCache.set(css, convert(css));
        return rgbaCache.get(css).slice();
    };
    const convert = (css) => {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = '#000';
        ctx.fillStyle = css;
        ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data;
        // getImageData is premultiplied-free for fillRect on a cleared canvas
        return [d[0], d[1], d[2], d[3] / 255];
    };
    const over = (top, bottom) => {
        const a = top[3] + bottom[3] * (1 - top[3]);
        if (a === 0) return [0, 0, 0, 0];
        return [0, 1, 2].map(i => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a).concat([a]);
    };
    const lum = ([r, g, b]) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const ratio = (a, b) => {
        const x = lum(a), y = lum(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    const stopsOf = (image) => {
        const m = image.match(/(?:rgba?|oklch|oklab|color|hsla?|lab|lch)\([^)]*\)/g);
        return m ? m.map(toRgba) : [];
    };
    const dedupe = (list) => {
        const seen = new Set();
        return list.filter(c => {
            const k = c.map(v => Math.round(v)).join(',');
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        });
    };
    const clipsText = (cs) => (cs.webkitBackgroundClip || cs.backgroundClip) === 'text';
    const backgrounds = (el, includeSelf) => {
        const chain = [];
        for (let n = includeSelf ? el : el.parentElement; n; n = n.parentElement) chain.unshift(n);
        // (a clip-text title is skipped below, so its own gradient is never a backdrop)
        let cands = [[255, 255, 255, 1]];
        for (const n of chain) {
            const cs = getComputedStyle(n);
            if (clipsText(cs)) continue;
            const base = toRgba(cs.backgroundColor);
            const stops = cs.backgroundImage !== 'none' ? stopsOf(cs.backgroundImage) : [];
            let layers = stops.length ? stops.map(s => over(s, base)) : (base[3] > 0 ? [base] : []);
            if (!layers.length) continue;
            const op = parseFloat(cs.opacity);
            cands = dedupe(cands.flatMap(c => layers.map(l => {
                const t = l.slice(); t[3] *= op;
                return over(t, c);
            })));
        }
        return cands;
    };
    const opacityOf = (el) => {
        let o = 1;
        for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity);
        return o;
    };
    const out = [];
    const seen = new Set();
    for (const el of main.querySelectorAll('*')) {
        const text = Array.from(el.childNodes)
            .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
        if (!text || /^[\W_]+$/.test(text) && text.length < 3) continue;
        if (!el.getClientRects().length) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || el.closest('[aria-hidden="true"], .sr-only, :disabled, [aria-disabled="true"]')) continue;
        let fgs;
        const fill = cs.webkitTextFillColor;
        if (clipsText(cs) && toRgba(fill)[3] === 0) {
            fgs = stopsOf(cs.backgroundImage);
        } else {
            fgs = [toRgba(fill && fill !== 'rgba(0, 0, 0, 0)' ? fill : cs.color)];
        }
        if (!fgs.length) continue;
        const bgs = backgrounds(el, true);
        const op = opacityOf(el);
        const size = parseFloat(cs.fontSize);
        const large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
        const need = large ? 3 : 4.5;
        let worst = Infinity, pair = '';
        for (const bg of bgs) for (const fg of fgs) {
            const solidBg = over(bg, [255, 255, 255, 1]);
            const f = fg.slice(); f[3] *= op;
            const r = ratio(over(f, solidBg), solidBg);
            if (r < worst) {
                worst = r;
                pair = 'fg ' + f.map(v => Math.round(v * 100) / 100) + ' on bg ' + solidBg.map(Math.round);
            }
        }
        if (worst < need) {
            const own = (n) => n.tagName.toLowerCase() + '.' + Array.from(n.classList).sort().join('.');
            const sig = (el.parentElement && el.parentElement !== main ? own(el.parentElement) + ' > ' : '') + own(el);
            if (seen.has(sig)) continue;
            seen.add(sig);
            out.push({sig, text: text.slice(0, 40), ratio: Math.round(worst * 100) / 100, need, pair});
        }
    }
    // ::placeholder colours against the field background.
    for (const el of main.querySelectorAll('input[placeholder], textarea[placeholder]')) {
        if (!el.getClientRects().length || !el.getAttribute('placeholder').trim()) continue;
        const ph = getComputedStyle(el, '::placeholder');
        const fg = toRgba(ph.color);
        fg[3] *= parseFloat(ph.opacity);
        const own = toRgba(getComputedStyle(el).backgroundColor);
        const bgs = backgrounds(el, true);
        let worst = Infinity, pair = '';
        for (const bg of bgs) {
            const solid = over(bg, [255, 255, 255, 1]);
            const r = ratio(over(fg, solid), solid);
            if (r < worst) { worst = r; pair = 'placeholder ' + fg.map(v => Math.round(v * 100) / 100) + ' on ' + solid.map(Math.round); }
        }
        if (worst < 4.5) {
            const sig = '::placeholder of ' + el.tagName.toLowerCase() + '.' + Array.from(el.classList).sort().join('.');
            if (!seen.has(sig)) {
                seen.add(sig);
                out.push({sig, text: el.getAttribute('placeholder').slice(0, 40), ratio: Math.round(worst * 100) / 100, need: 4.5, pair});
            }
        }
    }
    return out;
}
"""


def _markup(path):
    """Static markup of a Django template: tags stripped, branches merged."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    src = re.sub(r"\{% comment %\}.*?\{% endcomment %\}", "", src, flags=re.S)
    src = re.sub(r"<script\b.*?</script\s*[^>]*>", "", src, flags=re.S | re.I)
    src = re.sub(r"\{%\s*(?:trans|translate)\s+(['\"])(.*?)\1.*?%\}", r"\2", src)
    src = re.sub(r"\{%.*?%\}", "", src, flags=re.S)
    src = re.sub(r"\{\{.*?\}\}", "Sample", src, flags=re.S)
    src = re.sub(r"\sx-cloak(=\"[^\"]*\")?", "", src)
    src = re.sub(r"\shidden(?=[\s>])", "", src)
    return src


TEMPLATES = sorted(JOURNEY_TEMPLATES.rglob("*.html"))


def _page(browser, live_server, viewport):
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    user = _make_member("jy-contrast@example.com", is_staff=True)
    width, height = viewport
    context = browser.new_context(
        viewport={"width": width, "height": height}, color_scheme="light"
    )
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    # _markup turns {{ url }} into relative "Sample" srcs; answer them here so
    # the live_server log is not flooded with 404s.
    context.route("**/Sample*", lambda route: route.fulfill(status=204))
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    context.add_init_script("localStorage.setItem('theme', 'light');")
    page = context.new_page()
    response = page.goto(f"{live_server.url}/en/journey/gift/create/")
    assert response is not None and response.ok
    page.wait_for_function("() => window.Alpine && Alpine.store('prompts')")
    assert not page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    assert page.evaluate("() => document.body.classList.contains('journey-bg')")
    return page


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_every_light_journey_text_pair_reaches_aa(browser, live_server, viewport):
    page = _page(browser, live_server, VIEWPORTS[viewport])
    failures = {}
    for path in TEMPLATES:
        rel = str(path.relative_to(JOURNEY_TEMPLATES))
        markup = _markup(path)
        baseline = set()
        for state, hosts in STATES:
            hits = page.evaluate(
                SWEEP_JS, {"markup": markup, "classes": state, "hosts": hosts}
            )
            if not state:
                baseline = {hit["sig"] for hit in hits}
            for hit in hits:
                # A state pass only reports what the state itself broke.
                if hit["sig"] in ALLOW or (state and hit["sig"] in baseline):
                    continue
                key = hit["sig"] + (f"  [state: {'.'.join(state)}]" if state else "")
                failures.setdefault(
                    key,
                    f"{rel}: {hit['text']!r} {hit['ratio']}<{hit['need']}"
                    f" [{hit['pair']}]",
                )
    report = "\n".join(
        f"  {sig}  <- {where}" for sig, where in sorted(failures.items())
    )
    assert not failures, f"{len(failures)} light-theme contrast failures:\n{report}"
