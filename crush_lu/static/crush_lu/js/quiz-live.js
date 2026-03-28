/**
 * Live Quiz Alpine.js CSP-compliant components.
 *
 * Two components:
 *   quizLive  – attendee view (waiting, question, leaderboard, rotate screens)
 *   quizCoach – coach control panel (next question, rotate, leaderboard)
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

            // Leaderboard
            tables: [],
            individuals: [],

            // Round info
            roundName: '',

            // --- Getters (CSP-safe computed properties) ---

            get isWaiting() { return this.screen === 'waiting'; },
            get isQuestion() { return this.screen === 'question'; },
            get isLeaderboard() { return this.screen === 'leaderboard'; },
            get isRotate() { return this.screen === 'rotate'; },
            get isConnected() { return this.connected; },
            get isDisconnected() { return !this.connected; },
            get hasAnswered() { return this.answered; },
            get hasSelected() { return this.selectedIndex !== null; },

            get questionText() {
                return this.question ? this.question.text : '';
            },
            get pointsLabel() {
                return this.question ? this.question.points + ' pts' : '';
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
                this.connectWebSocket();
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
                    // Exponential backoff reconnect
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
                    if (data.status === 'active' && data.question) {
                        this.showQuestion(data);
                    } else if (data.current_round) {
                        this.roundName = data.current_round.title;
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
                    this.screen = 'rotate';
                } else if (type === 'quiz.status') {
                    if (data.status === 'round_complete') {
                        this.screen = 'waiting';
                    }
                } else if (type === 'quiz.table_score') {
                    // Could update a local table score display
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

            // --- User actions ---

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
                return 'bg-slate-700 hover:bg-slate-600';
            },

            // Cleanup
            destroy: function () {
                if (this.countdownTimer) clearInterval(this.countdownTimer);
                if (this.ws) this.ws.close();
            }
        };
    });


    // ========================================================================
    // COACH COMPONENT
    // ========================================================================
    Alpine.data('quizCoach', function () {
        return {
            // Connection
            ws: null,
            connected: false,
            quizId: null,
            reconnectAttempts: 0,

            // State
            status: 'draft',
            selectedRoundId: null,
            currentQuestion: null,
            currentRound: null,
            questionIdx: 0,
            questionCount: 0,

            // Leaderboard
            tables: [],

            // --- Getters ---

            get isConnected() { return this.connected; },
            get isDisconnected() { return !this.connected; },
            get canStart() { return this.status === 'draft' || this.status === 'paused'; },
            get hasCurrentQuestion() { return this.currentQuestion !== null; },
            get hasLeaderboard() { return this.tables.length > 0; },

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
            get currentRoundTitle() {
                return this.currentRound ? this.currentRound.title : '';
            },
            get questionProgress() {
                if (!this.questionCount) return '';
                return 'Q' + (this.questionIdx + 1) + ' / ' + this.questionCount;
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
                    if (data.current_round) {
                        this.currentRound = data.current_round;
                    }
                    if (data.question) {
                        this.currentQuestion = data.question;
                    }
                    this.questionIdx = data.question_index || 0;
                } else if (type === 'quiz.question') {
                    this.currentQuestion = data;
                    this.questionIdx = data.index || 0;
                    this.questionCount = data.total || 0;
                    this.status = 'active';
                } else if (type === 'quiz.leaderboard') {
                    this.tables = data.tables || [];
                } else if (type === 'quiz.status') {
                    if (data.status === 'round_complete') {
                        this.currentQuestion = null;
                    }
                }
            },

            // --- Coach actions ---

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
                // Start quiz by sending first question
                this.nextQuestion();
            },

            selectRound: function (roundId) {
                this.selectedRoundId = roundId;
                // Set round via API then refresh state
                var self = this;
                fetch('/api/quiz/' + this.quizId + '/state/', {
                    credentials: 'same-origin'
                })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self.status = data.status;
                });
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
