/**
 * WhatsApp support FAB — keep it clear of primary call-to-action buttons.
 *
 * The floating WhatsApp button is position:fixed in the bottom-right corner.
 * Full-width primary CTAs (.btn-crush-primary etc.) scroll underneath it, so on
 * mobile the FAB can cover the right edge of a button the user is trying to tap
 * ("Join event", "Details", wallet buttons). This tucks the FAB away (fades it
 * out and disables pointer events) whenever it would overlap such a button, and
 * restores it once the button scrolls clear.
 *
 * No-ops when the FAB isn't rendered (WhatsApp disabled in site config).
 */
(function () {
    "use strict";

    const fab = document.querySelector(".crush-whatsapp-btn");
    if (!fab) return;

    // Buttons the FAB must never obstruct: every canonical crush button
    // variant (primary/solid/outline/danger + modifiers), full-width blocks,
    // and the wallet badge. Matched by substring so new variants are covered.
    const CTA_SELECTOR = '[class*="btn-crush"], .btn-block, .google-wallet-btn';
    const PAD = 8; // px of breathing room around the FAB's box

    let ticking = false;
    let tucked = false;

    function overlaps(a, b) {
        return !(
            a.right < b.left - PAD ||
            a.left > b.right + PAD ||
            a.bottom < b.top - PAD ||
            a.top > b.bottom + PAD
        );
    }

    function update() {
        ticking = false;
        // The FAB is position:fixed and only fades (never moves), so its rect is
        // stable whether tucked or not — safe to read every pass without flicker.
        const fabRect = fab.getBoundingClientRect();
        const vh = window.innerHeight;
        let collide = false;

        const ctas = document.querySelectorAll(CTA_SELECTOR);
        for (let i = 0; i < ctas.length; i++) {
            const r = ctas[i].getBoundingClientRect();
            if (r.bottom < 0 || r.top > vh || r.width === 0) continue; // off-screen / hidden
            if (overlaps(fabRect, r)) {
                collide = true;
                break;
            }
        }

        if (collide !== tucked) {
            tucked = collide;
            fab.classList.toggle("crush-whatsapp-btn--tucked", tucked);
        }
    }

    function schedule() {
        if (!ticking) {
            ticking = true;
            requestAnimationFrame(update);
        }
    }

    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    // Re-check after HTMX swaps that may add or move CTAs on the page.
    document.body.addEventListener("htmx:afterSettle", schedule);
    // Initial pass once the first layout is available.
    requestAnimationFrame(update);
})();
