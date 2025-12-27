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

            // Computed getters for CSP compatibility (avoid inline expressions)
            get mobileMenuClosed() { return !this.mobileMenuOpen; },
            get mobileMenuAriaExpanded() { return this.mobileMenuOpen ? 'true' : 'false'; },
            get coachToolsAriaExpanded() { return this.coachToolsOpen ? 'true' : 'false'; },
            get coachProfileAriaExpanded() { return this.coachProfileOpen ? 'true' : 'false'; },
            get eventsAriaExpanded() { return this.eventsOpen ? 'true' : 'false'; },
            get userMenuAriaExpanded() { return this.userMenuOpen ? 'true' : 'false'; },

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

            // Computed getters for CSP compatibility
            get isLoginTab() { return this.activeTab === 'login'; },
            get isSignupTab() { return this.activeTab === 'signup'; },
            get loginTabClass() {
                return this.activeTab === 'login'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-900 bg-white/50 hover:bg-white/80';
            },
            get signupTabClass() {
                return this.activeTab === 'signup'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-900 bg-white/50 hover:bg-white/80';
            },

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

    // Email preferences component (account settings)
    // Reads initial unsubscribe state from data-unsubscribed attribute
    Alpine.data('emailPreferences', function() {
        return {
            unsubscribeAll: false,
            init: function() {
                var unsubscribed = this.$el.getAttribute('data-unsubscribed');
                this.unsubscribeAll = unsubscribed === 'true';
            },
            toggleUnsubscribe: function() {
                this.unsubscribeAll = !this.unsubscribeAll;
            }
        };
    });

    // Decline animation component (connection response)
    // Shows briefly then fades out
    Alpine.data('declineAnimation', function() {
        return {
            show: true,
            init: function() {
                var self = this;
                setTimeout(function() {
                    self.show = false;
                }, 2000);
            }
        };
    });

    // Character counter component
    // Reads initial count from data-initial-count and max from data-max-length
    Alpine.data('charCounter', function() {
        return {
            charCount: 0,
            maxLength: 500,
            init: function() {
                var initialCount = this.$el.getAttribute('data-initial-count');
                var maxLength = this.$el.getAttribute('data-max-length');
                this.charCount = initialCount ? parseInt(initialCount) : 0;
                this.maxLength = maxLength ? parseInt(maxLength) : 500;
            },
            updateCount: function(event) {
                this.charCount = event.target.value.length;
            }
        };
    });

    // Photo upload component for profile photos
    // Reads initial photos from data attributes
    Alpine.data('photoUpload', function() {
        return {
            photos: [
                { id: 1, hasImage: false, preview: '' },
                { id: 2, hasImage: false, preview: '' },
                { id: 3, hasImage: false, preview: '' }
            ],

            // Computed getters for CSP compatibility
            get photo1NoImage() { return !this.photos[0].hasImage; },
            get photo2NoImage() { return !this.photos[1].hasImage; },
            get photo3NoImage() { return !this.photos[2].hasImage; },

            init: function() {
                var el = this.$el;
                // Read initial photo states from data attributes
                for (var i = 1; i <= 3; i++) {
                    var hasImage = el.getAttribute('data-photo-' + i + '-exists') === 'true';
                    var preview = el.getAttribute('data-photo-' + i + '-url') || '';
                    this.photos[i - 1].hasImage = hasImage;
                    this.photos[i - 1].preview = preview;
                }
            },
            handleFile1: function(event) {
                this._handleFileSelect(0, event);
            },
            handleFile2: function(event) {
                this._handleFileSelect(1, event);
            },
            handleFile3: function(event) {
                this._handleFileSelect(2, event);
            },
            _handleFileSelect: function(index, event) {
                var file = event.target.files[0];
                if (file) {
                    var self = this;
                    var reader = new FileReader();
                    reader.onload = function(e) {
                        self.photos[index].preview = e.target.result;
                        self.photos[index].hasImage = true;
                    };
                    reader.readAsDataURL(file);
                }
            },
            removePhoto1: function() {
                this._removePhoto(0);
            },
            removePhoto2: function() {
                this._removePhoto(1);
            },
            removePhoto3: function() {
                this._removePhoto(2);
            },
            _removePhoto: function(index) {
                this.photos[index].preview = '';
                this.photos[index].hasImage = false;
                var input = document.getElementById('photo' + (index + 1));
                if (input) {
                    input.value = '';
                }
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
            get step1NotCompleted() { return !this.step1Completed; },
            get step2NotCompleted() { return !this.step2Completed; },
            get step3NotCompleted() { return !this.step3Completed; },
            get notPhoneVerified() { return !this.phoneVerified; },

            // Step progress bar classes (avoid ternary expressions in templates)
            get step1CircleClass() {
                return this.currentStep >= 1
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step1TextClass() {
                return this.currentStep >= 1
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step2CircleClass() {
                return this.currentStep >= 2
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step2TextClass() {
                return this.currentStep >= 2
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step3CircleClass() {
                return this.currentStep >= 3
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step3TextClass() {
                return this.currentStep >= 3
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step4CircleClass() {
                return this.currentStep >= 4
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step4TextClass() {
                return this.currentStep >= 4
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step1ConnectorClass() {
                return this.step1Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },
            get step2ConnectorClass() {
                return this.step2Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },
            get step3ConnectorClass() {
                return this.step3Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },

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

            // CSP-compatible method for conditional next step (requires phone verification)
            nextStepIfVerified: function() {
                if (this.phoneVerified) {
                    this.nextStep();
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

    // Canton Map component for location selection
    // Interactive SVG map for selecting Luxembourg cantons and border regions
    Alpine.data('cantonMap', function() {
        return {
            // State
            selectedRegion: '',
            selectedRegionName: '',
            hoveredRegion: '',
            hoveredRegionName: '',
            showFallbackDropdown: false,
            focusedIndex: -1,

            // Region data for keyboard navigation
            regions: [],

            // Computed getters for CSP compatibility
            get hasSelection() {
                return this.selectedRegion !== '';
            },
            get noSelection() {
                return this.selectedRegion === '';
            },
            get isHovering() {
                return this.hoveredRegion !== '';
            },
            get selectionLabel() {
                if (this.selectedRegionName) {
                    return this.selectedRegionName;
                }
                return this.$el.getAttribute('data-placeholder') || 'Click the map to select your region';
            },
            get hoverLabel() {
                return this.hoveredRegionName || '';
            },
            get fallbackDropdownVisible() {
                return this.showFallbackDropdown;
            },
            get selectedClass() {
                return this.hasSelection ? 'has-selection' : '';
            },

            init: function() {
                var self = this;

                // Read initial value from data attributes
                var initialValue = this.$el.getAttribute('data-initial-value');
                var initialName = this.$el.getAttribute('data-initial-name');

                if (initialValue && initialValue !== '') {
                    this.selectedRegion = initialValue;
                    this.selectedRegionName = initialName || initialValue;
                    // Highlight the initially selected region
                    this.$nextTick(function() {
                        self._highlightRegion(initialValue);
                    });
                }

                // Build regions array for keyboard navigation
                this.$nextTick(function() {
                    self._buildRegionsArray();
                    self._setupEventDelegation();
                    self._setupKeyboardNavigation();
                });

                // Check for reduced motion preference
                if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    this.$el.classList.add('reduce-motion');
                }
            },

            _buildRegionsArray: function() {
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                var paths = svg.querySelectorAll('[data-region-id]');
                this.regions = [];

                for (var i = 0; i < paths.length; i++) {
                    this.regions.push({
                        id: paths[i].getAttribute('data-region-id'),
                        name: paths[i].getAttribute('data-region-name'),
                        element: paths[i]
                    });
                }
            },

            _getBorderRegionAtPoint: function(svgX, svgY) {
                // France - South side (narrow bottom strip)
                if (svgY > 850) {
                    return { id: 'border-france', name: 'France (Thionville/Metz area)' };
                }
                // Belgium - West side (left strip)
                if (svgX < 350) {
                    return { id: 'border-belgium', name: 'Belgium (Arlon area)' };
                }
                // Germany - East side (right strip)
                if (svgX > 350) {
                    return { id: 'border-germany', name: 'Germany (Trier/Saarland area)' };
                }
                return null;
            },

            _screenToSVGCoords: function(svg, screenX, screenY) {
                var point = svg.createSVGPoint();
                point.x = screenX;
                point.y = screenY;
                var ctm = svg.getScreenCTM();
                if (ctm) {
                    return point.matrixTransform(ctm.inverse());
                }
                return point;
            },

            _setupEventDelegation: function() {
                var self = this;
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                // Click handler
                svg.addEventListener('click', function(e) {
                    var path = e.target.closest('[data-region-id]');

                    if (path && path.classList.contains('lux-canton')) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        return;
                    }

                    if (path && path.classList.contains('border-region')) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        return;
                    }

                    if (e.target.classList.contains('map-background') || e.target.tagName === 'svg') {
                        var svgPoint = self._screenToSVGCoords(svg, e.clientX, e.clientY);
                        var borderRegion = self._getBorderRegionAtPoint(svgPoint.x, svgPoint.y);
                        if (borderRegion) {
                            self.selectRegion(borderRegion.id, borderRegion.name);
                        }
                    }
                });

                // Mouseover handler
                svg.addEventListener('mouseover', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.hoverRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                    }
                });

                // Mouseout handler
                svg.addEventListener('mouseout', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.clearHover();
                    }
                });

                // Touch events for mobile
                svg.addEventListener('touchstart', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.hoverRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                    }
                }, { passive: true });

                svg.addEventListener('touchend', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        self.clearHover();
                    }
                });
            },

            _setupKeyboardNavigation: function() {
                var self = this;
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                svg.addEventListener('keydown', function(e) {
                    var key = e.key;
                    if (key === 'ArrowDown' || key === 'ArrowRight') {
                        e.preventDefault();
                        self._navigateNext();
                    } else if (key === 'ArrowUp' || key === 'ArrowLeft') {
                        e.preventDefault();
                        self._navigatePrevious();
                    } else if (key === 'Enter' || key === ' ') {
                        e.preventDefault();
                        self._selectFocused();
                    } else if (key === 'Home') {
                        e.preventDefault();
                        self._focusFirst();
                    } else if (key === 'End') {
                        e.preventDefault();
                        self._focusLast();
                    }
                });

                svg.addEventListener('focus', function() {
                    if (self.focusedIndex < 0 && self.regions.length > 0) {
                        var selectedIndex = self._getSelectedIndex();
                        self.focusedIndex = selectedIndex >= 0 ? selectedIndex : 0;
                        self._applyFocus();
                    }
                });

                svg.addEventListener('blur', function() {
                    self._clearFocus();
                });
            },

            _navigateNext: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex + 1) % this.regions.length;
                this._applyFocus();
            },

            _navigatePrevious: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex - 1 + this.regions.length) % this.regions.length;
                this._applyFocus();
            },

            _focusFirst: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = 0;
                this._applyFocus();
            },

            _focusLast: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = this.regions.length - 1;
                this._applyFocus();
            },

            _selectFocused: function() {
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    this.selectRegion(region.id, region.name);
                }
            },

            _getSelectedIndex: function() {
                for (var i = 0; i < this.regions.length; i++) {
                    if (this.regions[i].id === this.selectedRegion) {
                        return i;
                    }
                }
                return -1;
            },

            _applyFocus: function() {
                this._clearFocus();
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    region.element.classList.add('region-focused');
                    this.hoverRegion(region.id, region.name);
                }
            },

            _clearFocus: function() {
                var svg = this.$el.querySelector('svg');
                if (!svg) return;
                var focusedElements = svg.querySelectorAll('.region-focused');
                for (var i = 0; i < focusedElements.length; i++) {
                    focusedElements[i].classList.remove('region-focused');
                }
            },

            selectRegion: function(regionId, regionName) {
                if (this.selectedRegion) {
                    this._unhighlightRegion(this.selectedRegion);
                }
                this.selectedRegion = regionId;
                this.selectedRegionName = regionName;
                this._highlightRegion(regionId);

                var hiddenInput = document.getElementById('id_location');
                if (hiddenInput) {
                    hiddenInput.value = regionId;
                    hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
                }

                this.$dispatch('region-selected', { id: regionId, name: regionName });
            },

            hoverRegion: function(regionId, regionName) {
                this.hoveredRegion = regionId;
                this.hoveredRegionName = regionName;
                var path = document.getElementById(regionId);
                if (path && !path.classList.contains('region-selected')) {
                    path.classList.add('region-hover');
                }
            },

            clearHover: function() {
                if (this.hoveredRegion) {
                    var prevPath = document.getElementById(this.hoveredRegion);
                    if (prevPath) {
                        prevPath.classList.remove('region-hover');
                    }
                }
                this.hoveredRegion = '';
                this.hoveredRegionName = '';
            },

            toggleFallbackDropdown: function() {
                this.showFallbackDropdown = !this.showFallbackDropdown;
            },

            handleFallbackSelect: function(event) {
                var select = event.target;
                var option = select.options[select.selectedIndex];
                if (option && option.value) {
                    this.selectRegion(option.value, option.text);
                }
            },

            _highlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.add('region-selected');
                    path.classList.remove('region-hover');
                    path.setAttribute('aria-selected', 'true');
                }
            },

            _unhighlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.remove('region-selected');
                    path.setAttribute('aria-selected', 'false');
                }
            }
        };
    });

    // Phone verification modal component
    // Used in profile creation for SMS verification
    Alpine.data('phoneVerificationModal', function() {
        return {
            showModal: false,
            step: 'code', // 'sending', 'code', 'verifying', 'success', 'error'
            code: ['', '', '', '', '', ''],
            resendCountdown: 0,
            errorMessage: '',
            countdownInterval: null,

            // Computed getters for CSP compatibility
            get isSendingStep() { return this.step === 'sending'; },
            get isCodeStep() { return this.step === 'code'; },
            get isVerifyingStep() { return this.step === 'verifying'; },
            get isSuccessStep() { return this.step === 'success'; },
            get isErrorStep() { return this.step === 'error'; },
            get canResend() { return this.resendCountdown === 0; },
            get cannotResend() { return this.resendCountdown > 0; },

            init: function() {
                // Listen for modal open event
                var self = this;
                window.addEventListener('open-phone-modal', function() {
                    self.openModal();
                });
            },

            openModal: function() {
                this.showModal = true;
                this.step = 'code';
                this.code = ['', '', '', '', '', ''];
                this.errorMessage = '';
                this.startCountdown();
            },

            closeModal: function() {
                this.showModal = false;
                this.clearCountdown();
            },

            startCountdown: function() {
                var self = this;
                this.resendCountdown = 60;
                this.clearCountdown();
                this.countdownInterval = setInterval(function() {
                    if (self.resendCountdown > 0) {
                        self.resendCountdown--;
                    } else {
                        self.clearCountdown();
                    }
                }, 1000);
            },

            clearCountdown: function() {
                if (this.countdownInterval) {
                    clearInterval(this.countdownInterval);
                    this.countdownInterval = null;
                }
            },

            handleCodeInput: function(event, index) {
                var value = event.target.value;
                if (value.length === 1) {
                    this.code[index] = value;
                    // Focus next input
                    var nextInput = document.getElementById('code-' + (index + 1));
                    if (nextInput) {
                        nextInput.focus();
                    }
                } else if (value.length === 0) {
                    this.code[index] = '';
                }
            },

            handleCodeKeydown: function(event, index) {
                if (event.key === 'Backspace' && this.code[index] === '' && index > 0) {
                    var prevInput = document.getElementById('code-' + (index - 1));
                    if (prevInput) {
                        prevInput.focus();
                    }
                }
            },

            handleCodePaste: function(event) {
                event.preventDefault();
                var pastedData = (event.clipboardData || window.clipboardData).getData('text');
                var digits = pastedData.replace(/\D/g, '').substring(0, 6);
                for (var i = 0; i < digits.length; i++) {
                    this.code[i] = digits[i];
                }
                // Focus last filled input or first empty
                var focusIndex = Math.min(digits.length, 5);
                var focusInput = document.getElementById('code-' + focusIndex);
                if (focusInput) {
                    focusInput.focus();
                }
            },

            verifyCode: function() {
                var fullCode = this.code.join('');
                if (fullCode.length !== 6) {
                    this.errorMessage = 'Please enter all 6 digits';
                    return;
                }
                this.step = 'verifying';
                var self = this;

                // Get CSRF token
                var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');

                fetch('/verify-phone/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken ? csrfToken.value : ''
                    },
                    body: JSON.stringify({ code: fullCode })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success) {
                        self.step = 'success';
                        // Dispatch event to parent
                        window.dispatchEvent(new CustomEvent('phone-verified'));
                        setTimeout(function() {
                            self.closeModal();
                        }, 1500);
                    } else {
                        self.step = 'code';
                        self.errorMessage = data.error || 'Invalid code. Please try again.';
                    }
                })
                .catch(function(err) {
                    self.step = 'code';
                    self.errorMessage = 'An error occurred. Please try again.';
                });
            },

            resendCode: function() {
                if (this.resendCountdown > 0) return;
                this.step = 'sending';
                var self = this;

                var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');

                fetch('/resend-verification/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken ? csrfToken.value : ''
                    }
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    self.step = 'code';
                    self.code = ['', '', '', '', '', ''];
                    self.startCountdown();
                })
                .catch(function(err) {
                    self.step = 'code';
                    self.errorMessage = 'Failed to resend code. Please try again.';
                });
            }
        };
    });

});
