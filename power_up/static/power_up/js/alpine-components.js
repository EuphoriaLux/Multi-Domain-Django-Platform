// Power-UP Alpine.js CSP-compliant components
// Uses @alpinejs/csp build - no eval() or new Function()

document.addEventListener("alpine:init", function () {
    // FAQ accordion for FinOps FAQ page
    Alpine.data("faqAccordion", function () {
        return {
            open: 1,

            toggle(section) {
                this.open = this.open === section ? null : section;
            },

            isOpen(section) {
                return this.open === section;
            },

            buttonClass(section) {
                return this.open === section
                    ? "bg-blue-600 text-white"
                    : "bg-gray-100 text-gray-800";
            },

            icon(section) {
                return this.open === section ? "\u2212" : "+";
            },
        };
    });

    // Filter toggle for FinOps dashboard
    Alpine.data("finopsFilterToggle", function () {
        return {
            showFilters: this.$el.dataset.initialOpen === "true",

            get isClosed() {
                return !this.showFilters;
            },

            get showLabel() {
                return this.showFilters ? "Hide filters" : "Show filters";
            },

            toggle() {
                this.showFilters = !this.showFilters;
            },
        };
    });
});
