/**
 * Alpine.js CSP components for Crush.lu — quiz bundle.
 *
 * Quiz editor for coaches (coach_quiz_question_form.html). The live quiz
 * screens load quiz-live.js / quiz-display.js instead.
 * Loaded with crush_lu/partials/alpine_bundle.html, before Alpine starts.
 * Build: `npm run build:js`.
 */

document.addEventListener("alpine:init", function () {
    // -----------------------------------------------------------------------
    // Quiz Question Form – coach quiz configuration (multilingual)
    // -----------------------------------------------------------------------
    Alpine.data("quizQuestionForm", function () {
        return {
            questionType: "multiple_choice",
            activeLang: "en",
            // Per-language choices arrays
            choicesEn: [],
            choicesDe: [],
            choicesFr: [],
            correctTrueFalse: "true",
            mediaKind: "none",
            mediaSource: "upload",
            mediaMaxBytes: 25 * 1024 * 1024,
            fileName: "",
            fileMeta: "",
            fileFeedback: "",
            fileAccepted: true,
            previewUrl: "",

            init: function () {
                var el = this.$el;
                this.questionType =
                    el.getAttribute("data-question-type") || "multiple_choice";
                this.mediaKind =
                    el.getAttribute("data-media-kind") || "none";
                this.mediaSource =
                    el.getAttribute("data-media-source") || "upload";
                this.mediaMaxBytes = parseInt(
                    el.getAttribute("data-media-max-bytes") || "26214400",
                    10,
                );

                var langs = ["en", "de", "fr"];
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < langs.length; i++) {
                    var raw = el.getAttribute("data-choices-" + langs[i]);
                    if (raw) {
                        try {
                            this[props[i]] = JSON.parse(raw);
                        } catch (e) {
                            this[props[i]] = [];
                        }
                    }
                }

                if (this.questionType === "multiple_choice") {
                    for (var j = 0; j < props.length; j++) {
                        if (this[props[j]].length === 0) {
                            this[props[j]] = [
                                { text: "", isCorrect: false },
                                { text: "", isCorrect: false },
                            ];
                        }
                    }
                }

                if (this.questionType === "true_false") {
                    var ref = this.choicesEn;
                    var correct = null;
                    for (var k = 0; k < ref.length; k++) {
                        if (ref[k].isCorrect) {
                            correct = ref[k];
                            break;
                        }
                    }
                    this.correctTrueFalse = correct
                        ? correct.text.toLowerCase()
                        : "true";
                }

                this._renderAllChoices();
            },

            destroy: function () {
                this._revokePreviewUrl();
            },

            // Current language's choices (getter)
            get currentChoices() {
                if (this.activeLang === "de") return this.choicesDe;
                if (this.activeLang === "fr") return this.choicesFr;
                return this.choicesEn;
            },

            get isMultipleChoice() {
                return this.questionType === "multiple_choice";
            },
            get isTrueFalse() {
                return this.questionType === "true_false";
            },
            get isOpenEnded() {
                return this.questionType === "open_ended";
            },

            // Media stimulus visibility
            get hasMedia() {
                return this.mediaKind !== "none";
            },
            selectMediaKind: function () {
                var selected = this.$el.querySelector(
                    'input[name="media_kind"]:checked',
                );
                var nextKind = selected ? selected.value : "none";
                if (nextKind !== this.mediaKind) {
                    this.mediaKind = nextKind;
                    this._clearLocalPreview(true);
                }
            },
            selectMediaSource: function () {
                var selected = this.$el.querySelector(
                    'input[name="media_source_choice"]:checked',
                );
                this.mediaSource = selected ? selected.value : "upload";
                if (this.mediaSource === "external") {
                    this._clearLocalPreview(true);
                }
            },
            get isUploadSource() {
                return this.mediaSource === "upload";
            },
            get isExternalSource() {
                return this.mediaSource === "external";
            },
            get mediaNoneClass() {
                return this.mediaKind === "none" ? "is-selected" : "";
            },
            get mediaImageClass() {
                return this.mediaKind === "image" ? "is-selected" : "";
            },
            get mediaVideoClass() {
                return this.mediaKind === "video" ? "is-selected" : "";
            },
            get mediaAudioClass() {
                return this.mediaKind === "audio" ? "is-selected" : "";
            },
            get uploadSourceClass() {
                return this.isUploadSource ? "is-selected" : "";
            },
            get externalSourceClass() {
                return this.isExternalSource ? "is-selected" : "";
            },
            get mediaAccept() {
                if (this.mediaKind === "image") return "image/*";
                if (this.mediaKind === "video") return "video/*";
                if (this.mediaKind === "audio")
                    return "audio/*,application/ogg";
                return "";
            },
            get hasFileFeedback() {
                return Boolean(this.fileFeedback);
            },
            get fileFeedbackClass() {
                return this.fileAccepted
                    ? "text-green-600 dark:text-green-400"
                    : "text-red-600 dark:text-red-400";
            },
            get hasLocalPreview() {
                return Boolean(this.previewUrl) && this.fileAccepted;
            },
            get previewIsImage() {
                return this.hasLocalPreview && this.mediaKind === "image";
            },
            get previewIsVideo() {
                return this.hasLocalPreview && this.mediaKind === "video";
            },
            get previewIsAudio() {
                return this.hasLocalPreview && this.mediaKind === "audio";
            },
            selectMediaFile: function () {
                var input = this.$refs.mediaFile;
                var file = input && input.files ? input.files[0] : null;
                this._revokePreviewUrl();
                this.fileName = file ? file.name : "";
                this.fileMeta = file
                    ? this._formatFileSize(file.size) +
                      (file.type ? " · " + file.type : "")
                    : "";
                this.fileFeedback = "";
                this.fileAccepted = true;
                if (!file) return;

                var expected = this.mediaKind + "/";
                var typeMatches =
                    file.type.indexOf(expected) === 0 ||
                    (this.mediaKind === "audio" &&
                        file.type === "application/ogg");
                if (!typeMatches) {
                    this.fileAccepted = false;
                    this.fileFeedback =
                        this.$el.getAttribute("data-file-type-mismatch") ||
                        "This file type does not match the selected media type.";
                } else if (file.size > this.mediaMaxBytes) {
                    this.fileAccepted = false;
                    this.fileFeedback =
                        this.$el.getAttribute("data-file-too-large") ||
                        "This file is too large.";
                } else {
                    this.fileFeedback =
                        this.$el.getAttribute("data-file-ready") ||
                        "Ready to preview before saving.";
                    if (window.URL && window.URL.createObjectURL) {
                        this.previewUrl = window.URL.createObjectURL(file);
                    }
                }
                if (input && input.setCustomValidity) {
                    input.setCustomValidity(
                        this.fileAccepted ? "" : this.fileFeedback,
                    );
                }
            },
            _formatFileSize: function (bytes) {
                if (bytes < 1024) return bytes + " B";
                if (bytes < 1024 * 1024)
                    return (bytes / 1024).toFixed(1) + " KB";
                return (bytes / (1024 * 1024)).toFixed(1) + " MB";
            },
            _revokePreviewUrl: function () {
                if (
                    this.previewUrl &&
                    window.URL &&
                    window.URL.revokeObjectURL
                ) {
                    window.URL.revokeObjectURL(this.previewUrl);
                }
                this.previewUrl = "";
            },
            _clearLocalPreview: function (resetInput) {
                this._revokePreviewUrl();
                this.fileName = "";
                this.fileMeta = "";
                this.fileFeedback = "";
                this.fileAccepted = true;
                var input = this.$refs.mediaFile;
                if (input && input.setCustomValidity) input.setCustomValidity("");
                if (resetInput && input) input.value = "";
            },

            // Language tab switching
            get isLangEn() {
                return this.activeLang === "en";
            },
            get isLangDe() {
                return this.activeLang === "de";
            },
            get isLangFr() {
                return this.activeLang === "fr";
            },
            setLangEn: function () {
                this.activeLang = "en";
            },
            setLangDe: function () {
                this.activeLang = "de";
            },
            setLangFr: function () {
                this.activeLang = "fr";
            },
            get langEnClass() {
                return this.activeLang === "en"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get langDeClass() {
                return this.activeLang === "de"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get langFrClass() {
                return this.activeLang === "fr"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },

            selectType: function () {
                var sel = this.$refs.typeSelect;
                if (!sel) return;
                this.questionType = sel.value;
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                if (this.questionType === "true_false") {
                    for (var i = 0; i < props.length; i++) {
                        this[props[i]] = [
                            { text: "True", isCorrect: true },
                            { text: "False", isCorrect: false },
                        ];
                    }
                    this.correctTrueFalse = "true";
                } else if (this.questionType === "multiple_choice") {
                    for (var j = 0; j < props.length; j++) {
                        if (this[props[j]].length < 2) {
                            this[props[j]] = [
                                { text: "", isCorrect: false },
                                { text: "", isCorrect: false },
                            ];
                        }
                    }
                }
                this._renderAllChoices();
            },

            addChoice: function () {
                this.choicesEn.push({ text: "", isCorrect: false });
                this.choicesDe.push({ text: "", isCorrect: false });
                this.choicesFr.push({ text: "", isCorrect: false });
                this._renderAllChoices();
            },

            removeChoice: function () {
                var btn = this.$el;
                var idx = parseInt(btn.getAttribute("data-index"), 10);
                if (isNaN(idx)) return;
                this.choicesEn.splice(idx, 1);
                this.choicesDe.splice(idx, 1);
                this.choicesFr.splice(idx, 1);
            },

            setCorrect: function () {
                var btn = this.$el;
                var idx = parseInt(btn.getAttribute("data-index"), 10);
                if (isNaN(idx)) return;
                // Sync is_correct across all languages
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    for (var i = 0; i < arr.length; i++) {
                        arr[i].isCorrect = i === idx;
                    }
                }
            },

            setTrueFalseTrue: function () {
                this.correctTrueFalse = "true";
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < props.length; i++) {
                    this[props[i]] = [
                        { text: "True", isCorrect: true },
                        { text: "False", isCorrect: false },
                    ];
                }
                this._renderAllChoices();
            },

            setTrueFalseFalse: function () {
                this.correctTrueFalse = "false";
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < props.length; i++) {
                    this[props[i]] = [
                        { text: "True", isCorrect: false },
                        { text: "False", isCorrect: true },
                    ];
                }
                this._renderAllChoices();
            },

            updateChoiceText: function () {
                var input = this.$el;
                var idx = parseInt(input.getAttribute("data-index"), 10);
                var choices = this.currentChoices;
                if (isNaN(idx) || !choices[idx]) return;
                choices[idx].text = input.value;
            },

            // --- CSP-safe imperative DOM rendering for choices ---
            _placeholders: {
                en: "Choice text...",
                de: "Antworttext...",
                fr: "Texte du choix...",
            },

            _renderAllChoices: function () {
                this._renderChoices("en");
                this._renderChoices("de");
                this._renderChoices("fr");
            },

            _renderChoices: function (lang) {
                var container = this.$el.querySelector(
                    '[data-choices-container="' + lang + '"]',
                );
                if (!container) return;

                var self = this;
                var propName = "choices" + lang.charAt(0).toUpperCase() + lang.slice(1);
                var choices = this[propName];
                var placeholder = this._placeholders[lang] || "Choice text...";
                container.innerHTML = "";

                for (var i = 0; i < choices.length; i++) {
                    (function (index) {
                        var choice = choices[index];
                        var row = document.createElement("div");
                        row.className = "flex items-center gap-2 mb-2";

                        // Correct-answer button
                        var btn = document.createElement("button");
                        btn.type = "button";
                        btn.className =
                            "shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors" +
                            (choice.isCorrect
                                ? " border-green-500 bg-green-500"
                                : " border-gray-300 dark:border-gray-600 hover:border-green-400");
                        if (choice.isCorrect) {
                            btn.innerHTML =
                                '<svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
                                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>';
                        }
                        btn.addEventListener("click", function () {
                            self._setCorrectByIndex(index);
                        });
                        row.appendChild(btn);

                        // Text input
                        var input = document.createElement("input");
                        input.type = "text";
                        input.value = choice.text;
                        input.placeholder = placeholder;
                        input.className =
                            "flex-1 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm focus:ring-2 focus:ring-crush-purple focus:border-transparent";
                        input.addEventListener("input", function () {
                            self._updateChoiceTextByIndex(lang, index, input.value);
                        });
                        row.appendChild(input);

                        // Remove button
                        var removeBtn = document.createElement("button");
                        removeBtn.type = "button";
                        removeBtn.className =
                            "shrink-0 p-1.5 text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400 transition-colors";
                        removeBtn.innerHTML =
                            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
                            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
                        removeBtn.addEventListener("click", function () {
                            self._removeChoiceByIndex(index);
                        });
                        row.appendChild(removeBtn);

                        container.appendChild(row);
                    })(i);
                }
            },

            _setCorrectByIndex: function (idx) {
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    for (var i = 0; i < arr.length; i++) {
                        arr[i].isCorrect = i === idx;
                    }
                }
                this._renderAllChoices();
            },

            _updateChoiceTextByIndex: function (lang, idx, value) {
                var propName = "choices" + lang.charAt(0).toUpperCase() + lang.slice(1);
                var choices = this[propName];
                if (choices[idx]) {
                    choices[idx].text = value;
                }
                // No re-render: only the data model changes, input keeps focus
            },

            _removeChoiceByIndex: function (idx) {
                this.choicesEn.splice(idx, 1);
                this.choicesDe.splice(idx, 1);
                this.choicesFr.splice(idx, 1);
                this._renderAllChoices();
            },

            get choicesJsonEn() {
                return JSON.stringify(this.choicesEn);
            },
            get choicesJsonDe() {
                return JSON.stringify(this.choicesDe);
            },
            get choicesJsonFr() {
                return JSON.stringify(this.choicesFr);
            },

            get trueFalseTrueClass() {
                return this.correctTrueFalse === "true"
                    ? "bg-green-600 text-white border-green-600"
                    : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-300 dark:border-gray-600";
            },

            get trueFalseFalseClass() {
                return this.correctTrueFalse === "false"
                    ? "bg-red-600 text-white border-red-600"
                    : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-300 dark:border-gray-600";
            },

            get canSubmit() {
                if (this.questionType === "open_ended") return true;
                if (this.questionType === "true_false") return true;
                // At least one language must have valid choices
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    if (arr.length >= 2) {
                        var hasCorrect = false;
                        var allFilled = true;
                        for (var i = 0; i < arr.length; i++) {
                            if (arr[i].isCorrect) hasCorrect = true;
                            if (!arr[i].text || !arr[i].text.trim()) allFilled = false;
                        }
                        if (hasCorrect && allFilled) return true;
                    }
                }
                return false;
            },

            get submitClass() {
                return this.canSubmit
                    ? "bg-crush-purple hover:bg-purple-700 text-white"
                    : "bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed";
            },
        };
    });
});
