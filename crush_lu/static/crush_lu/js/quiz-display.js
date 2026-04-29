/**
 * Quiz Display — Projector/big-screen Alpine.js component for live quiz events.
 *
 * CSP-compliant: all logic in methods/getters, no inline expressions.
 * Connects via WebSocket (display_token auth) for real-time updates.
 */
document.addEventListener("alpine:init", function () {
    Alpine.data("quizDisplay", function () {
        var eventId = parseInt(this.$el.dataset.eventId) || 0;
        var quizId = parseInt(this.$el.dataset.quizId) || 0;
        var pinRequired = this.$el.dataset.pinRequired === "true";
        var eventTitle = this.$el.dataset.eventTitle || "";
        var eventDate = this.$el.dataset.eventDate || "";
        var numTables = parseInt(this.$el.dataset.numTables) || 0;
        var confirmedCount = parseInt(this.$el.dataset.confirmedCount) || 0;
        var initialStatus = this.$el.dataset.quizStatus || "draft";

        // Translated UI strings — injected from the Django template via data-str-* attributes
        // so the server-rendered language (en/fr/de) is used for JS-generated HTML.
        var strTable = this.$el.dataset.strTable || "Table";
        var strPlayers = this.$el.dataset.strPlayers || "players";
        var strWaiting = this.$el.dataset.strWaiting || "Waiting for attendees...";
        var strAnchor = this.$el.dataset.strAnchor || "anchor";
        var strRotator = this.$el.dataset.strRotator || "rotator";
        var strPts = this.$el.dataset.strPts || "pts";
        var strTablesScored = this.$el.dataset.strTablesScored || "tables scored";

        return {
            // --- Config ---
            eventId: eventId,
            quizId: quizId,
            eventTitle: eventTitle,
            eventDate: eventDate,
            numTables: numTables,

            // --- PIN gate ---
            pinDigits: ["", "", "", ""],
            pinError: false,
            pinVerifying: false,
            displayToken: "",

            // --- Connection ---
            ws: null,
            connected: false,
            reconnectAttempts: 0,

            // --- Screen state ---
            // pin | loading | waiting | question | scoring | reveal | leaderboard | finished
            screen: pinRequired ? "pin" : "loading",
            quizStatus: initialStatus,

            // --- Check-in ---
            attendedCount: 0,
            confirmedCount: confirmedCount,

            // --- Tables (pre-quiz grid) ---
            tables: [],

            // --- Question phase ---
            questionId: null,
            questionText: "",
            questionType: "",
            choices: [],
            questionIndex: 0,
            questionTotal: 0,
            countdown: 0,
            countdownTotal: 30,
            _countdownTimer: null,
            roundName: "",
            isBonusRound: false,
            questionPoints: 10,

            // --- Scoring progress ---
            scoredCount: 0,
            totalTables: 0,

            // --- Score reveal ---
            revealResults: [],

            // --- Leaderboard ---
            leaderboardTables: [],
            leaderboardIndividuals: [],
            _leaderboardTimeout: null,

            // --- Polling (pre-quiz) ---
            _pollInterval: null,
            _pollDelay: null,

            // ============================================================
            // GETTERS (CSP-safe screen toggles)
            // ============================================================

            get isPinGate() {
                return this.screen === "pin";
            },
            get isWaiting() {
                return this.screen === "waiting";
            },
            get isQuestion() {
                return this.screen === "question";
            },
            get isScoring() {
                return this.screen === "scoring";
            },
            get isReveal() {
                return this.screen === "reveal";
            },
            get isLeaderboard() {
                return this.screen === "leaderboard";
            },
            get isLoading() {
                return this.screen === "loading";
            },
            get isFinished() {
                return this.screen === "finished";
            },

            get progressPercent() {
                if (this.confirmedCount <= 0) return 0;
                return Math.round((this.attendedCount / this.confirmedCount) * 100);
            },
            get progressBarStyle() {
                return "width: " + this.progressPercent + "%";
            },
            get attendedDisplay() {
                return this.attendedCount + " / " + this.confirmedCount;
            },

            get roundLabel() {
                var label = this.roundName || "";
                if (this.questionTotal > 0) {
                    label +=
                        " \u2014 Q" +
                        (this.questionIndex + 1) +
                        "/" +
                        this.questionTotal;
                }
                return label;
            },
            get bonusLabel() {
                return this.isBonusRound ? "BONUS \u00D72" : "";
            },

            get countdownDisplay() {
                return this.countdown + "s";
            },
            get timerPercent() {
                if (this.countdownTotal <= 0) return 100;
                return Math.round((this.countdown / this.countdownTotal) * 100);
            },
            get timerBarStyle() {
                return "width: " + this.timerPercent + "%";
            },
            get timerBarColorClass() {
                if (this.timerPercent > 50) return "bg-green-500";
                if (this.timerPercent > 20) return "bg-yellow-500";
                return "bg-red-500";
            },

            get scoringProgressText() {
                return this.scoredCount + " / " + this.totalTables + " " + strTablesScored;
            },
            get scoringBarStyle() {
                var pct =
                    this.totalTables > 0
                        ? Math.round((this.scoredCount / this.totalTables) * 100)
                        : 0;
                return "width: " + pct + "%";
            },

            get hasChoices() {
                return this.choices.length > 0;
            },

            // CSP-safe getters for template expressions
            get pinShakeClass() {
                return this.pinError ? "animate-shake" : "";
            },
            get hasNoTables() {
                return this.tables.length === 0;
            },
            get hasLeaderboardIndividuals() {
                return this.leaderboardIndividuals.length > 0;
            },
            get hasPodium() {
                return this.leaderboardTables.length >= 3;
            },

            // Podium getters
            get firstPlaceLabel() {
                return this.leaderboardTables.length > 0
                    ? strTable + " " + this.leaderboardTables[0].table_number
                    : "";
            },
            get firstPlaceScore() {
                return this.leaderboardTables.length > 0
                    ? this.leaderboardTables[0].total_score + " " + strPts
                    : "";
            },
            get secondPlaceLabel() {
                return this.leaderboardTables.length > 1
                    ? strTable + " " + this.leaderboardTables[1].table_number
                    : "";
            },
            get secondPlaceScore() {
                return this.leaderboardTables.length > 1
                    ? this.leaderboardTables[1].total_score + " " + strPts
                    : "";
            },
            get thirdPlaceLabel() {
                return this.leaderboardTables.length > 2
                    ? strTable + " " + this.leaderboardTables[2].table_number
                    : "";
            },
            get thirdPlaceScore() {
                return this.leaderboardTables.length > 2
                    ? this.leaderboardTables[2].total_score + " " + strPts
                    : "";
            },

            // ============================================================
            // LIFECYCLE
            // ============================================================

            init: function () {
                var self = this;

                // Bind PIN input events imperatively (CSP: no inline args)
                [0, 1, 2, 3].forEach(function (i) {
                    var ref = self.$refs["pin" + i];
                    if (ref) {
                        ref.addEventListener("input", function () {
                            self.handlePinInput(i);
                        });
                        ref.addEventListener("keydown", function (e) {
                            self.handlePinKeydown(i, e);
                        });
                    }
                });

                // Watch data changes to trigger imperative DOM renders
                this.$watch("tables", function () {
                    self.renderTables();
                });
                this.$watch("choices", function () {
                    self.renderChoices();
                });
                this.$watch("revealResults", function () {
                    self.renderReveal();
                });
                this.$watch("leaderboardTables", function () {
                    self.renderLeaderboard();
                    self.renderFinishedIndividuals();
                });
                this.$watch("leaderboardIndividuals", function () {
                    self.renderLeaderboard();
                    self.renderFinishedIndividuals();
                });

                if (!pinRequired) {
                    this.startDisplay();
                }
            },

            destroy: function () {
                if (this._pollDelay) clearTimeout(this._pollDelay);
                this.stopPolling();
                this.stopCountdown();
                if (this._leaderboardTimeout) clearTimeout(this._leaderboardTimeout);
                if (this.ws) this.ws.close();
            },

            // ============================================================
            // PIN GATE
            // ============================================================

            handlePinInput: function (index) {
                var refs = [
                    this.$refs.pin0,
                    this.$refs.pin1,
                    this.$refs.pin2,
                    this.$refs.pin3,
                ];
                var val = refs[index].value;
                // Only keep last digit
                if (val.length > 1) val = val.slice(-1);
                refs[index].value = val;
                this.pinDigits[index] = val;
                this.pinError = false;

                // Auto-advance to next input
                if (val && index < 3) {
                    refs[index + 1].focus();
                }

                // Auto-submit when all 4 digits entered
                if (
                    this.pinDigits[0] &&
                    this.pinDigits[1] &&
                    this.pinDigits[2] &&
                    this.pinDigits[3]
                ) {
                    this.verifyPin();
                }
            },

            handlePinKeydown: function (index, event) {
                // Handle backspace to go to previous input
                if (event.key === "Backspace" && !this.pinDigits[index] && index > 0) {
                    var refs = [
                        this.$refs.pin0,
                        this.$refs.pin1,
                        this.$refs.pin2,
                        this.$refs.pin3,
                    ];
                    refs[index - 1].focus();
                    refs[index - 1].value = "";
                    this.pinDigits[index - 1] = "";
                }
            },

            verifyPin: function () {
                var self = this;
                var pin = this.pinDigits.join("");
                if (pin.length !== 4) return;

                this.pinVerifying = true;
                this.pinError = false;

                fetch("/api/quiz/" + this.eventId + "/verify-pin/", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ pin: pin }),
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.pinVerifying = false;
                        if (data.valid) {
                            self.displayToken = pin;
                            self.startDisplay();
                        } else {
                            self.pinError = true;
                            self.pinDigits = ["", "", "", ""];
                            var refs = [
                                self.$refs.pin0,
                                self.$refs.pin1,
                                self.$refs.pin2,
                                self.$refs.pin3,
                            ];
                            refs.forEach(function (r) {
                                r.value = "";
                            });
                            refs[0].focus();
                        }
                    })
                    .catch(function () {
                        self.pinVerifying = false;
                        self.pinError = true;
                    });
            },

            // ============================================================
            // DISPLAY INITIALIZATION
            // ============================================================

            startDisplay: function () {
                this.screen = "loading";
                this.connectWebSocket();
                // Give WebSocket 1.5s head start before falling back to polling
                var self = this;
                this._pollDelay = setTimeout(function () {
                    if (!self.connected) {
                        self.startPolling();
                    }
                }, 1500);
            },

            // ============================================================
            // WEBSOCKET
            // ============================================================

            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                var url =
                    protocol +
                    "//" +
                    window.location.host +
                    "/ws/quiz/" +
                    this.quizId +
                    "/";
                if (this.displayToken) {
                    url += "?display_token=" + encodeURIComponent(this.displayToken);
                } else {
                    url += "?display=true";
                }

                this.ws = new WebSocket(url);

                this.ws.onopen = function () {
                    self.connected = true;
                    self.reconnectAttempts = 0;
                };

                this.ws.onclose = function () {
                    self.connected = false;
                    // Restart polling as fallback while WebSocket reconnects
                    if (!self._pollInterval && self.screen !== "pin") {
                        self.startPolling();
                    }
                    if (self.reconnectAttempts >= 20) return;
                    var delay = Math.min(
                        1000 * Math.pow(2, self.reconnectAttempts),
                        30000,
                    );
                    self.reconnectAttempts++;
                    setTimeout(function () {
                        self.connectWebSocket();
                    }, delay);
                };

                this.ws.onmessage = function (event) {
                    try {
                        var msg = JSON.parse(event.data);
                        self.handleMessage(msg);
                    } catch (e) {
                        // Ignore malformed messages
                    }
                };
            },

            handleMessage: function (msg) {
                var type = msg.type;
                var data = msg.data;

                if (type === "quiz.state") {
                    this.handleQuizState(data);
                } else if (type === "quiz.question") {
                    this.handleQuestion(data);
                } else if (type === "quiz.status") {
                    this.handleStatus(data);
                } else if (type === "quiz.table_scored") {
                    this.handleTableScored(data);
                } else if (type === "quiz.reveal_scores") {
                    this.handleRevealScores(data);
                } else if (type === "quiz.leaderboard") {
                    this.handleLeaderboard(data);
                } else if (type === "quiz.rotate") {
                    this.handleRotate(data);
                } else if (type === "quiz.table_update") {
                    this.handleTableUpdate(data);
                }
            },

            // ============================================================
            // MESSAGE HANDLERS
            // ============================================================

            handleQuizState: function (data) {
                this.quizStatus = data.status || "draft";

                if (data.current_round) {
                    this.roundName = data.current_round.title || "";
                    this.isBonusRound = data.current_round.is_bonus || false;
                }

                if (data.leaderboard) {
                    this.leaderboardTables = data.leaderboard.tables || [];
                    this.leaderboardIndividuals = data.leaderboard.individuals || [];
                }

                if (data.total_tables) {
                    this.totalTables = data.total_tables;
                }

                // Extract table and attendance data
                if (data.tables) {
                    this.tables = data.tables;
                }
                if (data.attended_count !== undefined) {
                    this.attendedCount = data.attended_count;
                }
                if (data.confirmed_count !== undefined) {
                    this.confirmedCount = data.confirmed_count;
                }

                // Determine screen based on state
                // Stop polling when WebSocket is connected and delivering data
                if (this.quizStatus === "finished") {
                    this.screen = "finished";
                    if (this.connected) this.stopPolling();
                } else if (this.quizStatus === "active" && data.question) {
                    this.showQuestion(data);
                    if (this.connected) this.stopPolling();
                } else {
                    this.screen = "waiting";
                    // Always poll on the waiting screen so new check-ins appear
                    // within 5 s without a page refresh. Polling stops automatically
                    // when a question arrives (handleQuestion calls stopPolling).
                    if (!this._pollInterval) {
                        this.startPolling();
                    }
                }
            },

            handleQuestion: function (data) {
                if (this.connected) this.stopPolling();
                this.questionId = data.id || null;
                this.questionText = data.text || "";
                this.questionType = data.question_type || "multiple_choice";
                this.questionPoints = data.points || 10;
                this.questionIndex = data.index || 0;
                this.questionTotal = data.total || 0;
                this.isBonusRound = data.is_bonus || false;
                this.scoredCount = data.scored_count || 0;
                this.totalTables = data.total_tables || this.totalTables;

                // Extract choices
                this.choices = [];
                if (data.choices && data.choices.length > 0) {
                    this.choices = data.choices.map(function (c) {
                        return c.text || c;
                    });
                }

                // Round name from context
                if (data.round_title) {
                    this.roundName = data.round_title;
                }

                // Start countdown — use nullish check so time_remaining=0 is respected
                var time = (data.time_remaining != null) ? data.time_remaining : (data.time || 30);
                this.countdownTotal = data.time || 30;
                this.startCountdown(time);

                this.screen = "question";
            },

            showQuestion: function (data) {
                // Build question from quiz.state format
                var q = data.question;
                this.questionId = q.id || null;
                this.questionText = q.text || "";
                this.questionType = q.question_type || "multiple_choice";
                this.questionPoints = q.points || 10;
                this.questionIndex = data.index || 0;
                this.questionTotal = data.total || 0;
                this.isBonusRound = data.is_bonus || false;
                this.scoredCount = data.scored_count || 0;
                this.totalTables = data.total_tables || this.totalTables;

                this.choices = [];
                if (q.choices && q.choices.length > 0) {
                    this.choices = q.choices.map(function (c) {
                        return c.text || c;
                    });
                }

                var time = (data.time_remaining != null) ? data.time_remaining : (data.time || 30);
                this.countdownTotal = data.time || 30;
                this.startCountdown(time);

                this.screen = "question";
            },

            handleStatus: function (data) {
                if (data.status === "finished") {
                    this.quizStatus = "finished";
                    this.stopCountdown();
                    if (this.connected) this.stopPolling();
                    if (data.leaderboard) {
                        this.leaderboardTables = data.leaderboard.tables || [];
                        this.leaderboardIndividuals =
                            data.leaderboard.individuals || [];
                    }
                    this.screen = "finished";
                } else if (data.status === "active") {
                    this.quizStatus = "active";
                    if (data.current_round) {
                        this.roundName = data.current_round.title || "";
                        this.isBonusRound = data.current_round.is_bonus || false;
                    }
                } else if (data.status === "paused") {
                    this.quizStatus = "paused";
                    this.stopCountdown();
                    // Show the table grid while paused between questions
                    this.screen = "waiting";
                    if (!this._pollInterval) {
                        this.startPolling();
                    }
                } else if (data.status === "round_complete") {
                    this.stopCountdown();
                    // Show leaderboard between rounds
                    if (this.leaderboardTables.length > 0) {
                        this.screen = "leaderboard";
                    }
                }
            },

            handleTableScored: function (data) {
                this.scoredCount = data.scored_count || this.scoredCount + 1;
                this.totalTables = data.total_tables || this.totalTables;
                // Switch to scoring screen if we were on question and timer is done
                if (this.screen === "question" && this.countdown <= 0) {
                    this.screen = "scoring";
                }
            },

            handleRevealScores: function (data) {
                this.revealResults = [];
                var self = this;
                // WS sends {results: [{table_id, table_number, is_correct, points_awarded}, ...]}
                var results = data.results || [];
                // Build a lookup from table_id to table_number using self.tables
                var idToNumber = {};
                for (var i = 0; i < this.tables.length; i++) {
                    if (this.tables[i].table_id) {
                        idToNumber[String(this.tables[i].table_id)] =
                            this.tables[i].table_number;
                    }
                }
                results.forEach(function (r) {
                    var tNum =
                        r.table_number || idToNumber[String(r.table_id)] || r.table_id;
                    self.revealResults.push({
                        table_number: tNum,
                        is_correct: r.is_correct,
                    });
                });
                this.revealResults.sort(function (a, b) {
                    return a.table_number - b.table_number;
                });
                this.screen = "reveal";
            },

            handleLeaderboard: function (data) {
                this.leaderboardTables = data.tables || [];
                this.leaderboardIndividuals = data.individuals || [];

                if (data.spotlight) {
                    this.screen = "leaderboard";
                }
            },

            handleRotate: function (data) {
                // After rotation, show leaderboard briefly then wait for next question
                if (this.leaderboardTables.length > 0) {
                    this.screen = "leaderboard";
                    var self = this;
                    this._leaderboardTimeout = setTimeout(function () {
                        if (self.screen === "leaderboard") {
                            self.screen = "waiting";
                        }
                    }, 8000);
                }
            },

            handleTableUpdate: function (data) {
                // Refresh table and attendance data from the API
                this.fetchDisplayData();
            },

            // ============================================================
            // COUNTDOWN TIMER
            // ============================================================

            startCountdown: function (seconds) {
                this.stopCountdown();
                this.countdown = Math.ceil(seconds);
                var self = this;
                this._countdownTimer = setInterval(function () {
                    self.countdown--;
                    if (self.countdown <= 0) {
                        self.countdown = 0;
                        self.stopCountdown();
                    }
                }, 1000);
            },

            stopCountdown: function () {
                if (this._countdownTimer) {
                    clearInterval(this._countdownTimer);
                    this._countdownTimer = null;
                }
            },

            // ============================================================
            // PRE-QUIZ POLLING (check-in + table data)
            // ============================================================

            startPolling: function () {
                var self = this;
                this.fetchDisplayData();
                // Poll every 5 seconds for responsive updates
                this._pollInterval = setInterval(function () {
                    self.fetchDisplayData();
                }, 5000);
            },

            stopPolling: function () {
                if (this._pollInterval) {
                    clearInterval(this._pollInterval);
                    this._pollInterval = null;
                }
            },

            fetchDisplayData: function () {
                var self = this;
                var url = "/api/quiz/" + this.eventId + "/display-data/";
                if (this.displayToken) {
                    url += "?token=" + encodeURIComponent(this.displayToken);
                }
                fetch(url)
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.attendedCount = data.attended_count || 0;
                        self.confirmedCount =
                            data.confirmed_count || self.confirmedCount;
                        self.tables = data.tables || [];
                        if (data.quiz_status) {
                            self.quizStatus = data.quiz_status;
                        }
                        if (data.total_tables) {
                            self.totalTables = data.total_tables;
                        }
                        if (data.scored_count !== undefined) {
                            self.scoredCount = data.scored_count;
                        }
                        if (data.leaderboard_tables) {
                            self.leaderboardTables = data.leaderboard_tables;
                        }
                        if (data.leaderboard_individuals) {
                            self.leaderboardIndividuals = data.leaderboard_individuals;
                        }

                        // Handle question data from polling (fallback when no WebSocket)
                        if (data.question && !self.connected) {
                            self.questionId = data.question.id || null;
                            self.questionText = data.question.text || "";
                            self.questionType =
                                data.question.question_type || "multiple_choice";
                            self.questionPoints = data.question.points || 10;
                            self.questionIndex = data.question_index || 0;
                            self.questionTotal = data.question_total || 0;
                            self.isBonusRound = data.is_bonus || false;
                            self.roundName = data.round_title || "";

                            self.choices = [];
                            if (
                                data.question.choices &&
                                data.question.choices.length > 0
                            ) {
                                self.choices = data.question.choices.map(function (c) {
                                    return c.text || c;
                                });
                            }

                            // Show reveal if all scored, otherwise question/scoring
                            if (data.reveal_results) {
                                self.revealResults = data.reveal_results.map(
                                    function (r) {
                                        return {
                                            table_number: r.table_number,
                                            is_correct: r.is_correct,
                                        };
                                    },
                                );
                                self.screen = "reveal";
                            } else if (
                                self.screen === "waiting" ||
                                self.screen === "loading"
                            ) {
                                // Only switch to question if we're on waiting/loading screen
                                if (!self._countdownTimer) {
                                    self.countdownTotal = data.time_per_question || 30;
                                    self.countdown = 0; // No timer sync via polling
                                }
                                self.screen = "question";
                            }
                        } else if (
                            !data.question &&
                            !self.connected &&
                            self.quizStatus === "finished"
                        ) {
                            self.screen = "finished";
                        } else if (
                            !data.question &&
                            !self.connected &&
                            self.screen !== "pin"
                        ) {
                            self.screen = "waiting";
                        }
                    })
                    .catch(function () {
                        // If still on loading screen, fall through to waiting
                        if (self.screen === "loading") {
                            self.screen = "waiting";
                        }
                    });
            },

            // ============================================================
            // IMPERATIVE DOM RENDERING (CSP-safe, no x-for with nested props)
            // ============================================================

            escapeHtml: function (text) {
                var div = document.createElement("div");
                div.textContent = text;
                return div.innerHTML;
            },

            _avatarHtml: function (member, size) {
                var s = size || "sm";
                var dim = s === "lg" ? "h-10 w-10 text-sm" : "h-7 w-7 text-xs";
                var imgDim = s === "lg" ? "h-10 w-10" : "h-7 w-7";
                var initials =
                    member.initials || member.display_name.slice(0, 2).toUpperCase();
                var color = member.color || "#8B5CF6";

                if (member.photo_url) {
                    return (
                        '<img src="' +
                        this.escapeHtml(member.photo_url) +
                        '" alt="" class="shrink-0 rounded-full object-cover ring-2 ring-slate-700 ' +
                        imgDim +
                        "\" onerror=\"this.style.display='none';this.nextElementSibling.style.display='flex'\">" +
                        '<div class="shrink-0 rounded-full items-center justify-center font-bold text-white ' +
                        dim +
                        '" style="display:none;background:' +
                        color +
                        '">' +
                        this.escapeHtml(initials) +
                        "</div>"
                    );
                }
                return (
                    '<div class="shrink-0 rounded-full flex items-center justify-center font-bold text-white ' +
                    dim +
                    '" style="background:' +
                    color +
                    '">' +
                    this.escapeHtml(initials) +
                    "</div>"
                );
            },

            renderTables: function () {
                var grid = this.$refs.tablegrid;
                if (!grid) return;
                grid.innerHTML = "";
                var self = this;
                this.tables.forEach(function (table) {
                    var card = document.createElement("div");
                    card.className =
                        "rounded-2xl bg-gradient-to-b from-slate-800 to-slate-800/80 p-5 shadow-xl ring-1 ring-white/10";

                    // Header with table number badge and score
                    var header = document.createElement("div");
                    header.className = "flex items-center justify-between mb-4";
                    var scoreHtml =
                        table.total_score > 0
                            ? '<span class="rounded-full bg-crush-pink/20 px-3 py-1 text-sm font-bold text-crush-pink">' +
                              table.total_score +
                              " " + strPts + "</span>"
                            : '<span class="rounded-full bg-slate-700 px-3 py-1 text-sm font-medium text-gray-400">' +
                              table.members.length +
                              " " + strPlayers + "</span>";
                    header.innerHTML =
                        '<div class="flex items-center gap-3">' +
                        '<div class="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-crush-purple to-crush-pink text-lg font-bold text-white shadow-lg shadow-crush-purple/20">' +
                        table.table_number +
                        "</div>" +
                        '<span class="text-lg font-bold text-white">' + strTable + ' ' +
                        table.table_number +
                        "</span></div>" +
                        scoreHtml;
                    card.appendChild(header);

                    // Members with avatars
                    if (table.members.length === 0) {
                        var empty = document.createElement("div");
                        empty.className =
                            "flex items-center justify-center py-4 text-sm text-gray-500 italic";
                        empty.textContent = strWaiting;
                        card.appendChild(empty);
                    } else {
                        var list = document.createElement("div");
                        list.className = "space-y-1.5";
                        table.members.forEach(function (m) {
                            var row = document.createElement("div");
                            row.className =
                                "flex items-center gap-2.5 rounded-lg bg-slate-700/40 px-3 py-2";
                            var roleHtml = "";
                            if (m.role === "anchor") {
                                roleHtml =
                                    '<span class="ml-auto text-xs text-blue-400 font-medium">\u2693 ' + strAnchor + '</span>';
                            } else if (m.role === "rotator") {
                                roleHtml =
                                    '<span class="ml-auto text-xs text-pink-400 font-medium">\u21BB ' + strRotator + '</span>';
                            }
                            row.innerHTML =
                                self._avatarHtml(m) +
                                '<span class="text-sm font-medium text-white">' +
                                self.escapeHtml(m.display_name) +
                                "</span>" +
                                roleHtml;
                            list.appendChild(row);
                        });
                        card.appendChild(list);
                    }

                    grid.appendChild(card);
                });
            },

            renderChoices: function () {
                var grid = this.$refs.choicesgrid;
                if (!grid) return;
                grid.innerHTML = "";
                this.choices.forEach(function (choice, idx) {
                    var card = document.createElement("div");
                    card.className =
                        "flex items-center gap-4 rounded-xl bg-slate-800 px-6 py-5 ring-1 ring-white/10";
                    var letter = String.fromCharCode(65 + idx);
                    card.innerHTML =
                        '<span class="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-crush-purple/20 text-xl font-bold text-crush-purple">' +
                        letter +
                        "</span>" +
                        '<span class="text-2xl font-medium text-white">' +
                        choice +
                        "</span>";
                    grid.appendChild(card);
                });
            },

            renderReveal: function () {
                var grid = this.$refs.revealgrid;
                if (!grid) return;
                grid.innerHTML = "";
                var self = this;
                var pts = this.questionPoints || 10;
                if (this.isBonusRound) pts *= 2;

                this.revealResults.forEach(function (result, idx) {
                    var card = document.createElement("div");
                    var correct = result.is_correct;
                    var bgClass = correct
                        ? "bg-gradient-to-b from-green-900/50 to-green-950/30 ring-2 ring-green-400/60 shadow-lg shadow-green-500/10"
                        : "bg-gradient-to-b from-red-900/30 to-red-950/20 ring-1 ring-red-500/30";
                    card.className =
                        "reveal-card flex flex-col items-center rounded-2xl p-5 " +
                        bgClass;
                    card.style.animationDelay = idx * 0.12 + "s";

                    var icon = correct
                        ? '<div class="flex h-16 w-16 items-center justify-center rounded-full bg-green-500/20 mb-3"><span class="text-4xl">\u2705</span></div>'
                        : '<div class="flex h-16 w-16 items-center justify-center rounded-full bg-red-500/20 mb-3"><span class="text-4xl">\u274C</span></div>';
                    var pointsHtml = correct
                        ? '<span class="text-sm font-bold text-green-400">+' +
                          pts +
                          " " + strPts + "</span>"
                        : '<span class="text-sm font-medium text-red-400/70">+0 ' + strPts + '</span>';

                    // Show member avatars for this table
                    var membersHtml = "";
                    var tbl = null;
                    for (var i = 0; i < self.tables.length; i++) {
                        if (self.tables[i].table_number === result.table_number) {
                            tbl = self.tables[i];
                            break;
                        }
                    }
                    if (tbl && tbl.members.length > 0) {
                        membersHtml = '<div class="flex -space-x-2 mt-2 mb-1">';
                        var shown = tbl.members.slice(0, 5);
                        shown.forEach(function (m) {
                            membersHtml += self._avatarHtml(m);
                        });
                        if (tbl.members.length > 5) {
                            membersHtml +=
                                '<div class="shrink-0 rounded-full flex items-center justify-center font-bold text-white h-7 w-7 text-xs bg-slate-600">+' +
                                (tbl.members.length - 5) +
                                "</div>";
                        }
                        membersHtml += "</div>";
                    }

                    card.innerHTML =
                        icon +
                        '<span class="text-xl font-bold text-white mb-1">' + strTable + ' ' +
                        result.table_number +
                        "</span>" +
                        pointsHtml +
                        membersHtml;
                    grid.appendChild(card);
                });
            },

            renderLeaderboard: function () {
                var list = this.$refs.leaderboardlist;
                if (!list) return;
                list.innerHTML = "";
                var self = this;
                this.leaderboardTables.forEach(function (entry, idx) {
                    var row = document.createElement("div");
                    var isTop3 = idx < 3;
                    var ringClass =
                        idx === 0
                            ? "ring-2 ring-yellow-500/40 bg-gradient-to-r from-yellow-500/10 to-slate-800"
                            : idx === 1
                              ? "ring-1 ring-gray-400/30 bg-gradient-to-r from-gray-400/10 to-slate-800"
                              : idx === 2
                                ? "ring-1 ring-amber-600/30 bg-gradient-to-r from-amber-600/10 to-slate-800"
                                : "ring-1 ring-white/5 bg-slate-800";
                    row.className =
                        "lb-entry flex items-center gap-4 rounded-2xl px-6 py-5 " +
                        ringClass;
                    row.style.animationDelay = idx * 0.08 + "s";

                    var rankIcon = self._getRankIconText(idx);

                    // Show stacked member avatars
                    var membersHtml = "";
                    var tbl = null;
                    for (var i = 0; i < self.tables.length; i++) {
                        if (self.tables[i].table_number === entry.table_number) {
                            tbl = self.tables[i];
                            break;
                        }
                    }
                    if (tbl && tbl.members.length > 0) {
                        membersHtml = '<div class="flex -space-x-1.5 ml-2">';
                        var shown = tbl.members.slice(0, 4);
                        shown.forEach(function (m) {
                            membersHtml += self._avatarHtml(m);
                        });
                        if (tbl.members.length > 4) {
                            membersHtml +=
                                '<div class="shrink-0 rounded-full flex items-center justify-center font-bold text-white h-7 w-7 text-xs bg-slate-600 ring-1 ring-slate-800">+' +
                                (tbl.members.length - 4) +
                                "</div>";
                        }
                        membersHtml += "</div>";
                    }

                    row.innerHTML =
                        '<span class="text-3xl w-10 text-center">' +
                        rankIcon +
                        "</span>" +
                        '<div class="flex-1 flex items-center gap-3">' +
                        '<span class="text-2xl font-bold text-white">' + strTable + ' ' +
                        entry.table_number +
                        "</span>" +
                        membersHtml +
                        "</div>" +
                        '<span class="text-2xl font-bold tabular-nums text-crush-pink">' +
                        entry.total_score +
                        " " + strPts + "</span>";
                    list.appendChild(row);
                });

                // Individuals
                var indList = this.$refs.individualslist;
                if (!indList) return;
                indList.innerHTML = "";
                this.leaderboardIndividuals.forEach(function (player, idx) {
                    var row = document.createElement("div");
                    row.className =
                        "lb-entry flex items-center gap-3 rounded-xl bg-slate-800/60 px-4 py-3 ring-1 ring-white/5";
                    row.style.animationDelay = idx * 0.05 + 0.3 + "s";
                    var avatar = self._avatarHtml({
                        display_name: player.display_name,
                        initials: player.initials,
                        color: player.color,
                        photo_url: player.photo_url,
                    });
                    row.innerHTML =
                        '<span class="text-sm font-bold text-gray-400 w-6 text-center">#' +
                        (idx + 1) +
                        "</span>" +
                        avatar +
                        '<span class="flex-1 text-sm font-medium text-white">' +
                        self.escapeHtml(player.display_name) +
                        "</span>" +
                        '<span class="text-sm font-bold text-crush-pink">' +
                        player.total_score + " " + strPts +
                        "</span>";
                    indList.appendChild(row);
                });
            },

            renderFinishedIndividuals: function () {
                var list = this.$refs.finishedindividuals;
                if (!list) return;
                list.innerHTML = "";
                var self = this;
                var top5 = this.leaderboardIndividuals.slice(0, 5);
                top5.forEach(function (player, idx) {
                    var row = document.createElement("div");
                    row.className =
                        "flex items-center gap-3 rounded-xl bg-slate-800/60 px-5 py-3.5 ring-1 ring-white/5";
                    var rankIcon = self._getRankIconText(idx);
                    var avatar = self._avatarHtml(
                        {
                            display_name: player.display_name,
                            initials: player.initials,
                            color: player.color,
                            photo_url: player.photo_url,
                        },
                        "lg",
                    );
                    row.innerHTML =
                        '<span class="text-xl w-8 text-center">' +
                        rankIcon +
                        "</span>" +
                        avatar +
                        '<span class="flex-1 text-lg font-medium text-white">' +
                        self.escapeHtml(player.display_name) +
                        "</span>" +
                        '<span class="text-lg font-bold tabular-nums text-crush-pink">' +
                        player.total_score +
                        " " + strPts + "</span>";
                    list.appendChild(row);
                });
            },

            // Internal helpers for rank styling
            _getRankColorClass: function (index) {
                if (index === 0) return "text-yellow-400";
                if (index === 1) return "text-gray-300";
                if (index === 2) return "text-amber-600";
                return "text-gray-400";
            },

            _getRankIconText: function (index) {
                if (index === 0) return "\uD83E\uDD47";
                if (index === 1) return "\uD83E\uDD48";
                if (index === 2) return "\uD83E\uDD49";
                return "#" + (index + 1);
            },
        };
    });
});
