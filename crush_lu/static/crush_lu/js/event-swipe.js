/**
 * Post-event swipe deck (templates/crush_lu/event_attendees.html).
 *
 * Progressive enhancement: every card is a plain form POST that works without
 * this script. Here we add drag-to-swipe, submit the same endpoint as JSON,
 * animate the card away and show the match overlay.
 */
(function () {
    "use strict";

    var deck = document.querySelector("[data-swipe-deck]");
    if (!deck) {
        return;
    }

    var THRESHOLD = 100; // px of horizontal drag that counts as a decision
    var busy = false;

    var overlay = document.querySelector("[data-swipe-match]");
    var live = document.querySelector("[data-swipe-live]");
    var errorTextEl = document.querySelector("[data-swipe-error-text]");
    var errorText = errorTextEl ? errorTextEl.textContent.trim() : "Error";

    function topCard() {
        return deck.querySelector("[data-swipe-card]:not(.swipe-card--queued):not([data-gone])");
    }

    function csrfToken(form) {
        var input = form.querySelector("input[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    function updateProgress() {
        var el = deck.querySelector("[data-swipe-progress]");
        if (!el) {
            return;
        }
        var done = parseInt(el.getAttribute("data-done"), 10) + 1;
        el.setAttribute("data-done", String(done));
        // The translated sentence always names "done" first, then "total".
        el.textContent = el.textContent.replace(/\d+/, String(done));
    }

    function showNext() {
        var next = deck.querySelector("[data-swipe-card].swipe-card--queued");
        if (next) {
            next.classList.remove("swipe-card--queued");
        } else {
            var empty = deck.querySelector("[data-swipe-empty]");
            if (empty) {
                empty.classList.remove("hidden");
            }
        }
    }

    function showMatch(name, chatUrl) {
        if (!overlay) {
            window.location.href = chatUrl;
            return;
        }
        var text = overlay.querySelector("[data-swipe-match-text]");
        if (text) {
            text.textContent = text.getAttribute("data-template").replace("__NAME__", name);
        }
        var chat = overlay.querySelector("[data-swipe-match-chat]");
        if (chat) {
            chat.setAttribute("href", chatUrl);
        }
        overlay.classList.remove("hidden");
        if (chat) {
            chat.focus();
        }
        addMatchLink(name, chatUrl);
    }

    function addMatchLink(name, chatUrl) {
        var list = document.querySelector("[data-swipe-matches]");
        if (!list) {
            return;
        }
        var placeholder = list.querySelector("[data-swipe-no-matches]");
        if (placeholder) {
            placeholder.remove();
        }
        var a = document.createElement("a");
        a.href = chatUrl;
        a.className = "flex items-center gap-4 bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm";
        var p = document.createElement("p");
        p.className = "font-semibold dark:text-white";
        p.textContent = name + " 💞";
        a.appendChild(p);
        list.insertBefore(a, list.firstChild);
    }

    function setStamp(card, dx) {
        var like = card.querySelector(".swipe-stamp--like");
        var pass = card.querySelector(".swipe-stamp--pass");
        var strength = Math.min(Math.abs(dx) / THRESHOLD, 1);
        if (like) {
            like.style.opacity = dx > 0 ? String(strength) : "0";
        }
        if (pass) {
            pass.style.opacity = dx < 0 ? String(strength) : "0";
        }
    }

    function resetCard(card) {
        card.classList.remove("swipe-card--dragging");
        card.style.transform = "";
        setStamp(card, 0);
    }

    function decide(card, decision) {
        if (busy || !card) {
            return;
        }
        busy = true;
        var form = card.querySelector("[data-swipe-form]");
        var buttons = form.querySelectorAll("button");
        buttons.forEach(function (b) { b.disabled = true; });

        var body = new FormData();
        body.append("decision", decision);
        body.append("csrfmiddlewaretoken", csrfToken(form));

        fetch(form.getAttribute("action"), {
            method: "POST",
            body: body,
            credentials: "same-origin",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "X-CSRFToken": csrfToken(form)
            }
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok || !result.data.ok) {
                    throw new Error((result.data && result.data.error) || errorText);
                }
                card.setAttribute("data-gone", "1");
                card.style.transform = "";
                card.classList.remove("swipe-card--dragging");
                card.classList.add(decision === "like" ? "swipe-card--gone-like" : "swipe-card--gone-pass");
                updateProgress();
                window.setTimeout(function () {
                    card.remove();
                    showNext();
                    busy = false;
                    if (result.data.match) {
                        showMatch(result.data.match_name, result.data.chat_url);
                    }
                }, 280);
                if (live) {
                    live.textContent = decision === "like" ? "♥" : "✕";
                }
            })
            .catch(function (err) {
                resetCard(card);
                buttons.forEach(function (b) { b.disabled = false; });
                busy = false;
                if (live) {
                    live.textContent = err.message || errorText;
                }
                window.alert(err.message || errorText);
            });
    }

    // Buttons: intercept the plain form submit.
    deck.addEventListener("click", function (event) {
        var button = event.target.closest("[data-swipe-decision]");
        if (!button) {
            return;
        }
        event.preventDefault();
        decide(button.closest("[data-swipe-card]"), button.getAttribute("data-swipe-decision"));
    });

    // Keyboard: arrow keys on the page.
    document.addEventListener("keydown", function (event) {
        if (overlay && !overlay.classList.contains("hidden")) {
            if (event.key === "Escape") {
                overlay.classList.add("hidden");
            }
            return;
        }
        var tag = (event.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea") {
            return;
        }
        if (event.key === "ArrowRight") {
            decide(topCard(), "like");
        } else if (event.key === "ArrowLeft") {
            decide(topCard(), "pass");
        }
    });

    // Drag / touch swipe on the top card.
    var startX = null;
    var startY = null;
    var dragCard = null;
    var dx = 0;

    deck.addEventListener("pointerdown", function (event) {
        if (busy || event.target.closest("button")) {
            return;
        }
        var card = event.target.closest("[data-swipe-card]");
        if (!card || card !== topCard()) {
            return;
        }
        dragCard = card;
        startX = event.clientX;
        startY = event.clientY;
        dx = 0;
        card.classList.add("swipe-card--dragging");
        if (card.setPointerCapture) {
            card.setPointerCapture(event.pointerId);
        }
    });

    deck.addEventListener("pointermove", function (event) {
        if (!dragCard) {
            return;
        }
        dx = event.clientX - startX;
        var dy = event.clientY - startY;
        dragCard.style.transform = "translate(" + dx + "px, " + dy * 0.2 + "px) rotate(" + dx / 15 + "deg)";
        setStamp(dragCard, dx);
    });

    function endDrag() {
        if (!dragCard) {
            return;
        }
        var card = dragCard;
        dragCard = null;
        if (Math.abs(dx) >= THRESHOLD) {
            decide(card, dx > 0 ? "like" : "pass");
        } else {
            resetCard(card);
        }
    }

    deck.addEventListener("pointerup", endDrag);
    deck.addEventListener("pointercancel", endDrag);

    // Match overlay close.
    if (overlay) {
        overlay.addEventListener("click", function (event) {
            if (event.target === overlay || event.target.closest("[data-swipe-match-close]")) {
                overlay.classList.add("hidden");
            }
        });
    }
})();
