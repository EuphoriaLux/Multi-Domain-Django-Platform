/**
 * Alpine.js CSP components for Crush.lu — journey bundle.
 *
 * Wonderland Journey and gift pages (journey/journey_base.html,
 * journey/gift_base.html).
 * Loaded with crush_lu/partials/alpine_bundle.html, before Alpine starts.
 * Build: `npm run build:js`.
 */

document.addEventListener("alpine:init", function () {
    // ==========================================================================
    // JOURNEY GIFT COMPONENTS
    // ==========================================================================

    // Gift Sharing Component for success page
    // Handles copy to clipboard, WhatsApp, email, and native share
    Alpine.data("giftShare", function () {
        return {
            giftUrl: "",
            giftCode: "",
            recipientName: "",
            copied: false,
            hasNativeShare: false,

            // Computed getters for CSP compatibility
            get copyButtonText() {
                return this.copied ? gettext("Copied!") : gettext("Copy Link");
            },
            get copyButtonClass() {
                return this.copied
                    ? "share-btn share-btn-copy copied"
                    : "share-btn share-btn-copy";
            },
            get showNativeShare() {
                return this.hasNativeShare;
            },
            get hideNativeShare() {
                return !this.hasNativeShare;
            },

            init: function () {
                // Read data from attributes
                this.giftUrl = this.$el.getAttribute("data-gift-url") || "";
                this.giftCode = this.$el.getAttribute("data-gift-code") || "";
                this.recipientName = this.$el.getAttribute("data-recipient") || "";

                // Check for native share API support
                this.hasNativeShare = typeof navigator.share === "function";
            },

            copyLink: function () {
                var self = this;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard
                        .writeText(this.giftUrl)
                        .then(function () {
                            self.copied = true;
                            setTimeout(function () {
                                self.copied = false;
                            }, 2000);
                        })
                        .catch(function (err) {
                            console.error("Failed to copy:", err);
                            self.fallbackCopy();
                        });
                } else {
                    self.fallbackCopy();
                }
            },

            fallbackCopy: function () {
                var self = this;
                // Fallback for older browsers
                var textArea = document.createElement("textarea");
                textArea.value = this.giftUrl;
                textArea.style.position = "fixed";
                textArea.style.left = "-9999px";
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand("copy");
                    self.copied = true;
                    setTimeout(function () {
                        self.copied = false;
                    }, 2000);
                } catch (err) {
                    console.error("Fallback copy failed:", err);
                }
                document.body.removeChild(textArea);
            },

            shareWhatsApp: function () {
                var text =
                    "I created a magical Wonderland journey just for you! " +
                    "Scan the QR code or click here to begin: " +
                    this.giftUrl;
                var url = "https://wa.me/?text=" + encodeURIComponent(text);
                window.open(url, "_blank");
            },

            shareEmail: function () {
                var subject = gettext("A Magical Journey Awaits You!");
                var body =
                    "Hi " +
                    this.recipientName +
                    ",\n\n" +
                    gettext(
                        'I created a special "Wonderland of You" journey just for you!',
                    ) +
                    "\n\n" +
                    "Click here to begin your adventure:\n" +
                    this.giftUrl +
                    "\n\n" +
                    "Or use gift code: " +
                    this.giftCode;
                var mailto =
                    "mailto:?subject=" +
                    encodeURIComponent(subject) +
                    "&body=" +
                    encodeURIComponent(body);
                window.location.href = mailto;
            },

            shareNative: function () {
                var self = this;
                if (navigator.share) {
                    navigator
                        .share({
                            title: gettext("A Magical Journey Awaits!"),
                            text:
                                gettext("I created a special Wonderland journey for") +
                                " " +
                                self.recipientName +
                                "!",
                            url: self.giftUrl,
                        })
                        .catch(function () {
                            // Share was cancelled or failed
                        });
                }
            },
        };
    });

    // Gift Create Form Component
    // Multi-step form for creating journey gifts with media uploads
    Alpine.data("giftCreateForm", function () {
        return {
            currentStep: 1,
            // Component root; $el is the event's element inside handlers
            rootEl: null,
            // Chapter 1 image state
            chapter1HasFileFlag: false,
            chapter1Preview: "",
            chapter1FileName: "",
            // Audio file state
            audioHasFileFlag: false,
            audioFileName: "",
            audioFileSize: "",
            audioFileType: "",
            audioError: "",
            // Video file state
            videoHasFileFlag: false,
            videoFileName: "",
            videoFileSize: "",
            videoFileType: "",
            videoError: "",

            // Allowed file types
            allowedAudioTypes: [
                "audio/mpeg",
                "audio/mp3",
                "audio/wav",
                "audio/x-wav",
                "audio/mp4",
                "audio/x-m4a",
                "audio/aac",
            ],
            allowedVideoTypes: ["video/mp4", "video/quicktime", "video/x-m4v"],
            allowedAudioExtensions: [".mp3", ".wav", ".m4a", ".aac"],
            allowedVideoExtensions: [".mp4", ".mov", ".m4v"],

            // Computed getters for CSP compatibility
            get stepOneClass() {
                if (this.currentStep === 1) return "active";
                if (this.currentStep > 1) return "completed";
                return "";
            },
            get stepTwoClass() {
                if (this.currentStep === 2) return "active";
                return "";
            },
            // aria-current on the stepper <li>; false removes the attribute
            get stepOneCurrent() {
                return this.currentStep === 1 ? "step" : false;
            },
            get stepTwoCurrent() {
                return this.currentStep === 2 ? "step" : false;
            },
            get stepOneContentClass() {
                return this.currentStep === 1 ? "active" : "";
            },
            get stepTwoContentClass() {
                return this.currentStep === 2 ? "active" : "";
            },
            get chapter1HasFile() {
                return this.chapter1HasFileFlag ? "has-file" : "";
            },
            get chapter1PreviewClass() {
                return this.chapter1Preview ? "show" : "";
            },
            get audioHasFile() {
                return this.audioHasFileFlag ? "has-file" : "";
            },
            get audioInfoVisible() {
                return this.audioHasFileFlag;
            },
            get audioDefaultVisible() {
                return !this.audioHasFileFlag;
            },
            get audioHasError() {
                return this.audioError !== "";
            },
            get videoHasFile() {
                return this.videoHasFileFlag ? "has-file" : "";
            },
            get videoInfoVisible() {
                return this.videoHasFileFlag;
            },
            get videoDefaultVisible() {
                return !this.videoHasFileFlag;
            },
            get videoHasError() {
                return this.videoError !== "";
            },

            init: function () {
                var self = this;
                this.rootEl = this.$el;
                // A server re-render with errors opens on the step holding the
                // first error, and moves focus to that step's error summary.
                if (this.$el.dataset.initialStep === "2") {
                    this.currentStep = 2;
                }
                this.$nextTick(function () {
                    var summary = self.$el.querySelector(
                        ".step-content.active [data-gift-error-summary]"
                    );
                    if (summary) summary.focus();
                });

                // Listen for file changes on chapter1_image
                var ch1Input = document.getElementById("id_chapter1_image");
                if (ch1Input) {
                    ch1Input.addEventListener("change", function (e) {
                        self.handleChapter1FileChange(e);
                    });
                }

                // Listen for audio file changes
                var audioInput = document.getElementById("id_chapter5_letter_music");
                if (audioInput) {
                    audioInput.addEventListener("change", function (e) {
                        self.handleAudioFileChange(e);
                    });
                }

                // Listen for video file changes
                var videoInput = document.getElementById("id_chapter4_video");
                if (videoInput) {
                    videoInput.addEventListener("change", function (e) {
                        self.handleVideoFileChange(e);
                    });
                }

                // Slideshow thumbnails
                var slideInputs = this.rootEl.querySelectorAll(
                    "[data-slideshow-grid] input[type=file]"
                );
                slideInputs.forEach(function (input) {
                    input.addEventListener("change", function (e) {
                        self.handleSlideshowFileChange(e);
                    });
                });

                // Add visual feedback for all file inputs
                var fileInputs = document.querySelectorAll('input[type="file"]');
                fileInputs.forEach(function (input) {
                    input.addEventListener("change", function (e) {
                        var wrapper = e.target.closest(".file-upload-wrapper");
                        if (wrapper) {
                            if (e.target.files && e.target.files.length > 0) {
                                wrapper.classList.add("has-file");
                            } else {
                                wrapper.classList.remove("has-file");
                            }
                        }
                    });
                });
            },

            // Server-translated client error text (data-i18n-* on the root)
            message: function (name) {
                return this.rootEl.dataset["i18n" + name.charAt(0).toUpperCase() + name.slice(1)] || "";
            },

            // Thumbnail for a slideshow slot, shown inside its upload tile
            handleSlideshowFileChange: function (event) {
                var input = event.target;
                var wrapper = input.closest(".file-upload-wrapper");
                var preview = wrapper && wrapper.querySelector(".file-upload-preview");
                if (!preview) return;
                var img = preview.querySelector("img");
                var file = input.files && input.files[0];
                // Some browsers/OSes leave File.type empty for a real photo, so
                // fall back to the extension (as the audio/video handlers do).
                var isImage =
                    file &&
                    (file.type.indexOf("image/") === 0 ||
                        (!file.type && /\.(jpe?g|png|gif|webp|avif|heic|heif|bmp)$/i.test(file.name)));
                if (isImage) {
                    var reader = new FileReader();
                    reader.onload = function (e) {
                        // A slower read of an earlier pick must not overwrite
                        // the thumbnail of the file the input now holds.
                        if (input.files[0] !== file) return;
                        img.src = e.target.result;
                        preview.classList.add("show");
                    };
                    reader.readAsDataURL(file);
                } else {
                    img.removeAttribute("src");
                    preview.classList.remove("show");
                }
            },

            formatFileSize: function (bytes) {
                if (bytes === 0) return "0 Bytes";
                var k = 1024;
                var sizes = ["Bytes", "KB", "MB", "GB"];
                var i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
            },

            getFileExtension: function (filename) {
                var ext = filename.slice(((filename.lastIndexOf(".") - 1) >>> 0) + 2);
                return ext ? "." + ext.toLowerCase() : "";
            },

            isValidAudioFile: function (file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedAudioTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedAudioExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            isValidVideoFile: function (file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedVideoTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedVideoExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            goToStep2: function () {
                // Basic validation before proceeding
                var recipientName = document.getElementById("id_recipient_name");
                var dateMet = document.getElementById("id_date_first_met");
                var locationMet = document.getElementById("id_location_first_met");

                var isValid = true;
                var firstInvalid = null;

                [
                    [recipientName, !recipientName.value.trim()],
                    [dateMet, !dateMet.value],
                    [locationMet, !locationMet.value.trim()],
                ].forEach(function (pair) {
                    var field = pair[0];
                    field.classList.toggle("is-invalid", pair[1]);
                    field.setAttribute("aria-invalid", pair[1] ? "true" : "false");
                    if (pair[1]) {
                        isValid = false;
                        if (!firstInvalid) firstInvalid = field;
                    }
                });

                if (firstInvalid) firstInvalid.focus();

                if (isValid) {
                    var self = this;
                    this.currentStep = 2;
                    window.scrollTo({ top: 0, behavior: "smooth" });
                    // Keyboard and screen-reader users land on the new step
                    this.$nextTick(function () {
                        var intro = self.rootEl.querySelector(".media-info");
                        if (intro) {
                            intro.focus({ preventScroll: true });
                        }
                    });
                }
            },

            goToStep1: function () {
                var self = this;
                this.currentStep = 1;
                window.scrollTo({ top: 0, behavior: "smooth" });
                this.$nextTick(function () {
                    var first = self.rootEl.querySelector("#id_recipient_name");
                    if (first) first.focus({ preventScroll: true });
                });
            },

            handleChapter1FileChange: function (event) {
                var file = event.target.files[0];
                if (file) {
                    this.chapter1HasFileFlag = true;
                    this.chapter1FileName = file.name;

                    // Create preview for images
                    if (file.type.startsWith("image/")) {
                        var reader = new FileReader();
                        var self = this;
                        reader.onload = function (e) {
                            self.chapter1Preview = e.target.result;
                        };
                        reader.readAsDataURL(file);
                    }
                } else {
                    this.chapter1HasFileFlag = false;
                    this.chapter1Preview = "";
                    this.chapter1FileName = "";
                }
            },

            handleAudioFileChange: function (event) {
                var file = event.target.files[0];
                this.audioError = "";

                if (file) {
                    // Validate file type
                    if (!this.isValidAudioFile(file)) {
                        this.audioError = this.message("audioFormat");
                        this.audioHasFileFlag = false;
                        this.audioFileName = "";
                        this.audioFileSize = "";
                        this.audioFileType = "";
                        event.target.value = "";
                        return;
                    }

                    // Validate file size (10MB max)
                    if (file.size > 10 * 1024 * 1024) {
                        this.audioError = this.message("audioSize");
                        this.audioHasFileFlag = false;
                        this.audioFileName = "";
                        this.audioFileSize = "";
                        this.audioFileType = "";
                        event.target.value = "";
                        return;
                    }

                    this.audioHasFileFlag = true;
                    this.audioFileName = file.name;
                    this.audioFileSize = this.formatFileSize(file.size);
                    this.audioFileType = file.type || "audio";
                } else {
                    this.audioHasFileFlag = false;
                    this.audioFileName = "";
                    this.audioFileSize = "";
                    this.audioFileType = "";
                }
            },

            handleVideoFileChange: function (event) {
                var file = event.target.files[0];
                this.videoError = "";

                if (file) {
                    // Validate file type
                    if (!this.isValidVideoFile(file)) {
                        this.videoError = this.message("videoFormat");
                        this.videoHasFileFlag = false;
                        this.videoFileName = "";
                        this.videoFileSize = "";
                        this.videoFileType = "";
                        event.target.value = "";
                        return;
                    }

                    // Validate file size (50MB max)
                    if (file.size > 50 * 1024 * 1024) {
                        this.videoError = this.message("videoSize");
                        this.videoHasFileFlag = false;
                        this.videoFileName = "";
                        this.videoFileSize = "";
                        this.videoFileType = "";
                        event.target.value = "";
                        return;
                    }

                    this.videoHasFileFlag = true;
                    this.videoFileName = file.name;
                    this.videoFileSize = this.formatFileSize(file.size);
                    this.videoFileType = file.type || "video";
                } else {
                    this.videoHasFileFlag = false;
                    this.videoFileName = "";
                    this.videoFileSize = "";
                    this.videoFileType = "";
                }
            },
        };
    });

    // =========================================================================
    // JOURNEY SYSTEM ALPINE COMPONENTS
    // CSP-compatible components for the Wonderland Journey experience
    // =========================================================================

    /**
     * Journey State Manager
     * Handles auto-save of journey progress, time tracking, and state management.
     * Used in journey_base.html
     *
     * Usage:
     * <div x-data="journeyState"
     *      data-save-url="/api/journey/save-state/"
     *      data-journey-id="7"
     *      data-initial-time="300"
     *      data-initial-points="150">
     *
     * data-journey-id credits the time to the journey the page shows; the
     * server falls back to the map's journey when it is empty.
     */
    Alpine.data("journeyState", function () {
        return {
            startTime: Date.now(),
            lastSaveTime: Date.now(),
            totalTimeSeconds: 0,
            currentPoints: 0,
            saveUrl: "",
            journeyId: "",
            saveInterval: null,

            init: function () {
                var el = this.$el;
                this.saveUrl = el.dataset.saveUrl || "";
                this.journeyId = el.dataset.journeyId || "";
                this.totalTimeSeconds = parseInt(el.dataset.initialTime, 10) || 0;
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Start auto-save interval (every 30 seconds)
                var self = this;
                this.saveInterval = setInterval(function () {
                    self.saveState();
                }, 30000);

                // Save on page unload
                window.addEventListener("beforeunload", function () {
                    self.saveStateBeacon();
                });
            },

            saveState: function () {
                if (!this.saveUrl) return;

                var now = Date.now();
                var timeIncrement = Math.floor((now - this.lastSaveTime) / 1000);
                var self = this;

                if (timeIncrement > 0) {
                    var payload = { time_increment: timeIncrement };
                    if (this.journeyId) {
                        payload.journey_id = this.journeyId;
                    }
                    fetch(this.saveUrl, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": CrushUtils.getCsrfToken(),
                        },
                        body: JSON.stringify(payload),
                    })
                        .then(function (response) {
                            return response.json();
                        })
                        .then(function (data) {
                            if (data.success) {
                                self.lastSaveTime = now;
                                self.totalTimeSeconds = data.total_time;
                            }
                        })
                        .catch(function (error) {
                            console.error("Error saving state:", error);
                        });
                }
            },

            saveStateBeacon: function () {
                if (!this.saveUrl) return;

                var timeIncrement = Math.floor((Date.now() - this.lastSaveTime) / 1000);
                if (timeIncrement > 0) {
                    var formData = new FormData();
                    formData.append("time_increment", timeIncrement);
                    if (this.journeyId) {
                        formData.append("journey_id", this.journeyId);
                    }
                    formData.append("csrfmiddlewaretoken", CrushUtils.getCsrfToken());
                    navigator.sendBeacon(this.saveUrl, formData);
                }
            },

            destroy: function () {
                if (this.saveInterval) {
                    clearInterval(this.saveInterval);
                }
            },
        };
    });

    /**
     * Riddle Challenge Component
     * For riddle-type challenges with text input and hints
     *
     * Usage:
     * <article x-data="riddleChallenge"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-hint-url="/api/journey/hint/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-initial-points="25">
     */
    Alpine.data("riddleChallenge", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            currentPoints: 0,
            submitUrl: "",
            hintUrl: "",
            chapterUrl: "",
            hintsUsed: [],
            // Date input support
            inputType: "",
            dateFormat: "DD/MM/YYYY",
            dateValue: "",
            isDateInput: false,
            isTextInput: true,
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            feedbackClass: "hidden",
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: "",
            hint2BtnClass: "",
            hint3BtnClass: "",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try again!"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.hintUrl = el.dataset.hintUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Date input configuration
                this.inputType = el.dataset.inputType || "";
                this.dateFormat = el.dataset.dateFormat || "DD/MM/YYYY";
                this.isDateInput = this.inputType === "date";
                this.isTextInput = !this.isDateInput;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("answer", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
                this.$watch("hintsUsed", function () {
                    self._updateHintState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center";
                } else {
                    this.feedbackClass = "hidden";
                }
            },

            _updateHintState: function () {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint2BtnClass = this.hint2Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint3BtnClass = this.hint3Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = data.message || self.i18n.errorDefault;
                            self.answer = "";
                            self.isSubmitting = false;
                            self.shakeInput();
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.correct +
                    "</h3>" +
                    '<p class="mb-4">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold mb-6">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            shakeInput: function () {
                var input = this.$el.querySelector(".journey-input");
                if (input) {
                    input.classList.add("journey-animate-shake");
                    setTimeout(function () {
                        input.classList.remove("journey-animate-shake");
                    }, 500);
                }
            },

            unlockHint: function (hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.hintsUsed.push(hintNum);
                            self.currentPoints -= cost;

                            // Dispatch event for hint box to show content
                            self.$dispatch("hint-unlocked", {
                                hintNum: hintNum,
                                hintText: data.hint_text,
                            });
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                    });
            },

            isHintUsed: function (hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector("#answerInput");
                    if (input) this.answer = input.value;
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            // Reads hint number and cost from data attributes
            unlockHintFromButton: function (event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function (event) {
                if (event && event.key === "Enter") {
                    event.preventDefault();
                    this.submitAnswer();
                }
            },

            // CSP-safe: update answer from date input
            // Formats date according to dateFormat setting (DD/MM/YYYY by default)
            updateDateAnswer: function (event) {
                if (event && event.target) {
                    var dateStr = event.target.value; // YYYY-MM-DD format from date input
                    this.dateValue = dateStr;

                    if (dateStr) {
                        var parts = dateStr.split("-");
                        var year = parts[0];
                        var month = parts[1];
                        var day = parts[2];

                        // Format according to dateFormat
                        // Supports: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, MM/DD/YYYY
                        var format = this.dateFormat.toUpperCase();
                        var separator = "/";
                        if (format.indexOf("-") !== -1) separator = "-";
                        else if (format.indexOf(".") !== -1) separator = ".";

                        if (format.indexOf("MM") < format.indexOf("DD")) {
                            // MM/DD/YYYY format
                            this.answer = month + separator + day + separator + year;
                        } else {
                            // DD/MM/YYYY format (default)
                            this.answer = day + separator + month + separator + year;
                        }
                    } else {
                        this.answer = "";
                    }
                }
            },
        };
    });

    /**
     * Word Scramble Challenge Component
     * For word scramble challenges with shuffle functionality
     *
     * Usage:
     * <article x-data="wordScramble"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-hint-url="/api/journey/hint/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-initial-points="25"
     *          data-scrambled-words="WORD SCRAMBLE TEST">
     */
    Alpine.data("wordScramble", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            currentPoints: 0,
            submitUrl: "",
            hintUrl: "",
            chapterUrl: "",
            hintsUsed: [],
            scrambledWords: [],
            displayText: "",
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            feedbackClass: "hidden",
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: "",
            hint2BtnClass: "",
            hint3BtnClass: "",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try again!"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.hintUrl = el.dataset.hintUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                var scrambled = el.dataset.scrambledWords || "";
                this.scrambledWords = scrambled.split(/\s+/).filter(function (w) {
                    return w.trim();
                });
                this.displayText = this.scrambledWords.join("  •  ");

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("answer", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
                this.$watch("hintsUsed", function () {
                    self._updateHintState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center";
                } else {
                    this.feedbackClass = "hidden";
                }
            },

            _updateHintState: function () {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint2BtnClass = this.hint2Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint3BtnClass = this.hint3Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
            },

            shuffleWords: function () {
                var previousOrder = this.scrambledWords.join(" ");
                var newOrder;
                var attempts = 0;

                // Fisher-Yates shuffle for letters within each word
                do {
                    newOrder = this.scrambledWords.map(function (word) {
                        var letters = word.split("");
                        // Fisher-Yates shuffle on letters array
                        for (var i = letters.length - 1; i > 0; i--) {
                            var j = Math.floor(Math.random() * (i + 1));
                            var temp = letters[i];
                            letters[i] = letters[j];
                            letters[j] = temp;
                        }
                        return letters.join("");
                    });
                    // Track previous iteration to avoid duplicate shuffles
                    if (newOrder.join(" ") !== previousOrder) {
                        previousOrder = newOrder.join(" ");
                    }
                    attempts++;
                } while (
                    newOrder.join(" ") === this.scrambledWords.join(" ") &&
                    attempts < 50
                );

                this.scrambledWords = newOrder;
                this.displayText = this.scrambledWords.join("  •  ");
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = data.message || self.i18n.errorDefault;
                            self.answer = "";
                            self.isSubmitting = false;
                            self.shakeInput();
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.correct +
                    "</h3>" +
                    '<p class="mb-4">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold mb-6">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            shakeInput: function () {
                var input = this.$el.querySelector(".journey-input");
                if (input) {
                    input.classList.add("journey-animate-shake");
                    setTimeout(function () {
                        input.classList.remove("journey-animate-shake");
                    }, 500);
                }
            },

            unlockHint: function (hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.hintsUsed.push(hintNum);
                            self.currentPoints -= cost;
                            self.$dispatch("hint-unlocked", {
                                hintNum: hintNum,
                                hintText: data.hint_text,
                            });
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                    });
            },

            isHintUsed: function (hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value.toUpperCase();
                } else {
                    var input = this.$el.querySelector("#answerInput");
                    if (input) this.answer = input.value.toUpperCase();
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            unlockHintFromButton: function (event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function (event) {
                if (event && event.key === "Enter") {
                    event.preventDefault();
                    this.submitAnswer();
                }
            },
        };
    });

    /**
     * Multiple Choice Challenge Component
     * For multiple choice questions with option selection
     *
     * Usage:
     * <article x-data="multipleChoice"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="2">
     */
    Alpine.data("multipleChoice", function () {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            // CSP-safe: plain data properties instead of getters
            hasSelection: false,
            hasNoSelection: true,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right! Try a different answer."),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("selectedOption", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function () {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle("selected", !!isSelected);
                    card.setAttribute("aria-checked", isSelected ? "true" : "false");
                });
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            selectOption: function (optionKey, event) {
                var isSameOption = this.selectedOption === optionKey;
                this.clearSelection();

                if (isSameOption) {
                    this.selectedOption = null;
                    if (event && event.currentTarget && event.currentTarget.blur) {
                        event.currentTarget.blur();
                    }
                    return;
                }

                var target = event.currentTarget;
                target.classList.add("selected");
                target.setAttribute("aria-checked", "true");
                this.selectedOption = optionKey;
            },

            isSelected: function (optionKey) {
                return this.selectedOption === optionKey;
            },

            clearSelection: function () {
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.classList.remove("selected");
                    card.setAttribute("aria-checked", "false");
                    card.blur();
                });
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorDefault;
                            self.markIncorrect();
                            self.selectedOption = null;
                            self.isSubmitting = false;

                            // Auto-hide error after 3 seconds
                            setTimeout(function () {
                                if (self.feedbackType === "error") {
                                    self.feedback = "";
                                    self.feedbackType = "";
                                    // Explicitly update feedback state to trigger visual updates
                                    self._updateFeedbackState();
                                }
                            }, 3000);
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var isChapter2 = this.chapterNumber === 2;
                var iconHtml = isChapter2
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter2 ? this.i18n.thankYou : this.i18n.correct;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            markIncorrect: function () {
                var selectedCard = this.$el.querySelector(".option-card.selected");
                if (selectedCard) {
                    selectedCard.classList.remove("selected");
                    selectedCard.classList.add("incorrect", "journey-animate-shake");
                    setTimeout(function () {
                        selectedCard.classList.remove(
                            "incorrect",
                            "journey-animate-shake",
                        );
                    }, 1000);
                }
            },

            handleKeypress: function (optionKey, event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function (event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || "";
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function (event) {
                if (event && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            },
        };
    });

    /**
     * Timeline Sort Challenge Component
     * For timeline sorting challenges with Sortable.js
     *
     * Usage:
     * <article x-data="timelineSort"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/">
     */
    Alpine.data("timelineSort", function () {
        return {
            challengeId: 0,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            sortable: null,
            isTouchDevice: false,
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: false,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            instructionText: "",
            positionAnnouncement: "",
            // i18n translations (loaded from data attributes)
            i18n: {
                perfect: gettext("Perfect!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try rearranging the events!"),
                errorGeneric: gettext("An error occurred. Please try again."),
                instructionDesktop: gettext(
                    "Drag and drop the events, or use the up and down buttons, to arrange them in chronological order",
                ),
                instructionTouch: gettext(
                    "Touch and drag the events, or use the up and down buttons, to arrange them in chronological order",
                ),
                moved: gettext("{item} moved to position {position} of {total}"),
            },

            init: function () {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.isTouchDevice =
                    "ontouchstart" in window || navigator.maxTouchPoints > 0;

                // Load i18n translations from data attributes
                if (el.dataset.i18nPerfect) this.i18n.perfect = el.dataset.i18nPerfect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;
                if (el.dataset.i18nInstructionDesktop)
                    this.i18n.instructionDesktop = el.dataset.i18nInstructionDesktop;
                if (el.dataset.i18nInstructionTouch)
                    this.i18n.instructionTouch = el.dataset.i18nInstructionTouch;
                if (el.dataset.i18nMoved) this.i18n.moved = el.dataset.i18nMoved;

                // CSP-safe: update instruction text based on device type
                this._updateInstructionText();

                // CSP-safe: use $watch to update derived state
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });

                // Initialize Sortable.js after DOM is ready
                this.$nextTick(function () {
                    self.initSortable();
                    self.shuffleItems();

                    // CSP-safe: Manually bind click handler as fallback
                    // Alpine CSP build sometimes fails to bind @click on dynamically shown elements
                    var submitBtn = el.querySelector("button.journey-btn-primary");
                    if (submitBtn) {
                        submitBtn.addEventListener("click", function (e) {
                            e.preventDefault();
                            self.submitAnswer();
                        });
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var canSubmit = !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            _updateInstructionText: function () {
                this.instructionText = this.isTouchDevice
                    ? this.i18n.instructionTouch
                    : this.i18n.instructionDesktop;
            },

            initSortable: function () {
                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems || typeof Sortable === "undefined") return;

                var self = this;
                this.sortable = new Sortable(timelineItems, {
                    animation: 200,
                    easing: "cubic-bezier(1, 0, 0, 1)",
                    ghostClass: "sortable-ghost",
                    chosenClass: "sortable-chosen",
                    dragClass: "sortable-drag",
                    handle: ".timeline-item",
                    // Taps on the move buttons must not start a drag.
                    filter: ".timeline-move",
                    preventOnFilter: false,
                    forceFallback: false,
                    fallbackTolerance: 3,
                    touchStartThreshold: 5,
                    delay: 0,
                    delayOnTouchOnly: true,
                    onEnd: function (evt) {
                        self.updateNumbers();
                        self._announcePosition(evt.item);
                    },
                });
            },

            updateNumbers: function () {
                var items = this.$root.querySelectorAll(".timeline-item");
                var last = items.length - 1;
                items.forEach(function (item, index) {
                    var numberEl = item.querySelector(".timeline-number");
                    if (numberEl) {
                        numberEl.textContent = index + 1;
                    }
                    var up = item.querySelector(".timeline-move-up");
                    var down = item.querySelector(".timeline-move-down");
                    if (up) up.disabled = index === 0;
                    if (down) down.disabled = index === last;
                });
            },

            // Keyboard/screen-reader alternative to dragging.
            moveUp: function () {
                this._moveItem(this.$el, -1);
            },

            moveDown: function () {
                this._moveItem(this.$el, 1);
            },

            _moveItem: function (button, direction) {
                var item = button.closest(".timeline-item");
                var sibling =
                    direction < 0 ? item.previousElementSibling : item.nextElementSibling;
                if (!sibling) return;
                if (direction < 0) {
                    item.parentNode.insertBefore(item, sibling);
                } else {
                    item.parentNode.insertBefore(sibling, item);
                }
                this.updateNumbers();
                this._announcePosition(item);
                // Moving the node drops focus; keep it on a usable button of the item.
                var other = item.querySelector(
                    direction < 0 ? ".timeline-move-down" : ".timeline-move-up",
                );
                (button.disabled ? other : button).focus();
            },

            _announcePosition: function (item) {
                var items = Array.from(this.$root.querySelectorAll(".timeline-item"));
                var text = item.querySelector(".timeline-text");
                var label = text ? text.textContent.trim() : "";
                // Replacer functions: the event text is inserted literally (a
                // "$&" or "$1" in it is not a replacement pattern), and last,
                // so a "{total}" inside it is never substituted.
                this.positionAnnouncement = this.i18n.moved
                    .replace("{position}", function () {
                        return String(items.indexOf(item) + 1);
                    })
                    .replace("{total}", function () {
                        return String(items.length);
                    })
                    .replace("{item}", function () {
                        return label;
                    });
            },

            shuffleItems: function () {
                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems) return;

                var items = Array.from(timelineItems.children);
                // Fisher-Yates shuffle
                for (var i = items.length - 1; i > 0; i--) {
                    var j = Math.floor(Math.random() * (i + 1));
                    timelineItems.appendChild(items[j]);
                }
                this.updateNumbers();
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems) return;

                var items = timelineItems.querySelectorAll(".timeline-item");
                var order = Array.from(items).map(function (item) {
                    return item.dataset.originalIndex;
                });
                var answer = order.join(",");

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: answer,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                            self.disableSorting();
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorDefault;
                            self.isSubmitting = false;

                            // Auto-hide error after 3 seconds
                            setTimeout(function () {
                                if (self.feedbackType === "error") {
                                    self.feedback = "";
                                    self.feedbackType = "";
                                    // Explicitly update feedback state to trigger visual updates
                                    self._updateFeedbackState();
                                }
                            }, 3000);
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.perfect +
                    "</h3>" +
                    '<p class="text-lg my-5">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            disableSorting: function () {
                if (this.sortable) {
                    this.sortable.option("disabled", true);
                }
                var items = this.$el.querySelectorAll(".timeline-item");
                items.forEach(function (item) {
                    item.style.cursor = "default";
                });
                this.$root.querySelectorAll(".timeline-move").forEach(function (btn) {
                    btn.disabled = true;
                });
            },
        };
    });

    /**
     * Would You Rather Challenge Component
     * For two-option choice questions
     *
     * Usage:
     * <article x-data="wouldYouRather"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="4">
     */
    Alpine.data("wouldYouRather", function () {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            // CSP-safe: plain data properties instead of getters
            hasSelection: false,
            hasNoSelection: true,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            // i18n translations (loaded from data attributes)
            i18n: {
                greatChoice: gettext("Great choice!"),
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nGreatChoice)
                    this.i18n.greatChoice = el.dataset.i18nGreatChoice;
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("selectedOption", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function () {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle("selected", !!isSelected);
                    card.setAttribute("aria-checked", isSelected ? "true" : "false");
                });
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            selectOption: function (optionKey, event) {
                var isSameOption = this.selectedOption === optionKey;
                this.clearSelection();

                if (isSameOption) {
                    this.selectedOption = null;
                    return;
                }

                var target = event.currentTarget;
                target.classList.add("selected");
                target.setAttribute("aria-checked", "true");
                this.selectedOption = optionKey;
            },

            clearSelection: function () {
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.classList.remove("selected");
                    card.setAttribute("aria-checked", "false");
                    card.blur();
                });
            },

            isSelected: function (optionKey) {
                return this.selectedOption === optionKey;
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                            self.disableOptions();
                        } else {
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var isChapter4 = this.chapterNumber === 4;
                var iconHtml = isChapter4
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter4 ? this.i18n.thankYou : this.i18n.greatChoice;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            disableOptions: function () {
                var optionsSection = this.$el.querySelector("#optionsSection");
                if (optionsSection) {
                    optionsSection.style.opacity = "0.5";
                }
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.style.cursor = "default";
                    card.style.pointerEvents = "none";
                });
            },

            handleKeypress: function (optionKey, event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function (event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || "";
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function (event) {
                if (event && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            },
        };
    });

    /**
     * Open Text Challenge Component
     * For free-form text response challenges
     *
     * Usage:
     * <article x-data="openText"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="2"
     *          data-min-length="10"
     *          data-max-length="2000">
     */
    Alpine.data("openText", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            minLength: 10,
            maxLength: 2000,
            // CSP-safe: plain data properties instead of getters
            charCount: 0,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isSubmittingState: false,
            isNotSubmitting: true,
            feedbackClass: "hidden mt-6",
            charCounterClass: "char-counter",
            // i18n translations (loaded from data attributes)
            i18n: {
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;
                this.minLength = parseInt(el.dataset.minLength, 10) || 10;
                this.maxLength = parseInt(el.dataset.maxLength, 10) || 2000;

                // Load i18n translations from data attributes
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                this.$watch("answer", function () {
                    self._updateSubmitState();
                    self._updateCharCounterClass();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });

                // Auto-focus on input
                this.$nextTick(function () {
                    var textInput = self.$el.querySelector("#textInput");
                    if (textInput) {
                        setTimeout(function () {
                            textInput.focus();
                        }, 500);
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.charCount = this.answer.length;
                var hasMinLength = this.answer.trim().length >= this.minLength;
                var canSubmit = hasMinLength && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isSubmittingState = this.isSubmitting;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            _updateCharCounterClass: function () {
                if (this.charCount > this.maxLength * 0.9) {
                    this.charCounterClass = "char-counter error";
                } else if (this.charCount > this.maxLength * 0.75) {
                    this.charCounterClass = "char-counter warning";
                } else {
                    this.charCounterClass = "char-counter";
                }
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorGeneric;
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var chapterNum = this.chapterNumber;
                var isQuestionnaire =
                    chapterNum === 2 || chapterNum === 4 || chapterNum === 5;
                var iconHtml = isQuestionnaire
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = this.i18n.thankYou;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector("#textInput");
                    if (input) this.answer = input.value;
                }
            },
        };
    });

    /**
     * Hint Box Component
     * For displaying unlocked hints
     *
     * Usage:
     * <div x-data="hintBox" data-hint-num="1" @hint-unlocked.window="showHint($event.detail)">
     */
    Alpine.data("hintBox", function () {
        return {
            hintNum: 0,
            hintText: "",
            isActive: false,
            // CSP-safe: plain data properties instead of getters
            showHint: false,
            hintClass: "journey-hint-box",

            init: function () {
                this.hintNum = parseInt(this.$el.dataset.hintNum, 10) || 0;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("isActive", function () {
                    self._updateState();
                });
            },

            _updateState: function () {
                this.showHint = this.isActive;
                this.hintClass = this.isActive
                    ? "journey-hint-box active"
                    : "journey-hint-box";
            },

            // CSP-safe: wrapper that receives event object
            handleHintUnlockedEvent: function (event) {
                var detail = event && event.detail ? event.detail : {};
                this.handleHintUnlocked(detail);
            },

            handleHintUnlocked: function (detail) {
                if (detail.hintNum === this.hintNum) {
                    this.hintText = detail.hintText;
                    this.isActive = true;
                }
            },
        };
    });

    // Journey Final Response component (Chapter 6 - The Final Question)
    // Handles yes/thinking response submission with loading states
    Alpine.data("finalResponse", function () {
        return {
            isSubmitting: false,
            statusMessage: "",
            statusType: "", // 'info', 'success', 'error'

            // CSP-safe computed getters
            get isNotSubmitting() {
                return !this.isSubmitting;
            },
            get showStatus() {
                return this.statusMessage !== "";
            },
            get statusClass() {
                if (this.statusType === "success") return "journey-status-success";
                if (this.statusType === "error") return "journey-status-error";
                return "journey-status-info";
            },

            init: function () {
                // Read config from data attributes
                this.submitUrl = this.$el.dataset.submitUrl || "";
                this.i18nSubmitting =
                    this.$el.dataset.i18nSubmitting || "Submitting your response...";
                this.i18nSuccess =
                    this.$el.dataset.i18nSuccess || "Thank you for your response!";
                this.i18nError =
                    this.$el.dataset.i18nError ||
                    gettext("An error occurred. Please try again.");
                this.i18nNetworkError =
                    this.$el.dataset.i18nNetworkError ||
                    gettext("Network error. Please check your connection and try again.");
            },

            submitYes: function () {
                this._submit("yes");
            },

            submitThinking: function () {
                this._submit("thinking");
            },

            _submit: function (response) {
                var self = this;

                if (this.isSubmitting) return;
                this.isSubmitting = true;
                this.statusMessage = "\u23F3 " + this.i18nSubmitting;
                this.statusType = "info";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ response: response }),
                })
                    .then(function (apiResponse) {
                        return apiResponse.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.statusMessage =
                                "\u2705 " + self.i18nSuccess + " \uD83D\uDC96";
                            self.statusType = "success";
                            // Reload page after 2 seconds to show the confirmed response
                            setTimeout(function () {
                                window.location.reload();
                            }, 2000);
                        } else {
                            self.statusMessage =
                                "\u26A0\uFE0F " + (data.message || self.i18nError);
                            self.statusType = "error";
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error submitting final response:", error);
                        self.statusMessage = "\u26A0\uFE0F " + self.i18nNetworkError;
                        self.statusType = "error";
                        self.isSubmitting = false;
                    });
            },
        };
    });

    // Photo slideshow component for journey rewards
    // Reads images from data-images JSON attribute
    // Supports keyboard navigation, touch swipe, and auto-play
    Alpine.data("photoSlideshow", function () {
        return {
            images: [],
            currentIndex: 0,
            isLoading: true,
            touchStartX: 0,
            touchEndX: 0,
            autoPlayInterval: null,
            autoPlayEnabled: false,

            // Computed getters for CSP compatibility
            get hasMultipleImages() {
                return this.images.length > 1;
            },
            get hasSingleImage() {
                return this.images.length === 1;
            },
            get hasNoImages() {
                return this.images.length === 0;
            },
            get totalImages() {
                return this.images.length;
            },
            get currentImageNumber() {
                return this.currentIndex + 1;
            },
            get currentImage() {
                return this.images[this.currentIndex] || null;
            },
            get currentImageUrl() {
                var img = this.images[this.currentIndex];
                return img ? img.url : "";
            },
            get canGoNext() {
                return this.currentIndex < this.images.length - 1;
            },
            get canGoPrev() {
                return this.currentIndex > 0;
            },
            get isNotLoading() {
                return !this.isLoading;
            },
            get progressPercent() {
                if (this.images.length <= 1) return 100;
                return ((this.currentIndex + 1) / this.images.length) * 100;
            },

            // Dot navigation helpers - returns array of booleans for each dot
            get dotStates() {
                var self = this;
                return this.images.map(function (_, idx) {
                    return idx === self.currentIndex;
                });
            },

            // Individual dot state getters (for up to 5 images)
            get isDot0Active() {
                return this.currentIndex === 0;
            },
            get isDot1Active() {
                return this.currentIndex === 1;
            },
            get isDot2Active() {
                return this.currentIndex === 2;
            },
            get isDot3Active() {
                return this.currentIndex === 3;
            },
            get isDot4Active() {
                return this.currentIndex === 4;
            },
            get hasDot0() {
                return this.images.length > 0;
            },
            get hasDot1() {
                return this.images.length > 1;
            },
            get hasDot2() {
                return this.images.length > 2;
            },
            get hasDot3() {
                return this.images.length > 3;
            },
            get hasDot4() {
                return this.images.length > 4;
            },

            // CSP-safe dot class getters (avoid ternary in template)
            get dot0ActiveClass() {
                return this.isDot0Active ? "slideshow-dot-active" : "";
            },
            get dot1ActiveClass() {
                return this.isDot1Active ? "slideshow-dot-active" : "";
            },
            get dot2ActiveClass() {
                return this.isDot2Active ? "slideshow-dot-active" : "";
            },
            get dot3ActiveClass() {
                return this.isDot3Active ? "slideshow-dot-active" : "";
            },
            get dot4ActiveClass() {
                return this.isDot4Active ? "slideshow-dot-active" : "";
            },

            // CSP-safe aria-selected getters
            get dot0AriaSelected() {
                return this.isDot0Active ? "true" : "false";
            },
            get dot1AriaSelected() {
                return this.isDot1Active ? "true" : "false";
            },
            get dot2AriaSelected() {
                return this.isDot2Active ? "true" : "false";
            },
            get dot3AriaSelected() {
                return this.isDot3Active ? "true" : "false";
            },
            get dot4AriaSelected() {
                return this.isDot4Active ? "true" : "false";
            },

            init: function () {
                var self = this;

                // Load images from script tag (preferred) or data attribute (fallback)
                var scriptId = this.$el.getAttribute("data-images-from");
                if (scriptId) {
                    // Load from script tag containing JSON (avoids HTML attribute escaping issues)
                    var scriptEl = document.getElementById(scriptId);
                    if (scriptEl) {
                        try {
                            this.images = JSON.parse(scriptEl.textContent);
                        } catch (e) {
                            console.error(
                                "[PhotoSlideshow] Failed to parse images from script tag:",
                                e,
                            );
                            this.images = [];
                        }
                    }
                } else {
                    // Fallback: load from data-images attribute
                    var imagesData = this.$el.getAttribute("data-images");
                    if (imagesData) {
                        try {
                            this.images = JSON.parse(imagesData);
                        } catch (e) {
                            console.error(
                                "[PhotoSlideshow] Failed to parse images:",
                                e,
                            );
                            this.images = [];
                        }
                    }
                }

                // Preload first image
                if (this.images.length > 0) {
                    var img = new Image();
                    img.onload = function () {
                        self.isLoading = false;
                    };
                    img.onerror = function () {
                        self.isLoading = false;
                    };
                    img.src = this.images[0].url;
                } else {
                    this.isLoading = false;
                }

                // Setup keyboard navigation
                document.addEventListener("keydown", function (e) {
                    if (e.key === "ArrowLeft") {
                        self.prev();
                    } else if (e.key === "ArrowRight") {
                        self.next();
                    }
                });

                // Preload all images in background
                this._preloadImages();
            },

            _preloadImages: function () {
                var self = this;
                this.images.forEach(function (imgData, idx) {
                    if (idx === 0) return; // Already loaded
                    var img = new Image();
                    img.src = imgData.url;
                });
            },

            next: function () {
                if (this.currentIndex < this.images.length - 1) {
                    this.currentIndex++;
                } else {
                    // Loop to beginning
                    this.currentIndex = 0;
                }
            },

            prev: function () {
                if (this.currentIndex > 0) {
                    this.currentIndex--;
                } else {
                    // Loop to end
                    this.currentIndex = this.images.length - 1;
                }
            },

            goTo: function (index) {
                if (index >= 0 && index < this.images.length) {
                    this.currentIndex = index;
                }
            },

            // Individual goTo methods for CSP compatibility (avoid inline expressions)
            goToDot0: function () {
                this.goTo(0);
            },
            goToDot1: function () {
                this.goTo(1);
            },
            goToDot2: function () {
                this.goTo(2);
            },
            goToDot3: function () {
                this.goTo(3);
            },
            goToDot4: function () {
                this.goTo(4);
            },

            // Touch event handlers for swipe support
            handleTouchStart: function (event) {
                this.touchStartX = event.touches[0].clientX;
            },

            handleTouchMove: function (event) {
                this.touchEndX = event.touches[0].clientX;
            },

            handleTouchEnd: function () {
                var diff = this.touchStartX - this.touchEndX;
                var threshold = 50; // Minimum swipe distance

                if (Math.abs(diff) > threshold) {
                    if (diff > 0) {
                        // Swiped left - go next
                        this.next();
                    } else {
                        // Swiped right - go prev
                        this.prev();
                    }
                }

                // Reset touch positions
                this.touchStartX = 0;
                this.touchEndX = 0;
            },

            // Auto-play functionality
            toggleAutoPlay: function () {
                var self = this;
                if (this.autoPlayEnabled) {
                    this.stopAutoPlay();
                } else {
                    this.autoPlayEnabled = true;
                    this.autoPlayInterval = setInterval(function () {
                        self.next();
                    }, 3000);
                }
            },

            stopAutoPlay: function () {
                this.autoPlayEnabled = false;
                if (this.autoPlayInterval) {
                    clearInterval(this.autoPlayInterval);
                    this.autoPlayInterval = null;
                }
            },

            // Download current image
            downloadCurrent: function () {
                if (this.currentImage) {
                    var link = document.createElement("a");
                    link.href = this.currentImage.url;
                    link.download = "photo-" + (this.currentIndex + 1) + ".jpg";
                    link.target = "_blank";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }
            },
        };
    });

    // Letter audio player with autoplay fallback for browser compatibility
    // Most browsers block autoplay without user interaction, so we provide
    // a graceful fallback with a "click to play" prompt
    Alpine.data("letterAudioPlayer", function () {
        return {
            isPlaying: false,
            autoplayBlocked: false,
            audioElement: null,
            hasInteracted: false,

            // Computed getters for CSP compatibility
            get showPlayPrompt() {
                return this.autoplayBlocked && !this.isPlaying;
            },
            get isNotPlaying() {
                return !this.isPlaying;
            },
            get playButtonClass() {
                return this.isPlaying ? "letter-audio-playing" : "letter-audio-paused";
            },

            init: function () {
                var self = this;
                this.audioElement = this.$refs.audio;

                if (this.audioElement) {
                    // Listen for play/pause events
                    this.audioElement.addEventListener("play", function () {
                        self.isPlaying = true;
                        self.autoplayBlocked = false;
                    });

                    this.audioElement.addEventListener("pause", function () {
                        self.isPlaying = false;
                    });

                    this.audioElement.addEventListener("ended", function () {
                        self.isPlaying = false;
                    });

                    // Attempt autoplay
                    this.attemptAutoplay();
                }
            },

            attemptAutoplay: function () {
                var self = this;
                if (!this.audioElement) return;

                // Try to play - browsers may block this
                var playPromise = this.audioElement.play();

                if (playPromise !== undefined) {
                    playPromise
                        .then(function () {
                            // Autoplay succeeded
                            self.isPlaying = true;
                            self.autoplayBlocked = false;
                        })
                        .catch(function (error) {
                            // Autoplay was blocked by browser
                            self.autoplayBlocked = true;
                            self.isPlaying = false;
                        });
                }
            },

            togglePlay: function () {
                if (!this.audioElement) return;

                this.hasInteracted = true;

                if (this.isPlaying) {
                    this.audioElement.pause();
                } else {
                    this.audioElement.play();
                }
            },

            playMusic: function () {
                if (!this.audioElement) return;

                this.hasInteracted = true;
                this.audioElement.play();
            },
        };
    });
});
