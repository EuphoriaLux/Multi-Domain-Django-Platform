/**
 * Event Activity Voting System
 * Handles countdown timers, vote submission, and live updates
 */

class EventVotingManager {
    constructor(eventId, resultsUrl) {
        this.eventId = eventId;
        this.resultsUrl = resultsUrl || window.location.pathname.replace('/voting/', '/voting/results/');
        this.statusCheckInterval = null;
        this.countdownInterval = null;
        this.init();
    }

    init() {
        this.checkVotingStatus();
        this.statusCheckInterval = setInterval(function() {
            this.checkVotingStatus();
        }.bind(this), 5000);

        this.setupVoteForm();

        if (document.getElementById('voting-results-container')) {
            this.updateResultsDisplay();
            setInterval(function() { this.updateResultsDisplay(); }.bind(this), 10000);
        }
    }

    async checkVotingStatus() {
        try {
            var response = await fetch('/api/events/' + this.eventId + '/voting/status/');
            var data = await response.json();

            if (data.success) {
                this.updateStatusDisplay(data.data);
            }
        } catch (error) {
            console.error('Error checking voting status:', error);
        }
    }

    updateStatusDisplay(status) {
        var phase = status.phase;

        if (phase === 'waiting') {
            this.startCountdown('time-until-start', status.time_until_start, function() {
                window.location.reload();
            });
        } else if (phase === 'active') {
            this.startCountdown('time-remaining', status.time_remaining, function() {
                window.location.href = this.resultsUrl;
            }.bind(this));
        }

        var voteCountElement = document.getElementById('total-votes-count');
        if (voteCountElement) {
            voteCountElement.textContent = status.total_votes;
        }

        this.updateStatusBadge(phase, status.is_voting_open);
    }

    updateStatusBadge(phase, isOpen) {
        var badge = document.getElementById('voting-status-badge');
        if (!badge) return;

        badge.className = 'inline-flex items-center px-3 py-1 rounded-full text-sm font-medium';

        if (phase === 'waiting') {
            badge.className += ' bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300';
            badge.textContent = gettext('Voting Starts Soon');
        } else if (phase === 'active' && isOpen) {
            badge.className += ' bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300';
            badge.textContent = gettext('Voting Open');
        } else if (phase === 'ended') {
            badge.className += ' bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200';
            badge.textContent = gettext('Voting Closed');
        }
    }

    startCountdown(elementId, initialSeconds, onComplete) {
        var element = document.getElementById(elementId);
        if (!element) return;

        var secondsRemaining = Math.max(0, Math.floor(initialSeconds));

        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
        }

        this.updateCountdownDisplay(element, secondsRemaining);

        this.countdownInterval = setInterval(function() {
            secondsRemaining--;

            if (secondsRemaining <= 0) {
                clearInterval(this.countdownInterval);
                this.countdownInterval = null;
                element.textContent = '00:00';

                if (onComplete) {
                    onComplete();
                }
            } else {
                this.updateCountdownDisplay(element, secondsRemaining);
            }
        }.bind(this), 1000);
    }

    updateCountdownDisplay(element, seconds) {
        var minutes = Math.floor(seconds / 60);
        var secs = seconds % 60;
        element.textContent = String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
    }

    setupVoteForm() {
        var voteForm = document.getElementById('vote-form');
        if (!voteForm) return;

        var presentationHidden = document.getElementById('presentation-option-id');
        var twistHidden = document.getElementById('twist-option-id');
        var submitBtn = document.getElementById('submit-vote-btn');

        // Handle presentation radio selection
        var presentationRadios = document.querySelectorAll('.presentation-radio');
        presentationRadios.forEach(function(radio) {
            radio.addEventListener('change', function(e) {
                if (presentationHidden) {
                    presentationHidden.value = e.target.value;
                }
                checkBothSelected();
            });
        });

        // Handle twist radio selection
        var twistRadios = document.querySelectorAll('.twist-radio');
        twistRadios.forEach(function(radio) {
            radio.addEventListener('change', function(e) {
                if (twistHidden) {
                    twistHidden.value = e.target.value;
                }
                checkBothSelected();
            });
        });

        function checkBothSelected() {
            if (!submitBtn || !presentationHidden || !twistHidden) return;
            var presentationSelected = presentationHidden.value !== '';
            var twistSelected = twistHidden.value !== '';
            submitBtn.disabled = !(presentationSelected && twistSelected);
        }

        // The form submits normally (POST) -- no JS interception needed
        // since the view handles POST with presentation_option_id and twist_option_id
    }

    async updateResultsDisplay() {
        try {
            var response = await fetch('/api/events/' + this.eventId + '/voting/results/');
            var data = await response.json();

            if (data.success) {
                this.renderResults(data.data);
            }
        } catch (error) {
            console.error('Error fetching results:', error);
        }
    }

    renderResults(resultsData) {
        var container = document.getElementById('voting-results-container');
        if (!container) return;

        var options = resultsData.options;
        var totalVotes = resultsData.total_votes;

        options.forEach(function(option) {
            var countElement = document.getElementById('option-' + option.id + '-count');
            if (countElement) {
                countElement.textContent = option.vote_count;
            }

            var percentElement = document.getElementById('option-' + option.id + '-percent');
            if (percentElement) {
                percentElement.textContent = '(' + option.percentage + '%)';
            }

            var progressBar = document.getElementById('option-' + option.id + '-progress');
            if (progressBar) {
                progressBar.style.width = option.percentage + '%';
                progressBar.setAttribute('aria-valuenow', option.percentage);
            }

            if (option.is_winner) {
                var card = document.getElementById('option-' + option.id + '-card');
                if (card) {
                    card.classList.add('winner-option');
                }
            }
        });

        var totalVotesElement = document.getElementById('total-votes-display');
        if (totalVotesElement) {
            totalVotesElement.textContent = totalVotes;
        }
    }

    showMessage(message, type) {
        type = type || 'info';
        var alertDiv = document.createElement('div');
        var bgClass = type === 'error'
            ? 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-800 dark:text-red-300'
            : 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-700 text-green-800 dark:text-green-300';
        alertDiv.className = 'border rounded-lg p-4 mb-4 ' + bgClass;
        alertDiv.setAttribute('role', 'alert');
        alertDiv.textContent = message;

        var container = document.querySelector('.flex.justify-center');
        if (container) {
            container.insertBefore(alertDiv, container.firstChild);

            setTimeout(function() {
                alertDiv.remove();
            }, 5000);
        }
    }

    getCsrfToken() {
        var cookie = document.cookie
            .split('; ')
            .find(function(row) { return row.startsWith('csrftoken='); });
        return cookie ? cookie.split('=')[1] : '';
    }

    destroy() {
        if (this.statusCheckInterval) {
            clearInterval(this.statusCheckInterval);
        }
        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
        }
    }
}

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    var eventIdElement = document.getElementById('event-id-data');
    if (eventIdElement) {
        var eventId = eventIdElement.dataset.eventId;
        var resultsUrl = eventIdElement.dataset.resultsUrl || null;
        window.votingManager = new EventVotingManager(eventId, resultsUrl);
    }
});
