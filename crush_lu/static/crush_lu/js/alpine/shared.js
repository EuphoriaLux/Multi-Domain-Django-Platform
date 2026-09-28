/**
 * Shared helpers for the Crush.lu Alpine bundles (crush_lu/STYLE.md §7).
 *
 * Imported by the bundle entries in this folder; esbuild inlines what each
 * bundle uses (`npm run build:js`). Keep these stateless: every bundle gets
 * its own copy in production, while DEBUG shares one ES module instance.
 */

// =========================================================================
// SHARED INTERACTIVITY MIXINS (Phase 5 — see crush_lu/STYLE.md §7)
//
// These factories return plain state-and-method objects that named
// Alpine.data components compose with the `mixin` helper below. They
// are NOT Alpine.data registrations because the CSP build cannot pass
// args from x-data; wrap them in a named component instead.
//
// IMPORTANT: use `mixin`, NOT `Object.assign` / spread. Both of those
// *evaluate* getters on the source object during the copy (with `this`
// bound to the bare source literal, which lacks the mixin methods),
// turning a live `get isFoo()` into the literal value `undefined` —
// or a TypeError when the getter calls `this.somethingFromMixin()`.
// `mixin` copies descriptors via Object.defineProperties, so accessor
// properties stay live and resolve against the composed `this`.
//
// Usage:
//   Alpine.data("myTabs", () => mixin(
//       makeTabs("upcoming", ["upcoming", "past"]),
//       { get isUpcoming() { return this.isTabActive("upcoming"); },
//         showUpcoming() { this.setTab("upcoming"); } }
//   ));
// =========================================================================

export function mixin(target, source) {
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(source));
    return target;
}

// UX Wave 3 · WP5 — route a failure message through the shared toast
// store instead of a blocking alert(). Falls back to alert() only if
// the store genuinely isn't registered (defensive; toasts ship on
// every page via base.html).
export function notifyError(message) {
    if (typeof Alpine !== "undefined" && Alpine.store && Alpine.store("toasts")) {
        Alpine.store("toasts").add({ type: "error", message: message });
    } else {
        alert(message);
    }
}

export function makeTabs(initial, names) {
    return {
        activeTab: initial,
        _tabNames: names || [],
        setTab(name) {
            this.activeTab = name;
        },
        isTabActive(name) {
            return this.activeTab === name;
        },
    };
}

export function makeConfirm(opts) {
    var options = opts || {};
    return {
        confirming: !!options.initial,
        get isConfirming() {
            return this.confirming;
        },
        get isIdle() {
            return !this.confirming;
        },
        request() {
            this.confirming = true;
        },
        cancelConfirm() {
            this.confirming = false;
        },
        proceed() {
            this.confirming = false;
            if (options.autoSubmit !== false) {
                var form = this.$el && this.$el.closest("form");
                if (form) form.submit();
            }
        },
    };
}

export function makeModal(initiallyOpen) {
    return {
        open: !!initiallyOpen,
        showModal() {
            this.open = true;
        },
        hideModal() {
            this.open = false;
        },
        toggleModal() {
            this.open = !this.open;
        },
        get isModalOpen() {
            return this.open;
        },
        get isModalClosed() {
            return !this.open;
        },
    };
}
