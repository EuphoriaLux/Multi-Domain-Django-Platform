/**
 * Quiz blanks: render question text whose hidden word is a run of underscores,
 * and fill the blank in with the real word once the server reveals it.
 *
 * The server replaces a coach's `{{word}}` with a fixed-length underscore run
 * before anything is broadcast (see crush_lu/services/quiz_blanks.py), so the
 * word is not in the page until the reveal message carries it as `segments`.
 * Any run of three or more underscores renders as the animated blank, which
 * keeps hand-typed `___` questions working.
 *
 * Nodes are built with createElement/textContent, never innerHTML: the text is
 * coach-authored and the page runs under a CSP.
 */
(function (global) {
    "use strict";

    var BLANK_RUN = /(_{3,})/;

    function _clear(el) {
        while (el.firstChild) el.removeChild(el.firstChild);
    }

    function _blank(label) {
        var span = document.createElement("span");
        span.className = "quiz-blank quiz-blank--open";
        span.setAttribute("role", "img");
        span.setAttribute("aria-label", label);
        return span;
    }

    function _word(word, index) {
        var span = document.createElement("span");
        span.className = "quiz-blank quiz-blank--revealed";
        span.textContent = word;
        // One word after another when a question hides several.
        span.style.animationDelay = index * 0.35 + "s";
        return span;
    }

    /**
     * Render `text` into `el`.
     *
     * `segments` is optional: alternating text and word strings from the
     * reveal message (even indexes text, odd indexes the hidden words). When
     * present the blanks are replaced by the words, animated in.
     */
    function render(el, text, segments, options) {
        if (!el) return;
        var o = options || {};
        var label = o.blankLabel || "blank";
        _clear(el);

        if (Array.isArray(segments) && segments.length > 1) {
            for (var i = 0; i < segments.length; i++) {
                if (i % 2 === 1) {
                    el.appendChild(_word(segments[i], (i - 1) / 2));
                } else if (segments[i]) {
                    el.appendChild(document.createTextNode(segments[i]));
                }
            }
            return;
        }

        var value = text || "";
        if (!BLANK_RUN.test(value)) {
            el.textContent = value;
            return;
        }
        var parts = value.split(BLANK_RUN);
        for (var j = 0; j < parts.length; j++) {
            if (!parts[j]) continue;
            if (j % 2 === 1) {
                el.appendChild(_blank(label));
            } else {
                el.appendChild(document.createTextNode(parts[j]));
            }
        }
    }

    global.QuizBlanks = { render: render };
})(window);
