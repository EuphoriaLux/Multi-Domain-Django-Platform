/**
 * Alpine.js CSP components for Crush.lu — connect bundle.
 *
 * Crush Connect: onboarding wizard, waitlist and weekly cycle pages.
 * Loaded with crush_lu/partials/alpine_bundle.html, before Alpine starts.
 * Build: `npm run build:js`.
 */

document.addEventListener("alpine:init", function () {
    // Age range dual-handle slider for "Your Ideal Crush" preferences
    Alpine.data("ageRangeSlider", function () {
        return {
            minAge: 18,
            maxAge: 99,
            absoluteMin: 18,
            absoluteMax: 99,
            labelAnyAge: "Any age",

            init: function () {
                // Read translated label from data attribute
                var label = this.$el.getAttribute("data-label-any-age");
                if (label) {
                    this.labelAnyAge = label;
                }
                // Read initial values from data attributes set by Django template
                var initMin = this.$el.getAttribute("data-initial-min");
                var initMax = this.$el.getAttribute("data-initial-max");
                if (initMin) {
                    this.minAge = parseInt(initMin, 10) || this.absoluteMin;
                }
                if (initMax) {
                    this.maxAge = parseInt(initMax, 10) || this.absoluteMax;
                }
            },

            get rangeLabel() {
                if (
                    this.minAge === this.absoluteMin &&
                    this.maxAge === this.absoluteMax
                ) {
                    return this.labelAnyAge;
                }
                return this.minAge + " – " + this.maxAge;
            },

            get isDefaultRange() {
                return (
                    this.minAge === this.absoluteMin && this.maxAge === this.absoluteMax
                );
            },

            get notDefaultRange() {
                return !this.isDefaultRange;
            },

            get trackStyle() {
                var range = this.absoluteMax - this.absoluteMin;
                var left = ((this.minAge - this.absoluteMin) / range) * 100;
                var right = ((this.absoluteMax - this.maxAge) / range) * 100;
                return "--range-left:" + left + "%;--range-right:" + right + "%";
            },

            get minBubbleStyle() {
                var pct =
                    ((this.minAge - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            get maxBubbleStyle() {
                var pct =
                    ((this.maxAge - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            updateMin: function (event) {
                var val = parseInt(event.target.value, 10);
                if (val >= this.maxAge) {
                    val = this.maxAge - 1;
                    event.target.value = val;
                }
                if (val < this.absoluteMin) {
                    val = this.absoluteMin;
                    event.target.value = val;
                }
                this.minAge = val;
                this._syncHiddenInputs();
            },

            updateMax: function (event) {
                var val = parseInt(event.target.value, 10);
                if (val <= this.minAge) {
                    val = this.minAge + 1;
                    event.target.value = val;
                }
                if (val > this.absoluteMax) {
                    val = this.absoluteMax;
                    event.target.value = val;
                }
                this.maxAge = val;
                this._syncHiddenInputs();
            },

            resetRange: function () {
                this.minAge = this.absoluteMin;
                this.maxAge = this.absoluteMax;
                this._syncHiddenInputs();
            },

            _syncHiddenInputs: function () {
                var root = this.$el;
                var minInput = root.querySelector("#id_preferred_age_min");
                var maxInput = root.querySelector("#id_preferred_age_max");
                if (minInput) {
                    minInput.value = this.minAge;
                    minInput.setAttribute("value", this.minAge);
                }
                if (maxInput) {
                    maxInput.value = this.maxAge;
                    maxInput.setAttribute("value", this.maxAge);
                }
            },
        };
    });

    // Single-thumb height slider for Crush Connect "life" step. Optional field:
    // "engaged" tracks whether a real cm value is submitted; the "prefer not to
    // say" pill disengages without losing the thumb position. Only the hidden
    // input carries name="height_cm" (server-rendered so no-JS keeps the value).
    Alpine.data("heightSlider", function () {
        return {
            height: 170,
            engaged: false,
            absoluteMin: 120,
            absoluteMax: 230,
            unit: "cm",
            labelNoAnswer: "—",

            init: function () {
                var el = this.$el;
                var min = parseInt(el.getAttribute("data-min"), 10);
                var max = parseInt(el.getAttribute("data-max"), 10);
                var def = parseInt(el.getAttribute("data-default"), 10);
                if (min) {
                    this.absoluteMin = min;
                }
                if (max) {
                    this.absoluteMax = max;
                }
                var unit = el.getAttribute("data-unit");
                if (unit) {
                    this.unit = unit;
                }
                var label = el.getAttribute("data-label-no-answer");
                if (label) {
                    this.labelNoAnswer = label;
                }
                var initial = parseInt(el.getAttribute("data-initial"), 10);
                if (!isNaN(initial)) {
                    this.height = this._clamp(initial);
                    this.engaged = true;
                } else {
                    this.height = def || 170;
                    this.engaged = false;
                }
            },

            get isEngaged() {
                return this.engaged;
            },

            get valueLabel() {
                return this.engaged
                    ? this.height + " " + this.unit
                    : this.labelNoAnswer;
            },

            get bubbleLabel() {
                return this.height + " " + this.unit;
            },

            get trackStyle() {
                var pct =
                    ((this.height - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "--range-left:0%;--range-right:" + (100 - pct) + "%";
            },

            get bubbleStyle() {
                var pct =
                    ((this.height - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            get sliderWrapClass() {
                return this.engaged ? "" : "opacity-40";
            },

            get noAnswerClass() {
                return this.engaged
                    ? "border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-crush-purple/50"
                    : "border-crush-purple bg-crush-purple/10 text-crush-purple";
            },

            get sliderValue() {
                return String(this.height);
            },

            get submitValue() {
                return this.engaged ? String(this.height) : "";
            },

            engage: function () {
                this.engaged = true;
            },

            update: function (event) {
                var val = parseInt(event.target.value, 10);
                if (isNaN(val)) {
                    return;
                }
                this.height = this._clamp(val);
                this.engaged = true;
            },

            clearHeight: function () {
                this.engaged = false;
            },

            _clamp: function (v) {
                return Math.min(this.absoluteMax, Math.max(this.absoluteMin, v));
            },
        };
    });

    // Crush Connect waitlist join button
    Alpine.data("crushConnectWaitlist", function () {
        return {
            onWaitlist: false,
            position: 0,
            total: 0,
            loading: false,
            error: "",

            init: function () {
                var el = this.$el.closest("[data-on-waitlist]");
                if (el) {
                    this.onWaitlist = el.getAttribute("data-on-waitlist") === "true";
                    this.position = parseInt(
                        el.getAttribute("data-position") || "0",
                        10,
                    );
                    this.total = parseInt(el.getAttribute("data-total") || "0", 10);
                }
            },

            get isJoined() {
                return this.onWaitlist;
            },
            // Bare getter for the pre-join UI: the CSP build can't evaluate
            // "!isJoined" inline, so x-show needs a named negation.
            get notJoined() {
                return !this.onWaitlist;
            },
            get isLoading() {
                return this.loading;
            },
            get hasError() {
                return this.error !== "";
            },
            get errorMessage() {
                return this.error;
            },

            get buttonClass() {
                if (this.onWaitlist) return "bg-white/20 cursor-default";
                return "bg-white text-teal-700 hover:bg-gray-100 shadow-lg";
            },

            get buttonText() {
                if (this.loading) return "...";
                if (this.onWaitlist) return "\u2713 On Waitlist";
                return "Join Waitlist";
            },

            get positionText() {
                if (!this.onWaitlist) return "";
                return "#" + this.position + " of " + this.total;
            },

            joinWaitlist: function () {
                if (this.onWaitlist || this.loading) return;
                var self = this;
                self.loading = true;
                self.error = "";

                var csrfToken = document.querySelector("[name=csrfmiddlewaretoken]");
                if (!csrfToken) {
                    csrfToken = document.querySelector('meta[name="csrf-token"]');
                }
                var token = csrfToken
                    ? csrfToken.value || csrfToken.getAttribute("content")
                    : "";

                fetch("/api/crush-connect/join/", {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": token,
                        "Content-Type": "application/json",
                    },
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        self.loading = false;
                        self.onWaitlist = true;
                        self.position = data.position;
                        self.total = data.total;
                    })
                    .catch(function () {
                        self.loading = false;
                        self.error = gettext("Something went wrong. Please try again.");
                    });
            },
        };
    });

    // Crush Connect 4-step onboarding wizard.
    // Crush Connect onboarding — per-step selection helpers only. Step
    // navigation is now server-side (one URL per step), so this just powers the
    // step-3 interest 8-cap counter and the step-7 second-story toggle.
    Alpine.data("connectOnboarding", function () {
        return {
            interestMax: 8,
            interestCount: 0,
            storyCount: 0,
            _showSecondStory: false,

            get showSecondStory() {
                return this._showSecondStory;
            },
            get notShowSecondStory() {
                return !this._showSecondStory;
            },

            init: function () {
                // Interests (step 3): count pre-checked boxes and enforce the
                // cap by toggling `disabled` imperatively. The CSP-friendly
                // Alpine build forbids expressions (e.g. "atCap && !checked")
                // inside x-bind:disabled, so the cap is handled here in JS.
                this._syncInterests();
                // Story answer (step 7): seed the character counter from the
                // server-rendered value.
                var ta = this.$root.querySelector('textarea[name="story_answer"]');
                this.storyCount = ta ? ta.value.length : 0;
                this._showSecondStory =
                    this.$root.getAttribute("data-show-second-story") === "true";
            },

            onInterestChange: function () {
                this._syncInterests();
            },

            _syncInterests: function () {
                // $root, not $el: also invoked via @change on the checkboxes,
                // where $el is the checkbox itself and the queries find nothing.
                var boxes = this.$root.querySelectorAll('input[name="interests"]');
                var checked = 0;
                boxes.forEach(function (b) {
                    if (b.checked) checked++;
                });
                this.interestCount = checked;
                var atCap = checked >= this.interestMax;
                boxes.forEach(function (b) {
                    b.disabled = atCap && !b.checked;
                });
            },

            onStoryInput: function () {
                var ta = this.$root.querySelector('textarea[name="story_answer"]');
                this.storyCount = ta ? ta.value.length : 0;
            },

            toggleSecondStory: function () {
                this._showSecondStory = !this._showSecondStory;
            },
        };
    });

    // Step 7 "Read-the-Photo" pick step: count how many questions have a Yes/No
    // truth chosen (skip = value ""). The form validates "exactly 3"
    // server-side; JS only keeps the counter and selected row styling live.
    Alpine.data("connectGatePicker", function () {
        return {
            max: 3,
            count: 0,
            init: function () {
                this._sync();
            },
            onPick: function () {
                this._sync();
            },
            _sync: function () {
                // $root, not $el: invoked via @change on child radios, where
                // $el is the radio itself and the queries find nothing.
                var radios = this.$root.querySelectorAll('input[type="radio"][data-gate]');
                var picked = {};
                radios.forEach(function (r) {
                    if (r.checked && r.value) picked[r.name] = true;
                });
                this.count = Object.keys(picked).length;

                var rows = this.$root.querySelectorAll("[data-gate-row]");
                rows.forEach(function (row) {
                    var selected = false;
                    row.querySelectorAll('input[type="radio"][data-gate]').forEach(function (r) {
                        if (r.checked && r.value) selected = true;
                    });
                    row.classList.toggle("border-crush-purple", selected);
                    row.classList.toggle("bg-crush-purple/5", selected);
                });
            },
        };
    });

    // "Read-the-Photo" answer gate on a Drop card / compose page: enable the
    // submit button only once all 3 questions have a Yes/No guess. Radios work
    // without JS; the view also enforces "all three answered" server-side.
    Alpine.data("connectGateCard", function () {
        return {
            init: function () {
                this._sync();
            },
            onPick: function () {
                this._sync();
            },
            _sync: function () {
                // $root, not $el: invoked via @change on child radios, where
                // $el is the radio itself and the queries find nothing.
                var radios = this.$root.querySelectorAll('input[type="radio"][data-gate]');
                var answered = {};
                radios.forEach(function (r) {
                    if (r.checked) answered[r.name] = true;
                });
                var names = {};
                radios.forEach(function (r) {
                    names[r.name] = true;
                });
                var total = Object.keys(names).length;
                var done = Object.keys(answered).length;
                var submit = this.$root.querySelector("[data-gate-submit]");
                if (submit) submit.disabled = total === 0 || done < total;
            },
        };
    });

    // Connect Cycle temp chat: venue picker mode toggle (partner venue vs a
    // custom name/address). Mirrors connectGateCard's idiom — a bare-name
    // @change handler that re-queries the checked radio via $root, then
    // directly toggles the two field groups' visibility, rather than
    // reactive x-show state (CSP build can't pass args to methods, so
    // there's nothing to bind x-show to except a bare getter anyway; a
    // direct DOM toggle keeps this consistent with connectGateCard).
    Alpine.data("connectVenuePicker", function () {
        return {
            init: function () {
                this._sync();
            },
            onModeChange: function () {
                this._sync();
            },
            _sync: function () {
                var checked = this.$root.querySelector('input[name="venue_mode"]:checked');
                var mode = checked ? checked.value : "partner";
                var partnerFields = this.$root.querySelector("[data-venue-partner-fields]");
                var customFields = this.$root.querySelector("[data-venue-custom-fields]");
                if (partnerFields) partnerFields.classList.toggle("hidden", mode !== "partner");
                if (customFields) customFields.classList.toggle("hidden", mode !== "custom");
            },
        };
    });

    // Connect Week review card: opens/closes the per-card native <dialog>
    // "Choose" confirmation (UX Wave 3 · WP12 / 6-06). Template-local — one
    // dialog per card via $refs, not a shared cross-page sheet/store (that
    // name belongs to Wave 2's forthcoming shared component).
    //
    // Some WebViews (and very old browsers) have <dialog> without
    // HTMLDialogElement.showModal — openDialog() would then silently no-op
    // and the member could never send the request. Fall back to the
    // existing global confirm helper (window.crushConfirm, see
    // confirm-sheet.js), which itself falls back to window.confirm when
    // even that isn't available, and submit the form directly on accept.
    Alpine.data("connectReviewChoice", function () {
        return {
            openDialog: function () {
                var dialog = this.$refs.dialog;
                if (dialog && typeof dialog.showModal === "function") {
                    dialog.showModal();
                    return;
                }
                var message = this.$root.getAttribute("data-confirm-message") || "";
                var form = this.$root.querySelector("form");
                var submitForm = function () {
                    if (!form) return;
                    if (typeof form.requestSubmit === "function") {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                };
                if (typeof window.crushConfirm === "function") {
                    window.crushConfirm(message).then(function (ok) {
                        if (ok) submitForm();
                    });
                } else if (window.confirm(message)) {
                    submitForm();
                }
            },
            closeDialog: function () {
                var dialog = this.$refs.dialog;
                if (dialog && typeof dialog.close === "function") dialog.close();
            },
        };
    });
});
