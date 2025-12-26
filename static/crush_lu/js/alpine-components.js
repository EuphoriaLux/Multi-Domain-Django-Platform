/**
 * Alpine.js CSP-Compatible Components for Crush.lu
 *
 * The Alpine.js CSP build cannot interpret inline method calls or complex expressions.
 * All interactive components must be registered with Alpine.data() to work properly.
 *
 * IMPORTANT: The CSP build does NOT support:
 * - Inline JavaScript expressions like @click="count++" or @click="toggle()"
 * - Passing parameters to Alpine.data() from x-data attributes
 *
 * Instead, use data attributes to pass initial values and read them in init().
 */

document.addEventListener('alpine:init', function() {

    // Navbar component with dropdowns and mobile menu
    Alpine.data('navbar', function() {
        return {
            mobileMenuOpen: false,
            coachToolsOpen: false,
            coachProfileOpen: false,
            eventsOpen: false,
            userMenuOpen: false,

            toggleMobile: function() {
                this.mobileMenuOpen = !this.mobileMenuOpen;
            },
            toggleCoachTools: function() {
                this.coachToolsOpen = !this.coachToolsOpen;
            },
            toggleCoachProfile: function() {
                this.coachProfileOpen = !this.coachProfileOpen;
            },
            toggleEvents: function() {
                this.eventsOpen = !this.eventsOpen;
            },
            toggleUserMenu: function() {
                this.userMenuOpen = !this.userMenuOpen;
            },
            closeCoachTools: function() {
                this.coachToolsOpen = false;
            },
            closeCoachProfile: function() {
                this.coachProfileOpen = false;
            },
            closeEvents: function() {
                this.eventsOpen = false;
            },
            closeUserMenu: function() {
                this.userMenuOpen = false;
            }
        };
    });

    // Dismissible alert/message component
    Alpine.data('dismissible', function() {
        return {
            show: true,
            dismiss: function() {
                this.show = false;
            }
        };
    });

    // Tab navigation component (for auth page)
    // Reads initial tab from data-initial-tab attribute
    Alpine.data('tabNav', function() {
        return {
            activeTab: 'login',
            init: function() {
                // Read initial tab from data attribute
                var initialTab = this.$el.getAttribute('data-initial-tab');
                if (initialTab) {
                    this.activeTab = initialTab;
                }
            },
            setLogin: function() {
                this.activeTab = 'login';
            },
            setSignup: function() {
                this.activeTab = 'signup';
            }
        };
    });

    // Screening dashboard row component
    Alpine.data('screeningRow', function() {
        return {
            showCompleteModal: false,
            openModal: function() {
                this.showCompleteModal = true;
            },
            closeModal: function() {
                this.showCompleteModal = false;
            }
        };
    });

    // Completed screening row component (view notes)
    Alpine.data('completedScreeningRow', function() {
        return {
            showNotesModal: false,
            openNotesModal: function() {
                this.showNotesModal = true;
            },
            closeNotesModal: function() {
                this.showNotesModal = false;
            }
        };
    });

    // Invitation row component (reject modal)
    Alpine.data('invitationRow', function() {
        return {
            showRejectModal: false,
            openRejectModal: function() {
                this.showRejectModal = true;
            },
            closeRejectModal: function() {
                this.showRejectModal = false;
            }
        };
    });

    // Profile creation wizard component
    // Reads initial values from data attributes
    Alpine.data('profileWizard', function() {
        return {
            currentStep: 1,
            totalSteps: 4,
            isSubmitting: false,
            phoneVerified: false,
            showErrors: false,
            errors: {},
            isEditing: false,
            step1Valid: false,
            step2Valid: true,

            // Computed-like properties for CSP compatibility
            // These avoid function calls in templates
            get step1Completed() { return this.currentStep > 1; },
            get step2Completed() { return this.currentStep > 2; },
            get step3Completed() { return this.currentStep > 3; },
            get step4Completed() { return this.currentStep > 4; },
            get isStep1() { return this.currentStep === 1; },
            get isStep2() { return this.currentStep === 2; },
            get isStep3() { return this.currentStep === 3; },
            get isStep4() { return this.currentStep === 4; },

            init: function() {
                // Read initial values from data attributes
                var el = this.$el;
                var initialStep = el.getAttribute('data-initial-step');
                var phoneVerified = el.getAttribute('data-phone-verified');
                var isEditing = el.getAttribute('data-is-editing');

                // Map step names to numbers
                var stepMap = {
                    'not_started': 1,
                    'step1': 2,
                    'step2': 3,
                    'step3': 4,
                    'completed': 4,
                    'submitted': 4
                };

                if (initialStep && stepMap[initialStep]) {
                    this.currentStep = stepMap[initialStep];
                } else if (initialStep && !isNaN(parseInt(initialStep))) {
                    this.currentStep = parseInt(initialStep);
                }

                this.phoneVerified = phoneVerified === 'true';
                this.isEditing = isEditing === 'true';

                // Set up HTMX listener
                var self = this;
                window.addEventListener('htmx:afterRequest', function(event) {
                    if (event.detail.successful) {
                        var trigger = event.detail.xhr.getResponseHeader('HX-Trigger-After-Swap');
                        if (trigger === 'step-valid') {
                            self.nextStep();
                        }
                    }
                });
            },

            nextStep: function() {
                if (this.currentStep < this.totalSteps) {
                    this.currentStep++;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            prevStep: function() {
                if (this.currentStep > 1) {
                    this.currentStep--;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            goToStep: function(step) {
                if (step >= 1 && step <= this.totalSteps) {
                    this.currentStep = step;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            isStepCompleted: function(step) {
                return step < this.currentStep;
            },

            isCurrentStep: function(step) {
                return step === this.currentStep;
            },

            updateReview: function() {
                var phone = document.querySelector('[name=phone_number]');
                var dob = document.querySelector('[name=date_of_birth]');
                var genderEl = document.querySelector('[name=gender]:checked');
                var location = document.querySelector('[name=location]');

                var reviewPhone = this.$refs.reviewPhone;
                var reviewDob = this.$refs.reviewDob;
                var reviewGender = this.$refs.reviewGender;
                var reviewLocation = this.$refs.reviewLocation;

                if (reviewPhone) {
                    reviewPhone.textContent = phone ? phone.value || 'Not provided' : 'Not provided';
                }
                if (reviewDob) {
                    reviewDob.textContent = dob ? dob.value || 'Not provided' : 'Not provided';
                }
                if (reviewGender && genderEl) {
                    var label = genderEl.nextElementSibling;
                    if (label) {
                        var genderLabel = label.querySelector('.gender-label');
                        reviewGender.textContent = genderLabel ? genderLabel.textContent : 'Not selected';
                    }
                } else if (reviewGender) {
                    reviewGender.textContent = 'Not selected';
                }
                if (reviewLocation) {
                    reviewLocation.textContent = location ? location.value || 'Not selected' : 'Not selected';
                }
            },

            setSubmitting: function() {
                this.isSubmitting = true;
            },

            nextStepAndReview: function() {
                this.nextStep();
                this.updateReview();
            }
        };
    });

});
