/**
 * Live Quiz Alpine.js CSP-compliant components.
 *
 * Two components:
 *   quizLive  – attendee view (table info, question display, leaderboard, rotate)
 *   quizHost  – host control panel (next question, score tables, rotate, leaderboard)
 *
 * Both use the native WebSocket API – no external libraries.
 */
document.addEventListener("alpine:init", function () {
    // ========================================================================
    // ATTENDEE COMPONENT
    // ========================================================================
    Alpine.data("quizLive", function () {
        return {
            // Connection
            ws: null,
            connected: false,
            quizId: null,
            reconnectAttempts: 0,

            // Mode
            isQuizNight: false,

            // State
            screen: "waiting", // waiting | question | leaderboard | rotate
            countdown: 0,
            countdownTotal: 30,
            countdownTimer: null,

            // Question
            question: null,
            choices: [],
            selectedIndex: null,
            answered: false,
            lastResult: null,
            questionIndex: 0,
            questionTotal: 0,

            // Table info (quiz night)
            tableNumber: 0,
            tablemates: [],
            personalScore: 0,
            nextTable: null,
            userRole: "",
            tableScoredFeedback: "",
            tableScoredTimer: null,
            _tableScoredCorrect: true,

            // Leaderboard
            tables: [],
            individuals: [],

            // Round info
            roundName: "",
            isBonusRound: false,

            // Error display
            errorMessage: "",
            errorTimer: null,

            // --- Getters (CSP-safe computed properties) ---

            get hasError() {
                return this.errorMessage !== "";
            },
            get isWaiting() {
                return this.screen === "waiting";
            },
            get isQuestion() {
                return this.screen === "question";
            },
            get isLeaderboard() {
                return this.screen === "leaderboard";
            },
            get isRotate() {
                return this.screen === "rotate";
            },
            get isFinished() {
                return this.screen === "finished";
            },
            get isConnected() {
                return this.connected;
            },
            get isDisconnected() {
                return !this.connected;
            },
            get hasAnswered() {
                return this.answered;
            },
            get hasSelected() {
                return this.selectedIndex !== null;
            },
            get showAnswerControls() {
                return !this.isQuizNight;
            },
            get hasTableInfo() {
                return this.isQuizNight && this.tableNumber > 0;
            },
            get hasNextTable() {
                return this.nextTable !== null;
            },
            get hasTableScoredFeedback() {
                return this.tableScoredFeedback !== "";
            },

            get questionText() {
                return this.question ? this.question.text : "";
            },
            get pointsLabel() {
                if (!this.question) return "";
                var pts = this.question.points + " pts";
                if (this.isBonusRound) return pts + " (x2 BONUS)";
                return pts;
            },
            get roundTitle() {
                return this.roundName;
            },
            get questionProgress() {
                if (!this.questionTotal) return "";
                return this.questionIndex + 1 + " / " + this.questionTotal;
            },
            get countdownDisplay() {
                return this.countdown + "s";
            },
            get timerBarStyle() {
                var pct =
                    this.countdownTotal > 0
                        ? (this.countdown / this.countdownTotal) * 100
                        : 0;
                return "width: " + pct + "%";
            },
            get feedbackClass() {
                if (!this.lastResult) return "bg-slate-700";
                return this.lastResult.is_correct
                    ? "bg-green-900/50 text-green-300"
                    : "bg-red-900/50 text-red-300";
            },
            get feedbackText() {
                if (!this.lastResult) return "";
                return this.lastResult.is_correct ? "Correct!" : "Wrong!";
            },
            get pointsFeedback() {
                if (!this.lastResult) return "";
                if (this.lastResult.is_correct) {
                    return "+" + this.lastResult.points_earned + " points";
                }
                return "Correct answer: " + (this.lastResult.correct_answer || "");
            },
            get tableLabel() {
                return "T" + this.tableNumber;
            },
            get personalScoreLabel() {
                return this.personalScore + " pts";
            },
            get hasPersonalScore() {
                return this.personalScore > 0;
            },
            get nextTableLabel() {
                if (!this.nextTable) return "";
                return "Next: Table " + this.nextTable;
            },
            get roleLabel() {
                if (this.userRole === "anchor") return "Anchor (stay at table)";
                if (this.userRole === "rotator") return "Rotator (you move!)";
                return "";
            },
            get rotateDestination() {
                return this.tableNumber || this.nextTable || "";
            },
            get rotateMessage() {
                if (this.userRole === "anchor") {
                    return "Stay at your table!";
                }
                if (this.tableNumber) {
                    return "Move to Table " + this.tableNumber + "!";
                }
                return "Please move to the next table.";
            },
            get tableScoredFeedbackClass() {
                if (this._tableScoredCorrect) {
                    return "bg-green-900/50 text-green-300";
                }
                return "bg-red-900/50 text-red-300";
            },
            get hasIndividualScores() {
                return this.individuals.length > 0;
            },

            // --- Init ---

            init: function () {
                this._root = this.$el;
                this.quizId = this.$el.getAttribute("data-quiz-id");
                this.isQuizNight = this.$el.getAttribute("data-quiz-night") === "true";
                var tn = this.$el.getAttribute("data-table-number");
                if (tn) this.tableNumber = parseInt(tn, 10);
                var role = this.$el.getAttribute("data-user-role");
                if (role) this.userRole = role;
                // Parse initial tablemates from server
                var tmAttr = this.$el.getAttribute("data-tablemates");
                if (tmAttr) {
                    try {
                        this.tablemates = JSON.parse(tmAttr);
                    } catch (e) {}
                }
                // Check if quiz is already finished (server-side status)
                var initStatus = this.$el.getAttribute("data-quiz-status");
                if (initStatus === "finished") {
                    this.screen = "finished";
                }
                this.connectWebSocket();
                if (this.isQuizNight) {
                    this._renderRoleBadge();
                    this._renderTablemates();
                    this._renderCoachTables();
                    this.fetchAssignment();
                }
            },

            fetchAssignment: function () {
                var self = this;
                fetch("/api/quiz/" + this.quizId + "/my-assignment/", {
                    credentials: "same-origin",
                })
                    .then(function (r) {
                        if (!r.ok) throw r;
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.table_number) self.tableNumber = data.table_number;
                        if (data.role) {
                            self.userRole = data.role;
                            self._renderRoleBadge();
                        }
                        if (data.tablemates) {
                            self.tablemates = data.tablemates;
                            self._renderTablemates();
                        }
                        if (data.personal_score !== undefined)
                            self.personalScore = data.personal_score;
                        if (data.next_table) self.nextTable = data.next_table;
                    })
                    .catch(function () {});
            },

            // --- WebSocket ---

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
                this.ws = new WebSocket(url);

                this.ws.onopen = function () {
                    self.connected = true;
                    self.reconnectAttempts = 0;
                };

                this.ws.onclose = function () {
                    self.connected = false;
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
                    var msg = JSON.parse(event.data);
                    self.handleMessage(msg);
                };
            },

            handleMessage: function (msg) {
                var type = msg.type;
                var data = msg.data;

                if (type === "quiz.state") {
                    if (data.event_type)
                        this.isQuizNight = data.event_type === "quiz_night";
                    if (data.status === "finished") {
                        // Quiz already finished — show finished screen with leaderboard
                        if (data.leaderboard) {
                            this.tables = data.leaderboard.tables || [];
                            this.individuals = data.leaderboard.individuals || [];
                        }
                        this.screen = "finished";
                        this._renderTableLeaderboard();
                        this._renderIndividualLeaderboard();
                    } else if (data.status === "active" && data.question) {
                        this.showQuestion(data);
                    } else if (data.current_round) {
                        this.roundName = data.current_round.title;
                        this.isBonusRound = data.current_round.is_bonus || false;
                    }
                } else if (type === "quiz.question") {
                    this.showQuestion(data);
                } else if (type === "quiz.answer_result") {
                    this.lastResult = data;
                } else if (type === "quiz.leaderboard") {
                    this.tables = data.tables || [];
                    this.individuals = data.individuals || [];
                    this.screen = "leaderboard";
                    this._renderTableLeaderboard();
                    this._renderIndividualLeaderboard();
                } else if (type === "quiz.rotate") {
                    // Defensive: finished state may arrive via quiz.rotate
                    if (data.finished || data.status === "finished") {
                        if (data.leaderboard) {
                            this.tables = data.leaderboard.tables || [];
                            this.individuals = data.leaderboard.individuals || [];
                        }
                        this.screen = "finished";
                        this._renderTableLeaderboard();
                        this._renderIndividualLeaderboard();
                        return;
                    }
                    // Re-fetch assignment to get new table number
                    if (this.isQuizNight) {
                        this.fetchAssignment();
                    }
                    if (data.round_title) this.roundName = data.round_title;
                    if (data.is_bonus !== undefined) this.isBonusRound = data.is_bonus;
                    this.screen = "rotate";
                } else if (type === "quiz.status") {
                    if (data.status === "finished") {
                        if (data.leaderboard) {
                            this.tables = data.leaderboard.tables || [];
                            this.individuals = data.leaderboard.individuals || [];
                        }
                        this.screen = "finished";
                        this._renderTableLeaderboard();
                        this._renderIndividualLeaderboard();
                    } else if (data.status === "round_complete") {
                        this.screen = "waiting";
                    }
                } else if (type === "quiz.table_scored") {
                    // Scoring in progress — no correctness info yet (deferred until all tables scored)
                    this.tableScoredFeedback = "";
                } else if (type === "quiz.reveal_scores") {
                    // All tables scored — reveal correct/incorrect to attendees
                    var results = data.results || [];
                    for (var ri = 0; ri < results.length; ri++) {
                        if (results[ri].table_number === this.tableNumber) {
                            var r = results[ri];
                            this._tableScoredCorrect = r.is_correct;
                            if (r.is_correct) {
                                var pts = r.points_awarded || 0;
                                this.tableScoredFeedback = "+" + pts + " pts!";
                                this.personalScore += pts;
                            } else {
                                this.tableScoredFeedback = "Incorrect";
                            }
                            var self = this;
                            if (this.tableScoredTimer) clearTimeout(this.tableScoredTimer);
                            this.tableScoredTimer = setTimeout(function () {
                                self.tableScoredFeedback = "";
                            }, 3000);
                            break;
                        }
                    }
                } else if (type === "quiz.table_score") {
                    // Legacy table score update
                } else if (type === "quiz.error") {
                    this.showError(data.message || "An error occurred");
                }
            },

            showError: function (message) {
                var self = this;
                this.errorMessage = message;
                if (this.errorTimer) clearTimeout(this.errorTimer);
                this.errorTimer = setTimeout(function () {
                    self.errorMessage = "";
                }, 5000);
            },

            showQuestion: function (data) {
                this.question = data.question || data;
                this.choices = this.question.choices || [];
                this.questionIndex = data.index || 0;
                this.questionTotal = data.total || 0;
                this.countdownTotal = data.time || 30;
                if (data.time_remaining !== undefined) {
                    this.countdown = data.time_remaining;
                } else {
                    this.countdown = this.countdownTotal;
                }
                this.selectedIndex = null;
                this.answered = false;
                this.lastResult = null;
                this.isBonusRound = data.is_bonus || false;
                this.screen = "question";
                this._renderChoices();
                this.startCountdown();
            },

            startCountdown: function () {
                var self = this;
                if (this.countdownTimer) clearInterval(this.countdownTimer);
                this.countdownTimer = setInterval(function () {
                    self.countdown--;
                    if (self.countdown <= 0) {
                        clearInterval(self.countdownTimer);
                        self.countdownTimer = null;
                    }
                }, 1000);
            },

            // --- User actions (legacy, non-quiz-night only) ---

            selectAnswerFromEl: function () {
                if (this.answered) return;
                var index = parseInt(this.$el.getAttribute("data-choice-index"), 10);
                this.selectedIndex = index;
                this._updateChoiceButtons();
            },

            submitAnswer: function () {
                if (this.answered || this.selectedIndex === null || !this.question)
                    return;
                this.answered = true;

                var choice = this.choices[this.selectedIndex];
                this.ws.send(
                    JSON.stringify({
                        action: "table_answer",
                        question_id: this.question.id,
                        answer: choice.text,
                    }),
                );
            },

            _updateChoiceButtons: function () {
                var buttons = this._root.querySelectorAll(".quiz-choice-btn");
                for (var i = 0; i < buttons.length; i++) {
                    var idx = parseInt(
                        buttons[i].getAttribute("data-choice-index"),
                        10,
                    );
                    // Reset
                    buttons[i].className = buttons[i].className
                        .replace(/bg-\S+|ring-\S+/g, "")
                        .trim();
                    var cls = "bg-slate-700";
                    if (!this.isQuizNight) {
                        if (this.answered && this.selectedIndex === idx) {
                            if (this.lastResult && this.lastResult.is_correct) {
                                cls = "bg-green-700 ring-2 ring-green-400";
                            } else if (this.lastResult && !this.lastResult.is_correct) {
                                cls = "bg-red-700 ring-2 ring-red-400";
                            } else {
                                cls = "bg-crush-purple ring-2 ring-crush-pink";
                            }
                        } else if (this.selectedIndex === idx) {
                            cls = "bg-crush-purple/50 ring-2 ring-crush-pink";
                        }
                    }
                    var parts = cls.split(" ");
                    for (var j = 0; j < parts.length; j++) {
                        buttons[i].classList.add(parts[j]);
                    }
                }
            },

            // --- DOM rendering (CSP-safe replacements for x-for) ---

            _renderChoices: function () {
                var container = this.$refs.choices;
                if (!container) return;
                container.innerHTML = "";
                var self = this;
                for (var i = 0; i < this.choices.length; i++) {
                    var btn = document.createElement("button");
                    btn.setAttribute("data-choice-index", String(i));
                    btn.className =
                        "quiz-choice-btn w-full rounded-xl bg-slate-700 p-4 text-left text-white transition-all";
                    btn.textContent = this.choices[i].text;
                    btn.addEventListener("click", function () {
                        if (self.answered) return;
                        var index = parseInt(
                            this.getAttribute("data-choice-index"),
                            10,
                        );
                        self.selectedIndex = index;
                        self._updateChoiceButtons();
                    });
                    container.appendChild(btn);
                }
            },

            _renderRoleBadge: function () {
                var container = this.$refs.rolebadge;
                if (!container) return;
                container.innerHTML = "";
                if (!this.userRole) return;
                var badge = document.createElement("span");
                if (this.userRole === "anchor") {
                    badge.className =
                        "inline-flex items-center gap-1 rounded-full bg-blue-900/40 px-3 py-1 text-xs font-medium text-blue-300";
                    badge.textContent = "\u{1F4CC} Anchor \u2013 stay here";
                } else {
                    badge.className =
                        "inline-flex items-center gap-1 rounded-full bg-amber-900/40 px-3 py-1 text-xs font-medium text-amber-300";
                    badge.textContent = "\u{1F504} Rotator \u2013 you move!";
                }
                container.appendChild(badge);
            },

            _renderTablemates: function () {
                var container = this.$refs.tablemates;
                if (!container) return;
                container.innerHTML = "";
                if (this.tablemates.length === 0) return;

                var label = document.createElement("p");
                label.className =
                    "mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500";
                label.textContent = "Your tablemates";
                container.appendChild(label);

                var list = document.createElement("div");
                list.className = "space-y-1";
                for (var i = 0; i < this.tablemates.length; i++) {
                    var m = this.tablemates[i];
                    var row = document.createElement("div");
                    row.className =
                        "flex items-center justify-between rounded-lg bg-slate-700/40 px-3 py-2";

                    var nameSpan = document.createElement("span");
                    nameSpan.className = "text-sm text-gray-200";
                    nameSpan.textContent = m.display_name;
                    row.appendChild(nameSpan);

                    if (m.role) {
                        var roleBadge = document.createElement("span");
                        if (m.role === "anchor") {
                            roleBadge.className =
                                "rounded-full bg-blue-900/30 px-2 py-0.5 text-xs text-blue-400";
                            roleBadge.textContent = "\u{1F4CC} Anchor";
                        } else {
                            roleBadge.className =
                                "rounded-full bg-amber-900/30 px-2 py-0.5 text-xs text-amber-400";
                            roleBadge.textContent = "\u{1F504} Rotator";
                        }
                        row.appendChild(roleBadge);
                    }

                    list.appendChild(row);
                }
                container.appendChild(list);
            },

            _renderTableLeaderboard: function () {
                var container = this.$refs.tableboard;
                if (!container) return;
                container.innerHTML = "";
                for (var i = 0; i < this.tables.length; i++) {
                    var row = document.createElement("div");
                    row.className =
                        "flex items-center justify-between rounded-xl bg-slate-800 p-4";

                    var left = document.createElement("div");
                    left.className = "flex items-center gap-3";

                    var rank = document.createElement("span");
                    rank.className =
                        "flex h-8 w-8 items-center justify-center rounded-full bg-crush-purple/30 text-sm font-bold text-crush-purple";
                    rank.textContent = "#" + (i + 1);

                    var label = document.createElement("span");
                    label.className = "font-medium text-white";
                    label.textContent = "Table " + this.tables[i].table_number;

                    left.appendChild(rank);
                    left.appendChild(label);

                    var score = document.createElement("span");
                    score.className = "font-bold text-crush-pink";
                    score.textContent = this.tables[i].total_score + " pts";

                    row.appendChild(left);
                    row.appendChild(score);
                    container.appendChild(row);
                }
            },

            _renderIndividualLeaderboard: function () {
                var container = this.$refs.playerboard;
                if (!container) return;
                container.innerHTML = "";
                for (var i = 0; i < this.individuals.length; i++) {
                    var row = document.createElement("div");
                    row.className =
                        "flex items-center justify-between rounded-lg bg-slate-800/50 px-4 py-2";

                    var name = document.createElement("span");
                    name.className = "text-sm text-gray-300";
                    name.textContent = this.individuals[i].display_name;

                    var score = document.createElement("span");
                    score.className = "text-sm font-medium text-crush-pink";
                    score.textContent = this.individuals[i].total_score + " pts";

                    row.appendChild(name);
                    row.appendChild(score);
                    container.appendChild(row);
                }
            },

            _renderCoachTables: function () {
                var container = this.$refs.coachtables;
                if (!container) return;
                var dataAttr = container.getAttribute("data-all-tables");
                if (!dataAttr) return;
                var allTables;
                try {
                    allTables = JSON.parse(dataAttr);
                } catch (e) {
                    return;
                }
                container.innerHTML = "";
                for (var i = 0; i < allTables.length; i++) {
                    var t = allTables[i];
                    var card = document.createElement("div");
                    card.className =
                        "rounded-lg bg-slate-700/50 p-3 ring-1 ring-white/5";

                    var header = document.createElement("div");
                    header.className = "mb-2 flex items-center justify-between";

                    var titleWrap = document.createElement("div");
                    titleWrap.className = "flex items-center gap-2";

                    var numBadge = document.createElement("span");
                    numBadge.className =
                        "flex h-8 w-8 items-center justify-center rounded-lg bg-crush-purple/20 text-sm font-bold text-crush-purple";
                    numBadge.textContent = "T" + t.table_number;

                    var title = document.createElement("span");
                    title.className = "font-semibold text-white";
                    title.textContent = "Table " + t.table_number;

                    titleWrap.appendChild(numBadge);
                    titleWrap.appendChild(title);

                    var scoreBadge = document.createElement("span");
                    scoreBadge.className =
                        "rounded-full bg-crush-pink/20 px-2 py-0.5 text-xs font-medium text-crush-pink";
                    scoreBadge.textContent = t.total_score + " pts";

                    header.appendChild(titleWrap);
                    header.appendChild(scoreBadge);
                    card.appendChild(header);

                    var memberList = document.createElement("div");
                    memberList.className = "space-y-1";

                    for (var j = 0; j < t.members.length; j++) {
                        var m = t.members[j];
                        var memberRow = document.createElement("div");
                        memberRow.className =
                            "flex items-center justify-between text-sm";

                        var nameSpan = document.createElement("span");
                        nameSpan.className = "text-gray-300";
                        nameSpan.textContent = m.display_name;
                        memberRow.appendChild(nameSpan);

                        if (m.role) {
                            var roleBadge = document.createElement("span");
                            if (m.role === "anchor") {
                                roleBadge.className =
                                    "rounded-full bg-blue-900/40 px-2 py-0.5 text-xs text-blue-300";
                                roleBadge.textContent = "\u{1F4CC} Anchor";
                            } else {
                                roleBadge.className =
                                    "rounded-full bg-amber-900/40 px-2 py-0.5 text-xs text-amber-300";
                                roleBadge.textContent = "\u{1F504} Rotator";
                            }
                            memberRow.appendChild(roleBadge);
                        }

                        memberList.appendChild(memberRow);
                    }

                    card.appendChild(memberList);
                    container.appendChild(card);
                }
            },

            // Cleanup
            destroy: function () {
                if (this.countdownTimer) clearInterval(this.countdownTimer);
                if (this.tableScoredTimer) clearTimeout(this.tableScoredTimer);
                if (this.ws) this.ws.close();
            },
        };
    });

    // ========================================================================
    // HOST COMPONENT (formerly quizCoach)
    // ========================================================================
    Alpine.data("quizHost", function () {
        return {
            // Connection
            ws: null,
            connected: false,
            quizId: null,
            reconnectAttempts: 0,

            // State
            status: "draft",
            isQuizNight: false,
            selectedRoundId: null,
            currentQuestion: null,
            currentRound: null,
            questionIdx: 0,
            questionCount: 0,
            isBonusRound: false,
            roundComplete: false,

            // Guided flow: round list with statuses
            rounds: [], // [{ id, title, sort_order, is_bonus, question_count, status: 'done'|'current'|'upcoming' }]

            // Scoring
            tableCount: 0,
            scoredTables: {}, // { tableId: "pending"|"scored"|true|false }
            scoringQuestionId: null,
            scoredCount: 0,
            totalTables: 0,

            // Table overview (who sits where)
            tableMembers: [], // [{ table_number, members: [{display_name, role}], total_score }]

            // Leaderboard
            tables: [],

            // Error / feedback
            errorMessage: "",
            errorTimer: null,
            regenerating: false,

            // --- Getters ---

            get hasError() {
                return this.errorMessage !== "";
            },
            get isRegenerating() {
                return this.regenerating;
            },
            get isNotRegenerating() {
                return !this.regenerating;
            },
            get isConnected() {
                return this.connected;
            },
            get isDisconnected() {
                return !this.connected;
            },
            get showQuizNight() {
                return this.isQuizNight;
            },
            get canStart() {
                return this.status === "draft";
            },
            get canPause() {
                return this.status === "active";
            },
            get canEnd() {
                return this.status === "active" || this.status === "paused";
            },
            get isFinished() {
                return this.status === "finished";
            },
            get isRoundComplete() {
                return this.roundComplete;
            },
            get canRotate() {
                return this.isQuizNight && this.roundComplete;
            },
            get showNextQuestion() {
                return !this.roundComplete && !this.isFinished;
            },
            get hasCurrentQuestion() {
                return this.currentQuestion !== null;
            },
            get hasLeaderboard() {
                return this.tables.length > 0;
            },
            get scoringProgress() {
                return this.scoredCount + " / " + this.totalTables;
            },
            get showScoringGrid() {
                return this.isQuizNight && this.hasCurrentQuestion;
            },
            get hasTables() {
                return this.totalTables > 0;
            },
            get isBonusLabel() {
                return this.isBonusRound ? "BONUS x2" : "";
            },
            get hasBonusRound() {
                return this.isBonusRound;
            },

            get statusText() {
                var map = {
                    draft: "Draft",
                    active: "Active",
                    paused: "Paused",
                    finished: "Finished",
                };
                return map[this.status] || this.status;
            },
            get statusBadgeClass() {
                if (this.status === "active") return "bg-green-900/50 text-green-300";
                if (this.status === "paused") return "bg-amber-900/50 text-amber-300";
                if (this.status === "finished") return "bg-slate-700 text-gray-400";
                return "bg-slate-700 text-gray-400";
            },
            get statusDotClass() {
                if (this.status === "active") return "bg-green-400";
                if (this.status === "paused") return "bg-amber-400";
                return "bg-gray-400";
            },
            get currentQuestionText() {
                return this.currentQuestion ? this.currentQuestion.text : "";
            },
            get currentQuestionType() {
                return this.currentQuestion ? this.currentQuestion.question_type : "";
            },
            get currentRoundTitle() {
                return this.currentRound ? this.currentRound.title : "";
            },
            get questionProgress() {
                if (!this.questionCount) return "";
                return "Q" + (this.questionIdx + 1) + " / " + this.questionCount;
            },
            get correctAnswerText() {
                if (!this.currentQuestion) return "";
                // For multiple choice / true false
                if (this.currentQuestion.choices_with_answers) {
                    var correct = [];
                    for (
                        var i = 0;
                        i < this.currentQuestion.choices_with_answers.length;
                        i++
                    ) {
                        var c = this.currentQuestion.choices_with_answers[i];
                        if (c.is_correct) correct.push(c.text);
                    }
                    return correct.join(", ");
                }
                // For open ended
                if (this.currentQuestion.correct_answer) {
                    return this.currentQuestion.correct_answer;
                }
                return "";
            },
            get hasCorrectAnswer() {
                return this.correctAnswerText !== "";
            },
            // --- Init ---

            init: function () {
                this._root = this.$el;
                this.quizId = this.$el.getAttribute("data-quiz-id");
                this.isQuizNight = this.$el.getAttribute("data-quiz-night") === "true";
                var tc = this.$el.getAttribute("data-table-count");
                if (tc) this.tableCount = parseInt(tc, 10);
                // Parse initial table members from server
                var tmAttr = this.$el.getAttribute("data-table-members");
                if (tmAttr) {
                    try {
                        this.tableMembers = JSON.parse(tmAttr);
                    } catch (e) {}
                }
                this.connectWebSocket();
                this._renderTableOverview();
            },

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
                this.ws = new WebSocket(url);

                this.ws.onopen = function () {
                    self.connected = true;
                    self.reconnectAttempts = 0;
                };

                this.ws.onclose = function () {
                    self.connected = false;
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
                    var msg = JSON.parse(event.data);
                    self.handleMessage(msg);
                };
            },

            handleMessage: function (msg) {
                var type = msg.type;
                var data = msg.data;

                if (type === "quiz.state") {
                    this.status = data.status;
                    if (data.event_type)
                        this.isQuizNight = data.event_type === "quiz_night";
                    if (data.current_round) {
                        this.currentRound = data.current_round;
                        this.selectedRoundId = data.current_round.id;
                        this.isBonusRound = data.current_round.is_bonus || false;
                    } else {
                        this.currentRound = null;
                    }
                    if (data.rounds) {
                        this.rounds = data.rounds;
                        this._renderRoundButtons();
                    }
                    if (data.question) {
                        this.currentQuestion = data.question;
                        this.roundComplete = false;
                    } else {
                        this.currentQuestion = null;
                        // Round complete only if active AND we've gone past index 0
                        // (index -1 or 0 means no questions asked yet)
                        this.roundComplete =
                            data.status === "active" && data.question_index > 0;
                    }
                    this.questionIdx = data.question_index || 0;
                } else if (type === "quiz.question") {
                    this.currentQuestion = data;
                    this.questionIdx = data.index || 0;
                    this.questionCount = data.total || 0;
                    this.isBonusRound = data.is_bonus || false;
                    this.status = "active";
                    this.roundComplete = false;
                    // Reset scoring state for new question
                    this.scoredTables = {};
                    this.scoredCount = 0;
                    this.scoringQuestionId = data.id;
                    // Re-enable table buttons for the new question
                    this._updateTableButtons();
                } else if (type === "quiz.leaderboard") {
                    this.tables = data.tables || [];
                    this._renderTableLeaderboard();
                    // Update scores in table overview
                    this._updateTableScores();
                } else if (type === "quiz.status") {
                    if (data.status === "round_complete") {
                        this.currentQuestion = null;
                        this.roundComplete = true;
                        // Mark current round as done in guided flow
                        this._markCurrentRoundDone();
                    } else if (data.status) {
                        this.status = data.status;
                    }
                    if (data.current_round) {
                        this.currentRound = data.current_round;
                        this.selectedRoundId = data.current_round.id;
                        this.isBonusRound = data.current_round.is_bonus || false;
                        this._updateRoundCurrent(data.current_round.id);
                    }
                } else if (type === "quiz.rotate") {
                    // Defensive: finished state may arrive via quiz.rotate
                    if (data.finished || data.status === "finished") {
                        this.status = "finished";
                        return;
                    }
                    // Rebuild table overview from rotation assignments
                    if (data.assignments) {
                        this._rebuildTableMembersFromAssignments(data.assignments);
                        this._renderTableOverview();
                    }
                    if (data.round_title && data.is_bonus !== undefined) {
                        this.isBonusRound = data.is_bonus;
                    }
                    this.roundComplete = false;
                    this.currentQuestion = null;
                    // Update rounds to reflect the new current round from rotation
                    if (data.round_number !== undefined) {
                        this._advanceRoundsByNumber(data.round_number);
                    }
                } else if (type === "quiz.table_scored") {
                    // Track which tables have been scored (no correctness info yet)
                    if (data.table_id) {
                        this.scoredTables[data.table_id] = "scored";
                        if (data.scored_count !== undefined) this.scoredCount = data.scored_count;
                        if (data.total_tables !== undefined) this.totalTables = data.total_tables;
                        this._updateTableButtons();
                    }
                } else if (type === "quiz.reveal_scores") {
                    // All tables scored — reveal correct/incorrect
                    var results = data.results || [];
                    for (var i = 0; i < results.length; i++) {
                        this.scoredTables[results[i].table_id] = results[i].is_correct;
                    }
                    this._updateTableButtons();
                } else if (type === "quiz.error") {
                    this.showError(data.message || "An error occurred");
                }
            },

            showError: function (message) {
                var self = this;
                this.errorMessage = message;
                if (this.errorTimer) clearTimeout(this.errorTimer);
                this.errorTimer = setTimeout(function () {
                    self.errorMessage = "";
                }, 5000);
            },

            // --- Host actions ---

            regenerateTables: function () {
                if (this.regenerating) return;
                var self = this;
                this.regenerating = true;
                fetch("/api/quiz/" + this.quizId + "/regenerate-tables/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this._getCsrfToken(),
                    },
                })
                    .then(function (resp) {
                        return resp.json().then(function (d) {
                            return { ok: resp.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.regenerating = false;
                        if (!result.ok) {
                            self.showError(result.data.error || "Regeneration failed");
                            return;
                        }
                        var d = result.data;
                        // Refresh table overview by refetching assignment data
                        self._fetchTableOverview();
                        var msg =
                            d.num_tables +
                            " tables: " +
                            d.anchors +
                            " anchors + " +
                            d.rotators +
                            " rotators";
                        if (d.warnings && d.warnings.length) {
                            msg += ". Warnings: " + d.warnings.join("; ");
                        }
                        self.showError(msg); // Reuse error banner for feedback
                    })
                    .catch(function () {
                        self.regenerating = false;
                        self.showError("Network error during regeneration");
                    });
            },

            _getCsrfToken: function () {
                // Hidden form input first (works with CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                // Fallback to cookie
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },

            _fetchTableOverview: function () {
                var self = this;
                fetch("/api/quiz/" + this.quizId + "/tables/?round=0")
                    .then(function (resp) {
                        return resp.json();
                    })
                    .then(function (data) {
                        self.tableMembers = data;
                        self._renderTableOverview();
                    })
                    .catch(function () {});
            },

            nextQuestion: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "next_question" }));
                }
            },

            showLeaderboard: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "show_leaderboard" }));
                }
            },

            triggerRotate: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "rotate" }));
                    this.roundComplete = false;
                    this.currentQuestion = null;
                }
            },

            startQuiz: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "start_quiz" }));
                }
            },

            pauseQuiz: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "pause_quiz" }));
                }
            },

            endQuiz: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: "end_quiz" }));
                }
            },

            // --- Table scoring (quiz night) ---

            _scoreTable: function (tableId, isCorrect) {
                if (!this.ws || !this.connected || !this.currentQuestion) return;
                // Prevent re-scoring a table that has already been scored
                if (this.scoredTables[tableId] !== undefined) return;
                this.ws.send(
                    JSON.stringify({
                        action: "score_table",
                        table_id: tableId,
                        question_id: this.currentQuestion.id,
                        is_correct: isCorrect,
                    }),
                );
                this.scoredTables[tableId] = "pending";
                this._updateTableButtons();
            },

            scoreTableCorrectFromEl: function () {
                var tid = parseInt(this.$el.getAttribute("data-table-id"), 10);
                this._scoreTable(tid, true);
            },

            scoreTableWrongFromEl: function () {
                var tid = parseInt(this.$el.getAttribute("data-table-id"), 10);
                this._scoreTable(tid, false);
            },

            scoreAllCorrect: function () {
                var root = this._root;
                var tableEls = root.querySelectorAll(".quiz-table-btn[data-table-id]");
                for (var i = 0; i < tableEls.length; i++) {
                    var tid = parseInt(tableEls[i].getAttribute("data-table-id"), 10);
                    if (this.scoredTables[tid] === undefined) {
                        this._scoreTable(tid, true);
                    }
                }
            },

            // clearScoring removed — scoring is locked after reveal

            _updateTableButtons: function () {
                var root = this._root;
                var buttons = root.querySelectorAll(".quiz-table-btn[data-table-id]");
                for (var i = 0; i < buttons.length; i++) {
                    var tid = parseInt(buttons[i].getAttribute("data-table-id"), 10);
                    buttons[i].className = buttons[i].className
                        .replace(/bg-\S+/g, "")
                        .replace(/ring-\S+/g, "")
                        .replace(/text-\S+/g, "")
                        .replace(/hover:\S+/g, "")
                        .replace(/opacity-\S+/g, "")
                        .replace(/cursor-\S+/g, "")
                        .replace(/\s+/g, " ")
                        .trim();
                    var state = this.scoredTables[tid];
                    var cls;
                    if (state === true) {
                        cls = "bg-green-700 ring-2 ring-green-400 text-white opacity-60 cursor-not-allowed";
                    } else if (state === false) {
                        cls = "bg-red-700 ring-2 ring-red-400 text-white opacity-60 cursor-not-allowed";
                    } else if (state === "pending" || state === "scored") {
                        cls = "bg-amber-700 ring-2 ring-amber-400 text-white opacity-60 cursor-not-allowed";
                    } else {
                        cls = "bg-slate-700 text-gray-300 hover:bg-slate-600";
                    }
                    var parts = cls.split(" ");
                    for (var j = 0; j < parts.length; j++) {
                        buttons[i].classList.add(parts[j]);
                    }
                    // Disable buttons for already-scored tables
                    buttons[i].disabled = state !== undefined;
                }
                // Also disable the wrong (✗) buttons for scored tables
                var wrongBtns = root.querySelectorAll("button[x-on\\:click='scoreTableWrongFromEl'][data-table-id]");
                for (var k = 0; k < wrongBtns.length; k++) {
                    var wtid = parseInt(wrongBtns[k].getAttribute("data-table-id"), 10);
                    wrongBtns[k].disabled = this.scoredTables[wtid] !== undefined;
                    if (wrongBtns[k].disabled) {
                        wrongBtns[k].classList.add("opacity-30", "cursor-not-allowed");
                    } else {
                        wrongBtns[k].classList.remove("opacity-30", "cursor-not-allowed");
                    }
                }
            },

            selectRoundFromEl: function () {
                var roundId = parseInt(this.$el.getAttribute("data-round-id"), 10);
                // Prevent re-selecting completed rounds
                for (var i = 0; i < this.rounds.length; i++) {
                    if (this.rounds[i].id === roundId && this.rounds[i].status === "done") {
                        return;
                    }
                }
                this.selectedRoundId = roundId;
                this.roundComplete = false;
                this.currentQuestion = null;
                if (this.ws && this.connected) {
                    this.ws.send(
                        JSON.stringify({
                            action: "set_round",
                            round_id: roundId,
                        }),
                    );
                }
                this._updateRoundButtons();
            },

            // --- Guided flow: round status tracking ---

            _markCurrentRoundDone: function () {
                for (var i = 0; i < this.rounds.length; i++) {
                    if (this.rounds[i].status === "current") {
                        this.rounds[i].status = "done";
                        break;
                    }
                }
                this._renderRoundButtons();
            },

            _updateRoundCurrent: function (roundId) {
                for (var i = 0; i < this.rounds.length; i++) {
                    if (this.rounds[i].id === roundId) {
                        this.rounds[i].status = "current";
                    }
                }
                this._renderRoundButtons();
            },

            _advanceRoundsByNumber: function (roundNumber) {
                // After rotation: mark all rounds before roundNumber as done,
                // the round at roundNumber as current
                for (var i = 0; i < this.rounds.length; i++) {
                    if (i < roundNumber) {
                        this.rounds[i].status = "done";
                    } else if (i === roundNumber) {
                        this.rounds[i].status = "current";
                        this.selectedRoundId = this.rounds[i].id;
                        this.currentRound = {
                            id: this.rounds[i].id,
                            title: this.rounds[i].title,
                            is_bonus: this.rounds[i].is_bonus,
                        };
                    } else {
                        this.rounds[i].status = "upcoming";
                    }
                }
                this._renderRoundButtons();
            },

            _renderRoundButtons: function () {
                var root = this._root;
                var buttons = root.querySelectorAll(".quiz-round-btn[data-round-id]");
                for (var i = 0; i < buttons.length; i++) {
                    var rid = parseInt(buttons[i].getAttribute("data-round-id"), 10);
                    // Find round info
                    var roundInfo = null;
                    for (var j = 0; j < this.rounds.length; j++) {
                        if (this.rounds[j].id === rid) {
                            roundInfo = this.rounds[j];
                            break;
                        }
                    }
                    var status = roundInfo ? roundInfo.status : "upcoming";

                    // Reset classes on button
                    buttons[i].className = buttons[i].className
                        .replace(/bg-\S+/g, "")
                        .replace(/ring-\S+/g, "")
                        .replace(/text-\S+/g, "")
                        .replace(/hover:\S+/g, "")
                        .replace(/border-\S+/g, "")
                        .replace(/opacity-\S+/g, "")
                        .replace(/cursor-\S+/g, "")
                        .replace(/\s+/g, " ")
                        .trim();

                    var cls;
                    if (status === "current") {
                        cls = "bg-crush-purple/30 text-white ring-2 ring-crush-purple";
                    } else if (status === "done") {
                        cls = "bg-green-900/30 text-green-300 ring-1 ring-green-700 cursor-not-allowed opacity-50";
                    } else {
                        cls = "bg-slate-700 text-gray-300 hover:bg-slate-600";
                    }
                    var parts = cls.split(" ");
                    for (var k = 0; k < parts.length; k++) {
                        buttons[i].classList.add(parts[k]);
                    }

                    // Update round number circle styling
                    var numCircle = buttons[i].querySelector(".quiz-round-number");
                    if (numCircle) {
                        numCircle.className = numCircle.className
                            .replace(/bg-\S+/g, "")
                            .replace(/text-\S+/g, "")
                            .replace(/\s+/g, " ")
                            .trim();
                        var numCls;
                        if (status === "current") {
                            numCls = "bg-crush-purple text-white";
                        } else if (status === "done") {
                            numCls = "bg-green-700 text-green-200";
                        } else {
                            numCls = "bg-slate-600 text-gray-400";
                        }
                        var numParts = numCls.split(" ");
                        for (var m = 0; m < numParts.length; m++) {
                            numCircle.classList.add(numParts[m]);
                        }
                    }

                    // Update or create status badge
                    var badge = buttons[i].querySelector(".round-status-badge");
                    if (!badge) {
                        badge = document.createElement("span");
                        badge.className =
                            "round-status-badge text-xs font-semibold px-2 py-0.5 rounded-full";
                        var flexRow = buttons[i].querySelector(".flex.items-center.justify-between");
                        if (flexRow) {
                            flexRow.appendChild(badge);
                        }
                    }
                    if (status === "current") {
                        badge.textContent = "\u25B6 NOW";
                        badge.className =
                            "round-status-badge text-xs font-semibold px-2 py-0.5 rounded-full bg-crush-purple text-white";
                    } else if (status === "done") {
                        badge.textContent = "\u2713 DONE";
                        badge.className =
                            "round-status-badge text-xs font-semibold px-2 py-0.5 rounded-full bg-green-700 text-green-200";
                    } else {
                        badge.textContent = "";
                        badge.className = "round-status-badge hidden";
                    }
                }
                // Update progress bar and counter
                this._updateRoundProgress();
            },

            _updateRoundButtons: function () {
                // Alias for backward compat — delegates to guided flow renderer
                this._renderRoundButtons();
            },

            _updateRoundProgress: function () {
                var root = this._root;
                var total = this.rounds.length;
                var done = 0;
                for (var i = 0; i < this.rounds.length; i++) {
                    if (this.rounds[i].status === "done") done++;
                }
                // Update progress text
                var progressText = root.querySelector(".quiz-round-progress-text");
                if (progressText) {
                    progressText.textContent = done + " / " + total + " complete";
                }
                // Update progress bar width
                var progressBar = root.querySelector(".quiz-round-progress-bar");
                if (progressBar) {
                    var pct = total > 0 ? Math.round((done / total) * 100) : 0;
                    progressBar.style.width = pct + "%";
                }
            },

            // --- DOM rendering (CSP-safe replacement for x-for) ---

            _renderTableLeaderboard: function () {
                var container = this.$refs.tableboard;
                if (!container) return;
                container.innerHTML = "";
                for (var i = 0; i < this.tables.length; i++) {
                    var row = document.createElement("div");
                    row.className =
                        "flex items-center justify-between rounded-lg bg-slate-700/50 px-4 py-2";

                    var left = document.createElement("div");
                    left.className = "flex items-center gap-2";

                    var rank = document.createElement("span");
                    rank.className = "text-sm font-bold text-crush-purple";
                    rank.textContent = "#" + (i + 1);

                    var label = document.createElement("span");
                    label.className = "text-sm text-white";
                    label.textContent = "Table " + this.tables[i].table_number;

                    left.appendChild(rank);
                    left.appendChild(label);

                    var score = document.createElement("span");
                    score.className = "text-sm font-medium text-crush-pink";
                    score.textContent = this.tables[i].total_score + " pts";

                    row.appendChild(left);
                    row.appendChild(score);
                    container.appendChild(row);
                }
            },

            _updateTableScores: function () {
                // Sync scores from leaderboard data into tableMembers
                for (var i = 0; i < this.tables.length; i++) {
                    var tn = this.tables[i].table_number;
                    for (var j = 0; j < this.tableMembers.length; j++) {
                        if (this.tableMembers[j].table_number === tn) {
                            this.tableMembers[j].total_score =
                                this.tables[i].total_score;
                            break;
                        }
                    }
                }
                this._renderTableOverview();
            },

            _rebuildTableMembersFromAssignments: function (assignments) {
                // assignments = { userId: { table_number, role, display_name } }
                var byTable = {};
                for (var uid in assignments) {
                    var a = assignments[uid];
                    var tn = a.table_number;
                    if (!byTable[tn])
                        byTable[tn] = { table_number: tn, members: [], total_score: 0 };
                    byTable[tn].members.push({
                        display_name: a.display_name,
                        role: a.role,
                    });
                }
                // Preserve scores from current tableMembers
                for (var i = 0; i < this.tableMembers.length; i++) {
                    var tm = this.tableMembers[i];
                    if (byTable[tm.table_number]) {
                        byTable[tm.table_number].total_score = tm.total_score;
                    }
                }
                // Sort by table number
                var result = [];
                var keys = Object.keys(byTable).sort(function (a, b) {
                    return a - b;
                });
                for (var k = 0; k < keys.length; k++) {
                    result.push(byTable[keys[k]]);
                }
                this.tableMembers = result;
            },

            _renderTableOverview: function () {
                var container = this.$refs.tableoverview;
                if (!container) return;
                container.innerHTML = "";
                for (var i = 0; i < this.tableMembers.length; i++) {
                    var t = this.tableMembers[i];
                    var card = document.createElement("div");
                    card.className =
                        "rounded-lg bg-slate-700/50 p-3 ring-1 ring-white/5";

                    // Header: table number + score
                    var header = document.createElement("div");
                    header.className = "mb-2 flex items-center justify-between";

                    var titleWrap = document.createElement("div");
                    titleWrap.className = "flex items-center gap-2";

                    var numBadge = document.createElement("span");
                    numBadge.className =
                        "flex h-8 w-8 items-center justify-center rounded-lg bg-crush-purple/20 text-sm font-bold text-crush-purple";
                    numBadge.textContent = "T" + t.table_number;

                    var title = document.createElement("span");
                    title.className = "font-semibold text-white";
                    title.textContent = "Table " + t.table_number;

                    titleWrap.appendChild(numBadge);
                    titleWrap.appendChild(title);

                    var scoreBadge = document.createElement("span");
                    scoreBadge.className =
                        "rounded-full bg-crush-pink/20 px-2 py-0.5 text-xs font-medium text-crush-pink";
                    scoreBadge.textContent = t.total_score + " pts";

                    header.appendChild(titleWrap);
                    header.appendChild(scoreBadge);
                    card.appendChild(header);

                    // Members list
                    var memberList = document.createElement("div");
                    memberList.className = "space-y-1";

                    for (var j = 0; j < t.members.length; j++) {
                        var m = t.members[j];
                        var memberRow = document.createElement("div");
                        memberRow.className =
                            "flex items-center justify-between text-sm";

                        var nameSpan = document.createElement("span");
                        nameSpan.className = "text-gray-300";
                        nameSpan.textContent = m.display_name;

                        memberRow.appendChild(nameSpan);

                        if (m.role) {
                            var roleBadge = document.createElement("span");
                            if (m.role === "anchor") {
                                roleBadge.className =
                                    "rounded-full bg-blue-900/40 px-2 py-0.5 text-xs text-blue-300";
                                roleBadge.textContent = "\u{1F4CC} Anchor";
                            } else {
                                roleBadge.className =
                                    "rounded-full bg-amber-900/40 px-2 py-0.5 text-xs text-amber-300";
                                roleBadge.textContent = "\u{1F504} Rotator";
                            }
                            memberRow.appendChild(roleBadge);
                        }

                        memberList.appendChild(memberRow);
                    }

                    if (t.members.length === 0) {
                        var emptyMsg = document.createElement("p");
                        emptyMsg.className = "text-xs italic text-gray-500";
                        emptyMsg.textContent = "No members assigned";
                        memberList.appendChild(emptyMsg);
                    }

                    card.appendChild(memberList);
                    container.appendChild(card);
                }
            },

            // Cleanup
            destroy: function () {
                if (this.ws) this.ws.close();
            },
        };
    });
});
