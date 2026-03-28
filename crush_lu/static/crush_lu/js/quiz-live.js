/**
 * Live Quiz Alpine.js CSP-compliant components.
 *
 * Two components:
 *   quizLive  – attendee view (table info, question display, leaderboard, rotate)
 *   quizHost  – host control panel (next question, score tables, rotate, leaderboard)
 *
 * Both use the native WebSocket API – no external libraries.
 */
document.addEventListener('alpine:init', function () {

    // ========================================================================
    // ATTENDEE COMPONENT
    // ========================================================================
    Alpine.data('quizLive', function () {
        return {
            // Connection
            ws: null,
            connected: false,
            quizId: null,
            reconnectAttempts: 0,

            // Mode
            isQuizNight: false,

            // State
            screen: 'waiting', // waiting | question | leaderboard | rotate
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
            userRole: '',
            tableScoredFeedback: '',
            tableScoredTimer: null,
            _tableScoredCorrect: true,

            // Leaderboard
            tables: [],
            individuals: [],

            // Round info
            roundName: '',
            isBonusRound: false,

            // --- Getters (CSP-safe computed properties) ---

            get isWaiting() { return this.screen === 'waiting'; },
            get isQuestion() { return this.screen === 'question'; },
            get isLeaderboard() { return this.screen === 'leaderboard'; },
            get isRotate() { return this.screen === 'rotate'; },
            get isConnected() { return this.connected; },
            get isDisconnected() { return !this.connected; },
            get hasAnswered() { return this.answered; },
            get hasSelected() { return this.selectedIndex !== null; },
            get showAnswerControls() { return !this.isQuizNight; },
            get hasTableInfo() { return this.isQuizNight && this.tableNumber > 0; },
            get hasNextTable() { return this.nextTable !== null; },
            get hasTableScoredFeedback() { return this.tableScoredFeedback !== ''; },

            get questionText() {
                return this.question ? this.question.text : '';
            },
            get pointsLabel() {
                if (!this.question) return '';
                var pts = this.question.points + ' pts';
                if (this.isBonusRound) return pts + ' (x2 BONUS)';
                return pts;
            },
            get roundTitle() { return this.roundName; },
            get questionProgress() {
                if (!this.questionTotal) return '';
                return (this.questionIndex + 1) + ' / ' + this.questionTotal;
            },
            get countdownDisplay() {
                return this.countdown + 's';
            },
            get timerBarStyle() {
                var pct = this.countdownTotal > 0
                    ? (this.countdown / this.countdownTotal) * 100
                    : 0;
                return 'width: ' + pct + '%';
            },
            get feedbackClass() {
                if (!this.lastResult) return 'bg-slate-700';
                return this.lastResult.is_correct
                    ? 'bg-green-900/50 text-green-300'
                    : 'bg-red-900/50 text-red-300';
            },
            get feedbackText() {
                if (!this.lastResult) return '';
                return this.lastResult.is_correct ? 'Correct!' : 'Wrong!';
            },
            get pointsFeedback() {
                if (!this.lastResult) return '';
                if (this.lastResult.is_correct) {
                    return '+' + this.lastResult.points_earned + ' points';
                }
                return 'Correct answer: ' + (this.lastResult.correct_answer || '');
            },
            get tableLabel() {
                return 'Table ' + this.tableNumber;
            },
            get personalScoreLabel() {
                return this.personalScore + ' pts';
            },
            get nextTableLabel() {
                if (!this.nextTable) return '';
                return 'Next: Table ' + this.nextTable;
            },
            get roleLabel() {
                if (this.userRole === 'anchor') return 'Anchor (stay at table)';
                if (this.userRole === 'rotator') return 'Rotator (you move!)';
                return '';
            },
            get rotateDestination() {
                return this.tableNumber || this.nextTable || '';
            },
            get rotateMessage() {
                if (this.userRole === 'anchor') {
                    return 'Stay at your table!';
                }
                if (this.tableNumber) {
                    return 'Move to Table ' + this.tableNumber + '!';
                }
                return 'Please move to the next table.';
            },
            get tableScoredFeedbackClass() {
                if (this._tableScoredCorrect) {
                    return 'bg-green-900/50 text-green-300';
                }
                return 'bg-red-900/50 text-red-300';
            },
            get tableLeaderboard() {
                var result = [];
                for (var i = 0; i < this.tables.length; i++) {
                    result.push({
                        rank: '#' + (i + 1),
                        label: 'Table ' + this.tables[i].table_number,
                        scoreLabel: this.tables[i].total_score + ' pts'
                    });
                }
                return result;
            },
            get individualLeaderboard() {
                var result = [];
                for (var i = 0; i < this.individuals.length; i++) {
                    result.push({
                        display_name: this.individuals[i].display_name,
                        scoreLabel: this.individuals[i].total_score + ' pts'
                    });
                }
                return result;
            },
            get hasIndividualScores() {
                return this.individuals.length > 0;
            },

            // --- Init ---

            init: function () {
                this.quizId = this.$el.getAttribute('data-quiz-id');
                this.isQuizNight = this.$el.getAttribute('data-quiz-night') === 'true';
                var tn = this.$el.getAttribute('data-table-number');
                if (tn) this.tableNumber = parseInt(tn, 10);
                var role = this.$el.getAttribute('data-user-role');
                if (role) this.userRole = role;
                this.connectWebSocket();
                if (this.isQuizNight) this.fetchAssignment();
            },

            fetchAssignment: function () {
                var self = this;
                fetch('/api/quiz/' + this.quizId + '/my-assignment/', {
                    credentials: 'same-origin'
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.table_number) self.tableNumber = data.table_number;
                    if (data.role) self.userRole = data.role;
                    if (data.tablemates) self.tablemates = data.tablemates;
                    if (data.personal_score !== undefined) self.personalScore = data.personal_score;
                    if (data.next_table) self.nextTable = data.next_table;
                })
                .catch(function () {});
            },

            // --- WebSocket ---

            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                var url = protocol + '//' + window.location.host + '/ws/quiz/' + this.quizId + '/';
                this.ws = new WebSocket(url);

                this.ws.onopen = function () {
                    self.connected = true;
                    self.reconnectAttempts = 0;
                };

                this.ws.onclose = function () {
                    self.connected = false;
                    var delay = Math.min(1000 * Math.pow(2, self.reconnectAttempts), 30000);
                    self.reconnectAttempts++;
                    setTimeout(function () { self.connectWebSocket(); }, delay);
                };

                this.ws.onmessage = function (event) {
                    var msg = JSON.parse(event.data);
                    self.handleMessage(msg);
                };
            },

            handleMessage: function (msg) {
                var type = msg.type;
                var data = msg.data;

                if (type === 'quiz.state') {
                    if (data.event_type) this.isQuizNight = data.event_type === 'quiz_night';
                    if (data.status === 'active' && data.question) {
                        this.showQuestion(data);
                    } else if (data.current_round) {
                        this.roundName = data.current_round.title;
                        this.isBonusRound = data.current_round.is_bonus || false;
                    }
                } else if (type === 'quiz.question') {
                    this.showQuestion(data);
                } else if (type === 'quiz.answer_result') {
                    this.lastResult = data;
                } else if (type === 'quiz.leaderboard') {
                    this.tables = data.tables || [];
                    this.individuals = data.individuals || [];
                    this.screen = 'leaderboard';
                } else if (type === 'quiz.rotate') {
                    // Re-fetch assignment to get new table number
                    if (this.isQuizNight) {
                        this.fetchAssignment();
                    }
                    if (data.round_title) this.roundName = data.round_title;
                    if (data.is_bonus !== undefined) this.isBonusRound = data.is_bonus;
                    this.screen = 'rotate';
                } else if (type === 'quiz.status') {
                    if (data.status === 'round_complete') {
                        this.screen = 'waiting';
                    }
                } else if (type === 'quiz.table_scored') {
                    // Show feedback when host scores our table
                    if (data.table_number === this.tableNumber) {
                        this._tableScoredCorrect = data.is_correct;
                        if (data.is_correct) {
                            var pts = data.points_awarded || 0;
                            this.tableScoredFeedback = '+' + pts + ' pts!';
                            this.personalScore += pts;
                        } else {
                            this.tableScoredFeedback = 'Incorrect';
                        }
                        var self = this;
                        if (this.tableScoredTimer) clearTimeout(this.tableScoredTimer);
                        this.tableScoredTimer = setTimeout(function () {
                            self.tableScoredFeedback = '';
                        }, 3000);
                    }
                } else if (type === 'quiz.table_score') {
                    // Legacy table score update
                }
            },

            showQuestion: function (data) {
                this.question = data.question || data;
                this.choices = this.question.choices || [];
                this.questionIndex = data.index || 0;
                this.questionTotal = data.total || 0;
                this.countdownTotal = data.time || 30;
                this.countdown = this.countdownTotal;
                this.selectedIndex = null;
                this.answered = false;
                this.lastResult = null;
                this.isBonusRound = data.is_bonus || false;
                this.screen = 'question';
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

            selectAnswer: function (index) {
                if (!this.answered) {
                    this.selectedIndex = index;
                }
            },

            submitAnswer: function () {
                if (this.answered || this.selectedIndex === null || !this.question) return;
                this.answered = true;

                var choice = this.choices[this.selectedIndex];
                this.ws.send(JSON.stringify({
                    action: 'table_answer',
                    question_id: this.question.id,
                    answer: choice.text
                }));
            },

            choiceClass: function (index) {
                if (!this.isQuizNight) {
                    // Legacy: interactive choices
                    if (this.answered && this.selectedIndex === index) {
                        if (this.lastResult && this.lastResult.is_correct) {
                            return 'bg-green-700 ring-2 ring-green-400';
                        }
                        if (this.lastResult && !this.lastResult.is_correct) {
                            return 'bg-red-700 ring-2 ring-red-400';
                        }
                        return 'bg-crush-purple ring-2 ring-crush-pink';
                    }
                    if (this.selectedIndex === index) {
                        return 'bg-crush-purple/50 ring-2 ring-crush-pink';
                    }
                }
                // Quiz night: read-only display
                return 'bg-slate-700';
            },

            // Cleanup
            destroy: function () {
                if (this.countdownTimer) clearInterval(this.countdownTimer);
                if (this.tableScoredTimer) clearTimeout(this.tableScoredTimer);
                if (this.ws) this.ws.close();
            }
        };
    });


    // ========================================================================
    // HOST COMPONENT (formerly quizCoach)
    // ========================================================================
    Alpine.data('quizHost', function () {
        return {
            // Connection
            ws: null,
            connected: false,
            quizId: null,
            reconnectAttempts: 0,

            // State
            status: 'draft',
            isQuizNight: false,
            selectedRoundId: null,
            currentQuestion: null,
            currentRound: null,
            questionIdx: 0,
            questionCount: 0,
            isBonusRound: false,

            // Scoring
            tableCount: 0,
            scoredTables: {},  // { tableId: true/false }
            scoringQuestionId: null,

            // Leaderboard
            tables: [],

            // --- Getters ---

            get isConnected() { return this.connected; },
            get isDisconnected() { return !this.connected; },
            get canStart() { return this.status === 'draft' || this.status === 'paused'; },
            get canPause() { return this.status === 'active'; },
            get canEnd() { return this.status === 'active' || this.status === 'paused'; },
            get isFinished() { return this.status === 'finished'; },
            get hasCurrentQuestion() { return this.currentQuestion !== null; },
            get hasLeaderboard() { return this.tables.length > 0; },
            get showScoringGrid() { return this.isQuizNight && this.hasCurrentQuestion; },
            get isBonusLabel() { return this.isBonusRound ? 'BONUS x2' : ''; },
            get hasBonusRound() { return this.isBonusRound; },

            get statusText() {
                var map = { draft: 'Draft', active: 'Active', paused: 'Paused', finished: 'Finished' };
                return map[this.status] || this.status;
            },
            get statusBadgeClass() {
                if (this.status === 'active') return 'bg-green-900/50 text-green-300';
                if (this.status === 'paused') return 'bg-amber-900/50 text-amber-300';
                if (this.status === 'finished') return 'bg-slate-700 text-gray-400';
                return 'bg-slate-700 text-gray-400';
            },
            get statusDotClass() {
                if (this.status === 'active') return 'bg-green-400';
                if (this.status === 'paused') return 'bg-amber-400';
                return 'bg-gray-400';
            },
            get currentQuestionText() {
                return this.currentQuestion ? this.currentQuestion.text : '';
            },
            get currentQuestionType() {
                return this.currentQuestion ? this.currentQuestion.question_type : '';
            },
            get currentRoundTitle() {
                return this.currentRound ? this.currentRound.title : '';
            },
            get questionProgress() {
                if (!this.questionCount) return '';
                return 'Q' + (this.questionIdx + 1) + ' / ' + this.questionCount;
            },
            get correctAnswerText() {
                if (!this.currentQuestion) return '';
                // For multiple choice / true false
                if (this.currentQuestion.choices_with_answers) {
                    var correct = [];
                    for (var i = 0; i < this.currentQuestion.choices_with_answers.length; i++) {
                        var c = this.currentQuestion.choices_with_answers[i];
                        if (c.is_correct) correct.push(c.text);
                    }
                    return correct.join(', ');
                }
                // For open ended
                if (this.currentQuestion.correct_answer) {
                    return this.currentQuestion.correct_answer;
                }
                return '';
            },
            get hasCorrectAnswer() {
                return this.correctAnswerText !== '';
            },
            get tableLeaderboard() {
                var result = [];
                for (var i = 0; i < this.tables.length; i++) {
                    result.push({
                        rank: '#' + (i + 1),
                        label: 'Table ' + this.tables[i].table_number,
                        scoreLabel: this.tables[i].total_score + ' pts'
                    });
                }
                return result;
            },

            // --- Init ---

            init: function () {
                this.quizId = this.$el.getAttribute('data-quiz-id');
                this.isQuizNight = this.$el.getAttribute('data-quiz-night') === 'true';
                var tc = this.$el.getAttribute('data-table-count');
                if (tc) this.tableCount = parseInt(tc, 10);
                this.connectWebSocket();
            },

            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                var url = protocol + '//' + window.location.host + '/ws/quiz/' + this.quizId + '/';
                this.ws = new WebSocket(url);

                this.ws.onopen = function () {
                    self.connected = true;
                    self.reconnectAttempts = 0;
                };

                this.ws.onclose = function () {
                    self.connected = false;
                    var delay = Math.min(1000 * Math.pow(2, self.reconnectAttempts), 30000);
                    self.reconnectAttempts++;
                    setTimeout(function () { self.connectWebSocket(); }, delay);
                };

                this.ws.onmessage = function (event) {
                    var msg = JSON.parse(event.data);
                    self.handleMessage(msg);
                };
            },

            handleMessage: function (msg) {
                var type = msg.type;
                var data = msg.data;

                if (type === 'quiz.state') {
                    this.status = data.status;
                    if (data.event_type) this.isQuizNight = data.event_type === 'quiz_night';
                    if (data.current_round) {
                        this.currentRound = data.current_round;
                        this.isBonusRound = data.current_round.is_bonus || false;
                    }
                    if (data.question) {
                        this.currentQuestion = data.question;
                    }
                    this.questionIdx = data.question_index || 0;
                } else if (type === 'quiz.question') {
                    this.currentQuestion = data;
                    this.questionIdx = data.index || 0;
                    this.questionCount = data.total || 0;
                    this.isBonusRound = data.is_bonus || false;
                    this.status = 'active';
                    // Reset scoring state for new question
                    this.scoredTables = {};
                    this.scoringQuestionId = data.id;
                } else if (type === 'quiz.leaderboard') {
                    this.tables = data.tables || [];
                } else if (type === 'quiz.status') {
                    if (data.status) this.status = data.status;
                    if (data.status === 'round_complete') {
                        this.currentQuestion = null;
                    }
                } else if (type === 'quiz.table_scored') {
                    // Track which tables have been scored
                    if (data.table_id) {
                        this.scoredTables[data.table_id] = data.is_correct;
                    }
                }
            },

            // --- Host actions ---

            nextQuestion: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: 'next_question' }));
                }
            },

            showLeaderboard: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: 'show_leaderboard' }));
                }
            },

            triggerRotate: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: 'rotate' }));
                }
            },

            startQuiz: function () {
                this.nextQuestion();
            },

            pauseQuiz: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: 'pause_quiz' }));
                }
            },

            endQuiz: function () {
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({ action: 'end_quiz' }));
                }
            },

            // --- Table scoring (quiz night) ---

            scoreTable: function (tableId, isCorrect) {
                if (!this.ws || !this.connected || !this.currentQuestion) return;
                this.ws.send(JSON.stringify({
                    action: 'score_table',
                    table_id: tableId,
                    question_id: this.currentQuestion.id,
                    is_correct: isCorrect
                }));
                this.scoredTables[tableId] = isCorrect;
            },

            scoreAllCorrect: function () {
                // Score all unscored tables as correct
                var tableEls = this.$el.querySelectorAll('[data-table-id]');
                for (var i = 0; i < tableEls.length; i++) {
                    var tid = parseInt(tableEls[i].getAttribute('data-table-id'), 10);
                    if (this.scoredTables[tid] === undefined) {
                        this.scoreTable(tid, true);
                    }
                }
            },

            clearScoring: function () {
                this.scoredTables = {};
            },

            tableScoreClass: function (tableId) {
                if (this.scoredTables[tableId] === true) {
                    return 'bg-green-700 ring-2 ring-green-400 text-white';
                }
                if (this.scoredTables[tableId] === false) {
                    return 'bg-red-700 ring-2 ring-red-400 text-white';
                }
                return 'bg-slate-700 text-gray-300 hover:bg-slate-600';
            },

            isTableScored: function (tableId) {
                return this.scoredTables[tableId] !== undefined;
            },

            selectRound: function (roundId) {
                this.selectedRoundId = roundId;
                if (this.ws && this.connected) {
                    this.ws.send(JSON.stringify({
                        action: 'set_round',
                        round_id: roundId
                    }));
                }
            },

            roundButtonClass: function (roundId) {
                if (this.selectedRoundId === roundId) {
                    return 'bg-crush-purple/30 text-white ring-1 ring-crush-purple';
                }
                return 'bg-slate-700 text-gray-300 hover:bg-slate-600';
            },

            // Cleanup
            destroy: function () {
                if (this.ws) this.ws.close();
            }
        };
    });

});
