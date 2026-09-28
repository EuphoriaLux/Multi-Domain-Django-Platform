"""Check-in toasts escape every value they write into ``innerHTML``.

CodeQL (js/xss) flagged ``coachCheckin._renderToastElement``: the photo-mismatch
button interpolated the reject URL, registration id and toast id, and the
translated labels, into ``innerHTML`` unescaped. This runs the committed
``coach.min.js`` in Node's ``vm`` with a tiny DOM stub whose ``_esc`` helper
behaves like the browser's (text node -> ``innerHTML``), renders one toast
carrying markup in every field, and checks the payload never becomes a tag.
Skipped when ``node`` is not on PATH.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
JS_FILE = (
    Path(__file__).resolve().parents[1]
    / "static"
    / "crush_lu"
    / "js"
    / "alpine"
    / "coach.min.js"
)

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const file = process.argv[1];
const esc = (s) => String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function el() {
    return {
        _text: "", html: "",
        appendChild(n) { if (n && "data" in n) this._text += n.data; else appended.push(n); },
        get innerHTML() { return this.html || esc(this._text); },
        set innerHTML(v) { this.html = v; },
        setAttribute() {}, querySelector() { return null; },
    };
}
const appended = [];
const container = el();
const components = {};
let initCb = null;
const sandbox = {
    URL, console, setTimeout: () => 0, clearTimeout: () => {},
    gettext: (s) => s,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: () => new Promise(() => {}),
    document: {
        addEventListener: (n, cb) => { if (n === "alpine:init") initCb = cb; },
        createElement: () => el(),
        createTextNode: (s) => ({ data: String(s) }),
        getElementById: () => container,
    },
    Alpine: { data: (n, fn) => { components[n] = fn; }, store: () => {} },
};
sandbox.window = sandbox;
const evil = '"><img src=x onerror=alert(1)>';
sandbox.window._checkinI18n = { rejectAction: evil, coach: evil, unverified: evil, verified: evil };
sandbox.window.location = { origin: "https://crush.lu", href: "https://crush.lu/" };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
initCb();
const c = components.coachCheckin();
c.eventId = evil;
const base = { id: evil, regId: evil, name: evil, genderIcon: evil, ageDisplay: evil,
               location: evil, hasTable: true, tableLabel: evil, interests: evil,
               hasPhoto: false, coachName: evil, submissionStatus: evil };
c._renderToastElement(Object.assign({}, base, { isApproved: true }));
c._renderToastElement(Object.assign({}, base, { isApproved: false }));
process.stdout.write(JSON.stringify(appended.map((d) => d.html)));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_toast_escapes_every_interpolated_value():
    result = subprocess.run(
        [NODE, "-e", HARNESS, str(JS_FILE)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    toasts = json.loads(result.stdout)
    assert len(toasts) == 2
    for html in toasts:
        assert "<img" not in html
    # The approved toast carries the photo-mismatch button, with escaped data-*.
    assert "toast-reject-btn" in toasts[0]
    assert "&quot;&gt;&lt;img" in toasts[0]
