/**
 * Full-screen photo viewer for Crush Connect cards.
 *
 * Opens crush_connect/_photo_viewer.html's <dialog> when a member taps a
 * photo rendered with {% profile_photo ... zoomable=True %} (a button marked
 * data-photo-viewer-open). The other photos of the same
 * [data-photo-viewer-group] (one member's card) become a gallery: arrows,
 * a horizontal swipe or the arrow keys move between them. Escape, the close
 * button or a tap beside the photo closes it.
 *
 * It only reuses the address each card <img> already loaded, so it can never
 * show a photo the card did not (the photo endpoint decides that).
 *
 * Plain script with document-level delegation, like confirm-sheet.js: it works
 * for cards added later (HTMX) and needs no Alpine component per card.
 */
(() => {
    const dialog = document.querySelector("[data-photo-viewer]");
    if (!dialog || typeof dialog.showModal !== "function") return;

    const image = dialog.querySelector("[data-photo-viewer-image]");
    const stage = dialog.querySelector("[data-photo-viewer-stage]");
    const prevButton = dialog.querySelector("[data-photo-viewer-prev]");
    const nextButton = dialog.querySelector("[data-photo-viewer-next]");
    const counter = dialog.querySelector("[data-photo-viewer-counter]");
    const closeButton = dialog.querySelector("[data-photo-viewer-close]");

    // A swipe must travel this far, and more sideways than up or down.
    const SWIPE_MIN_PX = 50;

    let photos = [];
    let index = 0;
    let opener = null;
    let touchStart = null;

    function sourceOf(img) {
        return img.currentSrc || img.getAttribute("src") || "";
    }

    function galleryFor(trigger) {
        const group = trigger.closest("[data-photo-viewer-group]");
        const triggers = group
            ? Array.from(group.querySelectorAll("[data-photo-viewer-open]"))
            : [trigger];
        const seen = new Set();
        return triggers
            .map((button) => button.querySelector("img"))
            .filter((img) => {
                const src = img && sourceOf(img);
                if (!src || seen.has(src)) return false;
                seen.add(src);
                return true;
            });
    }

    function show(position) {
        if (!photos.length) return;
        index = (position + photos.length) % photos.length;
        const img = photos[index];
        image.src = sourceOf(img);
        image.alt = img.alt || "";
        const several = photos.length > 1;
        prevButton.hidden = !several;
        nextButton.hidden = !several;
        counter.hidden = !several;
        counter.textContent = several ? `${index + 1} / ${photos.length}` : "";
    }

    function open(trigger) {
        const img = trigger.querySelector("img");
        if (!img || !sourceOf(img)) return false;
        photos = galleryFor(trigger);
        opener = trigger;
        show(Math.max(photos.indexOf(img), 0));
        dialog.showModal();
        return true;
    }

    function close() {
        if (dialog.open) dialog.close();
    }

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-photo-viewer-open]");
        if (trigger && open(trigger)) event.preventDefault();
    });

    closeButton.addEventListener("click", close);
    prevButton.addEventListener("click", () => show(index - 1));
    nextButton.addEventListener("click", () => show(index + 1));

    // A tap on the dark area around the photo closes; a tap on the photo
    // itself does not (it may be the end of a pinch or a swipe).
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog || event.target === stage) close();
    });

    dialog.addEventListener("keydown", (event) => {
        if (photos.length < 2) return;
        if (event.key === "ArrowLeft") {
            event.preventDefault();
            show(index - 1);
        } else if (event.key === "ArrowRight") {
            event.preventDefault();
            show(index + 1);
        }
    });

    stage.addEventListener(
        "touchstart",
        (event) => {
            // Two fingers is a pinch-zoom, not a swipe.
            touchStart =
                event.touches.length === 1
                    ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
                    : null;
        },
        { passive: true }
    );
    stage.addEventListener(
        "touchend",
        (event) => {
            if (!touchStart || photos.length < 2) return;
            const touch = event.changedTouches[0];
            const dx = touch.clientX - touchStart.x;
            const dy = touch.clientY - touchStart.y;
            touchStart = null;
            if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) <= Math.abs(dy)) return;
            show(dx < 0 ? index + 1 : index - 1);
        },
        { passive: true }
    );

    dialog.addEventListener("close", () => {
        // Drop the photo so the next opening never flashes the previous one.
        image.removeAttribute("src");
        photos = [];
        // Safari does not focus a tapped button, so the dialog cannot hand
        // focus back by itself.
        if (opener && opener.isConnected) opener.focus({ preventScroll: true });
        opener = null;
    });
})();
