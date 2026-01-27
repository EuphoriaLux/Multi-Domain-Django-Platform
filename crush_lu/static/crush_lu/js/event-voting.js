/**
 * Event Activity Voting System
 * Handles countdown timers, vote submission, and live updates
 */

class EventVotingManager {
    constructor(eventId, resultsUrl = null) {
        this.eventId = eventId;
        // Use provided resultsUrl or fallback to current path replacement
        // This allows templates to pass language-prefixed URLs
        this.resultsUrl = resultsUrl || window.location.pathname.replace('/voting/', '/voting/results/');
        this.statusCheckInterval = null;
        this.countdownInterval = null;
        this.init();
    }

    init() {
        // Start checking voting status
        this.checkVotingStatus();
        this.statusCheckInterval = setInterval(() => {
            this.checkVotingStatus();
        }, 5000); // Check every 5 seconds

        // Setup vote form submission if on voting page
        this.setupVoteForm();

        // Setup live results updates if on results page
        if (document.getElementById('voting-results-container')) {
            this.updateResultsDisplay();
            setInterval(() => this.updateResultsDisplay(), 10000); // Update every 10 seconds
        }
    }

    async checkVotingStatus() {
        try {
            const response = await fetch(`/api/events/${this.eventId}/voting/status/`);
            const data = await response.json();

            if (data.success) {
                this.updateStatusDisplay(data.data);
            }
        } catch (error) {
            console.error('Error checking voting status:', error);
        }
    }

    updateStatusDisplay(status) {
        const phase = status.phase;

        // Update countdown timer
        if (phase === 'waiting') {
            this.startCountdown('time-until-start', status.time_until_start, () => {
                // Voting has started, reload to voting interface
                window.location.reload();
            });
        } else if (phase === 'active') {
            this.startCountdown('time-remaining', status.time_remaining, () => {
                // Voting has ended, redirect to results
                window.location.href = this.resultsUrl;
            });
        }

        // Update vote count display
        const voteCountElement = document.getElementById('total-votes-count');
        if (voteCountElement) {
            voteCountElement.textContent = status.total_votes;
        }

        // Update voting status badge
        this.updateStatusBadge(phase, status.is_voting_open);
    }

    updateStatusBadge(phase, isOpen) {
        const badge = document.getElementById('voting-status-badge');
        if (!badge) return;

        badge.className = 'badge fs-6';

        if (phase === 'waiting') {
            badge.className += ' bg-warning';
            badge.textContent = gettext('Voting Starts Soon');
        } else if (phase === 'active' && isOpen) {
            badge.className += ' bg-success';
            badge.textContent = gettext('Voting Open');
        } else if (phase === 'ended') {
            badge.className += ' bg-secondary';
            badge.textContent = gettext('Voting Closed');
        }
    }

    startCountdown(elementId, initialSeconds, onComplete) {
        const element = document.getElementById(elementId);
        if (!element) return;

        let secondsRemaining = Math.max(0, Math.floor(initialSeconds));

        // Clear any existing countdown
        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
        }

        // Update display immediately
        this.updateCountdownDisplay(element, secondsRemaining);

        // Start countdown
        this.countdownInterval = setInterval(() => {
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
        }, 1000);
    }

    updateCountdownDisplay(element, seconds) {
        const minutes = Math.floor(seconds / 60);
        const secs = seconds % 60;
        element.textContent = `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }

    setupVoteForm() {
        const voteForm = document.getElementById('vote-form');
        if (!voteForm) return;

        // Handle radio button selection
        const radioButtons = document.querySelectorAll('.variant-radio');
        radioButtons.forEach(radio => {
            radio.addEventListener('change', (e) => {
                // Set the hidden input value
                const optionId = e.target.value;
                document.getElementById('selected-option-id').value = optionId;

                // Enable submit button
                document.getElementById('submit-vote-btn').disabled = false;
            });
        });

        // Handle form submission
        voteForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.submitVote();
        });
    }

    async submitVote() {
        const optionId = document.getElementById('selected-option-id').value;

        if (!optionId) {
            this.showMessage(gettext('Please select an activity option'), 'error');
            return;
        }

        const submitBtn = document.getElementById('submit-vote-btn');
        const originalText = submitBtn.textContent;
        submitBtn.disabled = true;
        submitBtn.textContent = gettext('Submitting...');

        try {
            const response = await fetch(`/api/events/${this.eventId}/voting/submit/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrfToken(),
                },
                body: JSON.stringify({ option_id: parseInt(optionId) })
            });

            const data = await response.json();

            if (data.success) {
                this.showMessage(data.message, 'success');
                // Redirect to results page after short delay
                setTimeout(() => {
                    window.location.href = this.resultsUrl;
                }, 1500);
            } else {
                this.showMessage(data.error || 'Failed to submit vote', 'error');
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        } catch (error) {
            console.error('Error submitting vote:', error);
            this.showMessage(gettext('An error occurred. Please try again.'), 'error');
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    }

    async updateResultsDisplay() {
        try {
            const response = await fetch(`/api/events/${this.eventId}/voting/results/`);
            const data = await response.json();

            if (data.success) {
                this.renderResults(data.data);
            }
        } catch (error) {
            console.error('Error fetching results:', error);
        }
    }

    renderResults(resultsData) {
        const container = document.getElementById('voting-results-container');
        if (!container) return;

        const options = resultsData.options;
        const totalVotes = resultsData.total_votes;

        options.forEach(option => {
            // Update vote count
            const countElement = document.getElementById(`option-${option.id}-count`);
            if (countElement) {
                countElement.textContent = option.vote_count;
            }

            // Update percentage
            const percentElement = document.getElementById(`option-${option.id}-percent`);
            if (percentElement) {
                percentElement.textContent = `${option.percentage}%`;
            }

            // Update progress bar
            const progressBar = document.getElementById(`option-${option.id}-progress`);
            if (progressBar) {
                progressBar.style.width = `${option.percentage}%`;
                progressBar.setAttribute('aria-valuenow', option.percentage);
            }

            // Highlight winner
            if (option.is_winner) {
                const card = document.getElementById(`option-${option.id}-card`);
                if (card) {
                    card.classList.add('winner-option');
                }
            }
        });

        // Update total votes count
        const totalVotesElement = document.getElementById('total-votes-display');
        if (totalVotesElement) {
            totalVotesElement.textContent = totalVotes;
        }
    }

    showMessage(message, type = 'info') {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type === 'error' ? 'danger' : 'success'} alert-dismissible fade show`;
        alertDiv.setAttribute('role', 'alert');
        alertDiv.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        `;

        const container = document.querySelector('.container');
        if (container) {
            container.insertBefore(alertDiv, container.firstChild);

            // Auto-dismiss after 5 seconds
            setTimeout(() => {
                alertDiv.remove();
            }, 5000);
        }
    }

    getCsrfToken() {
        const cookie = document.cookie
            .split('; ')
            .find(row => row.startsWith('csrftoken='));
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
document.addEventListener('DOMContentLoaded', () => {
    const eventIdElement = document.getElementById('event-id-data');
    if (eventIdElement) {
        const eventId = eventIdElement.dataset.eventId;
        // Read results URL from data attribute (set by template with language prefix)
        const resultsUrl = eventIdElement.dataset.resultsUrl || null;
        window.votingManager = new EventVotingManager(eventId, resultsUrl);
    }
});
