/**
 * Speed Dating TV Display — Alpine.js CSP-compliant component.
 *
 * Polls /api/events/<id>/tv-display-data/ every 8 seconds and renders the
 * current event phase on a big screen / TV in the room.
 *
 * Phases (in order): welcome → voting → voting_results → presentations → speed_dating
 */
document.addEventListener("alpine:init", function () {
    Alpine.data("speedDatingDisplay", function () {
        var el = this.$el;
        var eventId = parseInt(el.dataset.eventId) || 0;
        var eventTitle = el.dataset.eventTitle || "";
        var eventDate = el.dataset.eventDate || "";
        var eventLocation = el.dataset.eventLocation || "";

        // Translated strings injected from template
        var strCheckedIn = el.dataset.strCheckedIn || "checked in";
        var strConfirmed = el.dataset.strConfirmed || "confirmed";
        var strOf = el.dataset.strOf || "of";
        var strMale = el.dataset.strMale || "Male";
        var strFemale = el.dataset.strFemale || "Female";
        var strNonBinary = el.dataset.strNonBinary || "Non-binary";
        var strOther = el.dataset.strOther || "Other";
        var strVotesFor = el.dataset.strVotesFor || "votes";
        var strPresented = el.dataset.strPresented || "presented";
        var strTotal = el.dataset.strTotal || "total";
        var strPresenter = el.dataset.strPresenter || "Presenter";
        var strRound = el.dataset.strRound || "Round";
        var strCouplesRound = el.dataset.strCouplesRound || "couples this round";

        // ─────────────────────────────────────────────────────────────────
        // GENDER HELPERS
        // ─────────────────────────────────────────────────────────────────
        var GENDER_SYMBOL = { M: "♂", F: "♀", NB: "⚧", O: "⚬" };
        var GENDER_LABEL = { M: strMale, F: strFemale, NB: strNonBinary, O: strOther };
        var GENDER_COLOR = { M: "#3B82F6", F: "#EC4899", NB: "#8B5CF6", O: "#6B7280" };
        var GENDER_PILL = {
            M: "bg-blue-900/50 text-blue-300 border border-blue-700/60",
            F: "bg-pink-900/50 text-pink-300 border border-pink-700/60",
            NB: "bg-purple-900/50 text-purple-300 border border-purple-700/60",
            O: "bg-gray-800 text-gray-300 border border-gray-600",
        };

        function genderSymbol(g) {
            return GENDER_SYMBOL[g && g.toUpperCase()] || "⚬";
        }
        function genderLabel(g) {
            return GENDER_LABEL[g && g.toUpperCase()] || g;
        }
        function genderColor(g) {
            return GENDER_COLOR[g && g.toUpperCase()] || "#6B7280";
        }
        function genderPill(g) {
            return GENDER_PILL[g && g.toUpperCase()] || GENDER_PILL.O;
        }

        // ─────────────────────────────────────────────────────────────────
        // PHASE DISPLAY NAMES (for the top-bar phase indicator)
        // ─────────────────────────────────────────────────────────────────
        var PHASE_ORDER = ["welcome", "voting", "presentations", "speed_dating"];
        var PHASE_GROUPS = { voting_results: "voting" }; // voting_results is part of "voting" step

        // ─────────────────────────────────────────────────────────────────
        // IMPERATIVE RENDER HELPERS
        // ─────────────────────────────────────────────────────────────────
        function pct(count, list) {
            if (!list || !list.length) return 0;
            var total = list.reduce(function (s, o) {
                return s + (o.count || 0);
            }, 0);
            return total > 0 ? Math.round((count / total) * 100) : 0;
        }

        function renderVotingBars(ref, votes, gradientClass) {
            if (!ref) return;
            if (!votes || !votes.length) {
                ref.innerHTML = "";
                return;
            }
            var html = "";
            votes.forEach(function (opt) {
                var p = pct(opt.count, votes);
                html +=
                    '<div class="mb-5">' +
                    '  <div class="flex justify-between items-baseline mb-2">' +
                    '    <span class="text-white font-semibold text-lg leading-tight" style="max-width:70%">' +
                    _esc(opt.name) +
                    "</span>" +
                    '    <span class="text-gray-300 font-bold tabular-nums text-lg">' +
                    opt.count +
                    " " +
                    strVotesFor +
                    "</span>" +
                    "  </div>" +
                    '  <div class="h-9 bg-slate-700/70 rounded-xl overflow-hidden">' +
                    '    <div class="h-full rounded-xl ' +
                    gradientClass +
                    ' flex items-center px-3 transition-all duration-1000 ease-out" style="width:' +
                    p +
                    '%">' +
                    (p > 18
                        ? '<span class="text-white font-bold text-sm">' + p + "%</span>"
                        : "") +
                    "    </div>" +
                    "  </div>" +
                    "</div>";
            });
            ref.innerHTML = html;
        }

        function renderGenderBreakdown(ref, genderCounts, maxParticipants) {
            if (!ref) return;
            var keys = Object.keys(genderCounts || {});
            if (!keys.length) {
                ref.innerHTML = "";
                return;
            }
            var html = "";
            keys.forEach(function (g) {
                var count = genderCounts[g];
                var barPct =
                    maxParticipants > 0
                        ? Math.min(100, Math.round((count / maxParticipants) * 200)) // ×2 so bars are visible
                        : 50;
                html +=
                    '<div class="flex items-center gap-5 py-2">' +
                    '  <span class="text-4xl w-10 text-center">' +
                    genderSymbol(g) +
                    "</span>" +
                    '  <div class="flex-1">' +
                    '    <div class="flex items-end gap-3 mb-1.5">' +
                    '      <span class="text-4xl font-bold text-white tabular-nums">' +
                    count +
                    "</span>" +
                    '      <span class="text-gray-400 text-lg pb-0.5">' +
                    genderLabel(g) +
                    "</span>" +
                    "    </div>" +
                    '    <div class="h-2.5 bg-slate-700 rounded-full overflow-hidden w-48">' +
                    '      <div class="h-full rounded-full transition-all duration-1000 ease-out" style="width:' +
                    barPct +
                    "%;background-color:" +
                    genderColor(g) +
                    '"></div>' +
                    "    </div>" +
                    "  </div>" +
                    "</div>";
            });
            ref.innerHTML = html;
        }

        function renderGenderPills(ref, genderCounts) {
            if (!ref) return;
            var keys = Object.keys(genderCounts || {});
            if (!keys.length) {
                ref.innerHTML = "";
                return;
            }
            var html = "";
            keys.forEach(function (g) {
                html +=
                    '<span class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold ' +
                    genderPill(g) +
                    '">' +
                    genderSymbol(g) +
                    " " +
                    genderCounts[g] +
                    "</span>";
            });
            ref.innerHTML = html;
        }

        function _esc(str) {
            return String(str || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;");
        }

        return {
            // ── Config ────────────────────────────────────────────────────
            eventId: eventId,
            eventTitle: eventTitle,
            eventDate: eventDate,
            eventLocation: eventLocation,

            // ── Clock ─────────────────────────────────────────────────────
            currentTime: "",

            // ── Phase ─────────────────────────────────────────────────────
            screen: "welcome",

            // ── Attendance ────────────────────────────────────────────────
            attendedCount: parseInt(el.dataset.attended) || 0,
            confirmedCount: parseInt(el.dataset.confirmed) || 0,
            maxParticipants: parseInt(el.dataset.max) || 0,

            // ── Gender ────────────────────────────────────────────────────
            genderCounts: {},

            // ── Phase-specific payload ────────────────────────────────────
            phaseData: {},

            // ── Phase step labels (for top-bar dots) ─────────────────────
            phaseSteps: [
                { key: "welcome", label: el.dataset.strPhaseCheckin || "Check-in" },
                { key: "voting", label: el.dataset.strPhaseVoting || "Voting" },
                {
                    key: "presentations",
                    label: el.dataset.strPhasePresents || "Presentations",
                },
                {
                    key: "speed_dating",
                    label: el.dataset.strPhaseDating || "Speed Dating",
                },
            ],

            // ── Computed ──────────────────────────────────────────────────
            get attendancePct() {
                if (!this.maxParticipants) return 0;
                return Math.min(100, (this.attendedCount / this.maxParticipants) * 100);
            },
            get presenterNumber() {
                return (this.phaseData.completed_count || 0) + 1;
            },
            get votingTimerClass() {
                var t = this.phaseData.time_remaining || 0;
                if (t < 60) return "text-red-400";
                if (t < 180) return "text-yellow-400";
                return "text-white";
            },

            // ── Phase dot CSS ─────────────────────────────────────────────
            phaseDotClass: function (stepKey) {
                var norm = PHASE_GROUPS[this.screen] || this.screen;
                var ci = PHASE_ORDER.indexOf(norm);
                var ti = PHASE_ORDER.indexOf(stepKey);
                if (ti < 0) return "bg-slate-600";
                if (ti < ci) return "bg-emerald-400";
                if (ti === ci)
                    return "bg-gradient-to-r from-crush-purple to-crush-pink ring-2 ring-crush-purple/50";
                return "bg-slate-600";
            },
            phaseTextClass: function (stepKey) {
                var norm = PHASE_GROUPS[this.screen] || this.screen;
                return norm === stepKey ? "text-white font-semibold" : "text-gray-500";
            },

            // ── Countdown formatter ───────────────────────────────────────
            formatTime: function (seconds) {
                var s = Math.max(0, Math.floor(seconds));
                var m = Math.floor(s / 60);
                var r = s % 60;
                return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
            },

            // ── Init ──────────────────────────────────────────────────────
            init: function () {
                var self = this;
                self._updateClock();
                setInterval(function () {
                    self._updateClock();
                }, 1000);
                self._fetchData();
                setInterval(function () {
                    self._fetchData();
                }, 8000);

                // Kick off a second poll at 2s so first render is fast
                setTimeout(function () {
                    self._fetchData();
                }, 2000);
            },

            _updateClock: function () {
                var now = new Date();
                this.currentTime = now.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                });
            },

            _fetchData: function () {
                var self = this;
                fetch("/api/events/" + self.eventId + "/tv-display-data/")
                    .then(function (r) {
                        return r.ok ? r.json() : null;
                    })
                    .then(function (data) {
                        if (!data) return;
                        self.attendedCount = data.attended_count ?? self.attendedCount;
                        self.confirmedCount =
                            data.confirmed_count ?? self.confirmedCount;
                        self.maxParticipants =
                            data.max_participants ?? self.maxParticipants;
                        self.genderCounts = data.gender_counts || {};
                        self.phaseData = data.phase_data || {};
                        self.screen = data.phase || "welcome";
                        self._renderDynamic();
                    })
                    .catch(function (e) {
                        console.warn("TV display poll failed:", e);
                    });
            },

            // Imperative rendering for CSP-safe dynamic lists
            _renderDynamic: function () {
                var self = this;

                // Gender breakdown (welcome screen)
                renderGenderBreakdown(
                    self.$refs.genderBreakdown,
                    self.genderCounts,
                    self.maxParticipants,
                );

                // Gender pills (footer)
                renderGenderPills(self.$refs.genderPills, self.genderCounts);

                // Voting bars
                if (self.screen === "voting") {
                    renderVotingBars(
                        self.$refs.presVotes,
                        self.phaseData.presentation_votes,
                        "bg-gradient-to-r from-violet-600 to-crush-purple",
                    );
                    renderVotingBars(
                        self.$refs.twistVotes,
                        self.phaseData.twist_votes,
                        "bg-gradient-to-r from-crush-pink to-rose-500",
                    );
                }
            },
        };
    });
});
