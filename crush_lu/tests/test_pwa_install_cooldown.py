"""Execute PWA sources against browser stubs; browser tests check reactivity.

The source's unrelated shared-helper import is removed in the Node probe.
None of the install store/component methods use those helpers.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
JS_ROOT = Path(__file__).resolve().parents[1] / "static/crush_lu/js"

PROBE = r"""
const fs = require("fs");
const vm = require("vm");
const [jsRoot, optionsJson] = process.argv.slice(1);
const options = JSON.parse(optionsJson);
const NOW = 1800000000000;
const values = new Map(Object.entries({
    "crush-pwa-sessions": "2", "crush-pwa-last-seen": String(NOW),
    ...(options.values || {}),
}));

function runtime() {
    const components = {}, stores = {}, listeners = {}, watch = [];
    const storage = {
        getItem(key) {
            if (options.readBlocked) throw new Error("storage blocked");
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            if (options.writeBlocked && key === "crush-pwa-install-dismissed") {
                throw new Error("storage blocked");
            }
            values.set(key, String(value));
        },
        removeItem(key) {
            if (options.writeBlocked && key === "crush-pwa-install-dismissed") {
                throw new Error("storage blocked");
            }
            values.delete(key);
        },
    };
    const sandbox = {
        console, URL, URLSearchParams,
        Date: class extends Date { static now() { return NOW; } },
        CustomEvent: class {
            constructor(type, details = {}) {
                this.type = type; this.detail = details.detail;
            }
        },
        setTimeout: () => 0, clearTimeout: () => {}, localStorage: storage,
        location: { pathname: "/en/dashboard/", search: "" },
        navigator: {
            userAgent: options.ios ? "iPhone" : "Chrome",
            platform: options.ipad ? "MacIntel" : "",
            maxTouchPoints: options.ipad ? 5 : 0,
            standalone: !!options.installed,
        },
        matchMedia: () => ({ matches: !!options.installed }),
        addEventListener(name, callback) {
            (listeners[name] ||= []).push(callback);
        },
        dispatchEvent(event) {
            (listeners[event.type] || []).forEach(callback => callback(event));
        },
        document: {
            readyState: "complete",
            documentElement: { hasAttribute: () => !!options.native },
            body: { classList: { contains: () => false } },
            addEventListener(name, callback) {
                (listeners[name] ||= []).push(callback);
            },
            getElementById: () => null, createElement: () => ({}),
            head: { appendChild() {} },
        },
        Alpine: {
            data(name, factory) { components[name] = factory; },
            store(name, value) {
                if (value) { stores[name] = value; if (value.init) value.init(); }
                return stores[name];
            },
        },
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    const source = fs.readFileSync(jsRoot + "/alpine/core.js", "utf8");
    vm.runInContext(source.replace(/^import .*;$/m, ""), sandbox);
    sandbox.dispatchEvent({type: "alpine:init"});
    vm.runInContext(fs.readFileSync(jsRoot + "/pwa-install.js", "utf8"), sandbox);
    function component(name) {
        const c = components[name]();
        c.$watch = (key, callback) => watch.push(() => callback(c[key]));
        c.init(); flush(); return c;
    }
    function flush() { watch.forEach(callback => callback()); }
    function prompt(outcome) {
        sandbox.dispatchEvent({
            type: "beforeinstallprompt", preventDefault() {}, prompt() {},
            userChoice: Promise.resolve({outcome}),
        });
        flush();
    }
    return {
        sandbox, stores, component, flush, prompt,
        installer: () => vm.runInContext("new PWAInstaller()", sandbox),
    };
}

(async () => {
    let r = runtime();
    const banner = options.surface === "banner";
    let c = r.component(banner ? "pwaInstallBanner" : "pwaInstallButton");
    const installer = banner ? r.installer() : null;
    r.flush();
    if (!options.ios && !options.ipad) r.prompt(options.outcome || "dismissed");
    r.flush();
    const before = banner ? c.visible : c.cardVisible;
    if (options.dismiss) {
        if (banner) await installer.handleInstall();
        else { c.install(); await Promise.resolve(); }
        r.flush();
    }
    if (options.dismissEvent) {
        r.sandbox.dispatchEvent({type: "pwa-dismiss-install"}); r.flush();
    }
    const after = banner ? c.visible : c.cardVisible;
    const activeAfter = r.stores.prompts.active;
    const timestamp = values.get("crush-pwa-install-dismissed") || null;
    r = runtime();
    const reloadBanner = options.reloadSurface ? options.reloadSurface === "banner" : banner;
    c = r.component(reloadBanner ? "pwaInstallBanner" : "pwaInstallButton");
    if (reloadBanner) r.installer();
    if (!options.ios && !options.ipad) r.prompt("dismissed");
    r.flush();
    process.stdout.write(JSON.stringify({
        before, after, activeAfter, timestamp,
        afterReload: reloadBanner ? c.visible : c.cardVisible,
        installed: !!c.isInstalled, instructions: !!c.showInstructions,
    }));
})().catch(error => { console.error(error); process.exit(1); });
"""


def probe(**options):
    result = subprocess.run(
        [NODE, "-e", PROBE, str(JS_ROOT), json.dumps(options)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestPWAInstallCooldown:
    @pytest.mark.parametrize("surface", ["dashboard", "banner"])
    def test_dismissed_native_prompt_stays_hidden_after_reload(self, surface):
        result = probe(surface=surface, dismiss=True)
        assert result["before"] is True
        assert result["after"] is False
        assert result["activeAfter"] is None
        assert result["timestamp"] == "1800000000000"
        assert result["afterReload"] is False

    @pytest.mark.parametrize("surface", ["dashboard", "banner"])
    def test_accepting_prompt_does_not_record_dismissal(self, surface):
        result = probe(surface=surface, dismiss=True, outcome="accepted")
        assert result["timestamp"] is None
        assert result["activeAfter"] is None

    @pytest.mark.parametrize("ios,ipad", [(True, False), (False, True)])
    def test_ios_dashboard_honors_shared_cooldown(self, ios, ipad):
        result = probe(
            ios=ios,
            ipad=ipad,
            values={"crush-pwa-install-dismissed": "1799999999000"},
        )
        assert result["before"] is False
        assert result["instructions"] is False
        assert result["activeAfter"] is None

    @pytest.mark.parametrize(
        "value", ["true", "bad", "1797408000000", "1800000000001", "-1", "42junk"]
    )
    def test_expired_or_invalid_timestamp_allows_offer(self, value):
        result = probe(ios=True, values={"crush-pwa-install-dismissed": value})
        assert result["before"] is True
        assert result["timestamp"] is None

    def test_unexpired_timestamp_suppresses_android_offer(self):
        result = probe(values={"crush-pwa-install-dismissed": "1797408000001"})
        assert result["before"] is False

    def test_storage_read_failure_does_not_offer_install(self):
        result = probe(readBlocked=True, ios=True)
        assert result["before"] is False
        assert result["instructions"] is False

    @pytest.mark.parametrize("surface", ["dashboard", "banner"])
    def test_failed_timestamp_write_still_hides_current_offer(self, surface):
        result = probe(surface=surface, writeBlocked=True, dismiss=True)
        assert result["before"] is True
        assert result["after"] is False
        assert result["activeAfter"] is None

    def test_banner_dismiss_event_suppresses_dashboard_after_reload(self):
        result = probe(
            surface="banner", ios=True, dismissEvent=True, reloadSurface="dashboard"
        )
        assert result["before"] is True
        assert result["after"] is False
        assert result["timestamp"] == "1800000000000"
        assert result["afterReload"] is False

    def test_installed_card_remains_visible_during_cooldown(self):
        result = probe(
            installed=True,
            ios=True,
            values={"crush-pwa-install-dismissed": "1799999999000"},
        )
        assert result["before"] is True
        assert result["installed"] is True
        assert result["instructions"] is False

    def test_native_shell_does_not_offer_install(self):
        assert probe(native=True, ios=True)["before"] is False
