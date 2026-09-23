"""The coach door scanner must never POST a QR that is not a Crush.lu ticket.

``coachCheckin.handleScan`` used to ``fetch(scannedText, {method: "POST"})``
unvalidated: an empty / ``?…`` / ``#…`` decode resolved to the door page's
own URL (prod: 403 "CSRF token missing" on ``/de/coach/events/<id>/checkin/``
from iOS Safari), and a foreign QR was POSTed to whatever host it named.

There is no JS unit harness in this repo, so this runs the real
``alpine-components.js`` in Node's ``vm`` with minimal ``document``/``Alpine``
stubs and exercises the component's own methods. Skipped when ``node`` is not
on PATH.
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
    / "alpine-components.js"
)

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [file, origin, casesJson] = process.argv.slice(1);
const loc = new URL(origin);
const components = {};
let initCb = null;
const fetched = [];
const alerts = [];
const timers = [];
const sandbox = {
    URL,
    console,
    setTimeout: (fn) => { timers.push(fn); return 0; },
    clearTimeout: () => {},
    gettext: (s) => s,
    alert: (m) => alerts.push(m),
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: (u) => { fetched.push(String(u)); return new Promise(() => {}); },
    document: {
        addEventListener: (name, cb) => { if (name === "alpine:init") initCb = cb; },
    },
    Alpine: {
        data: (name, fn) => { components[name] = fn; },
        store: () => {},
    },
};
sandbox.window = sandbox;
sandbox.window.location = {
    origin: loc.origin,
    protocol: loc.protocol,
    hostname: loc.hostname,
    port: loc.port,
    href: loc.href,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
initCb();
const out = [];
for (const text of JSON.parse(casesJson)) {
    const c = components.coachCheckin();
    c.processedIds = c.processedIds || {};
    const before = fetched.length;
    const path = c._checkinPathFromScan(text);
    c.scanBusy = false;
    c.handleScan(text);
    const sent = fetched.slice(before);
    // Run the resume timer: the scanner must re-arm after every outcome.
    timers.splice(0).forEach((fn) => fn());
    out.push({
        text, path, sent, errorState: !!c.errorState, message: c.message || "",
        scanBusyAfter: c.scanBusy,
    });
}
const c = components.coachCheckin();
out.push({
    missingEmpty: c._missingActionUrl(""),
    missingNull: c._missingActionUrl(null),
    missingOk: c._missingActionUrl("/api/events/1/verify/2/"),
    alerts: alerts.length,
});
process.stdout.write(JSON.stringify(out));
"""

VALID = "/api/events/checkin/42/42:21:AbC-d_9xYz/"

REJECTED = [
    "",
    "   ",
    "?foo=1",
    "#frag",
    "/de/coach/events/21/checkin/",
    "https://evil.example/api/events/checkin/42/t/",
    "//evil.example/api/events/checkin/42/t/",
    "http://crush.lu/api/events/checkin/42/t/",  # downgraded scheme
    "https://test.crush.lu/api/events/checkin/42/t/",  # other slot
    "https://crush.lu:8443/api/events/checkin/42/t/",  # other port
    "javascript:alert(1)",
    "http://",
    "/api/events/checkin/42/",
    "/api/events/checkin/abc/t/",
    "/api/events/checkin/42/t/extra/",
    "/api/events/checkin/42/../../x/",
    "WIFI:S:Bar;T:WPA;P:pw;;",
    "https://www.menu.example/",
]


def _run(origin, cases):
    result = subprocess.run(
        [NODE, "-e", HARNESS, str(JS_FILE), origin, json.dumps(cases)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestCheckinScanValidation:
    def test_rejected_scans_never_fetch_and_rearm_the_scanner(self):
        results = _run("https://crush.lu", REJECTED)[:-1]
        for row in results:
            assert row["path"] is None, row
            assert row["sent"] == [], row
            assert row["errorState"] is True, row
            assert row["message"] == "This QR code is not a Crush.lu ticket.", row
            assert row["scanBusyAfter"] is False, row

    def test_valid_ticket_posts_the_bare_path(self):
        cases = [
            VALID,
            "https://crush.lu" + VALID,
            "https://crush.lu" + VALID + "?utm=x#y",  # query/hash dropped
            "  https://crush.lu" + VALID + "  ",
            "https://www.crush.lu" + VALID,  # www alias is served, not redirected
        ]
        for row in _run("https://crush.lu", cases)[:-1]:
            assert row["path"] == VALID, row
            assert row["sent"] == [VALID], row

    def test_www_door_accepts_apex_ticket(self):
        row = _run("https://www.crush.lu", ["https://crush.lu" + VALID])[0]
        assert row["sent"] == [VALID]

    def test_missing_action_url_guard(self):
        summary = _run("https://crush.lu", [])[-1]
        assert summary["missingEmpty"] is True
        assert summary["missingNull"] is True
        assert summary["missingOk"] is False
        assert summary["alerts"] == 2
