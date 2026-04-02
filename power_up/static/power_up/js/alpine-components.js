// Power-UP Alpine.js CSP-compliant components
// Uses @alpinejs/csp build - no eval() or new Function()

document.addEventListener('alpine:init', function () {
    // Filter toggle for FinOps dashboard
    Alpine.data('finopsFilterToggle', function () {
        return {
            showFilters: this.$el.dataset.initialOpen === 'true',

            get isClosed() {
                return !this.showFilters;
            },

            get showLabel() {
                return this.showFilters ? 'Hide filters' : 'Show filters';
            },

            toggle() {
                this.showFilters = !this.showFilters;
            },
        };
    });
});
