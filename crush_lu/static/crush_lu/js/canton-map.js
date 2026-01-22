/**
 * Canton Map Alpine.js Component for Crush.lu
 *
 * Interactive SVG map component for selecting Luxembourg cantons and border regions.
 * CSP-compatible using Alpine.data() pattern with event delegation.
 *
 * Usage:
 * <div x-data="cantonMap"
 *      data-initial-value="{{ profile.location }}"
 *      data-initial-name="{% if profile.location %}{{ profile.get_location_display }}{% endif %}"
 *      class="canton-map-container">
 *     {% include "crush_lu/partials/canton_map_svg.html" %}
 *     <input type="hidden" name="location" id="id_location" x-bind:value="selectedRegion">
 * </div>
 */

document.addEventListener('alpine:init', function() {

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

            /**
             * Initialize the component
             */
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

            /**
             * Build array of all selectable regions for keyboard navigation
             */
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

            /**
             * Determine which border region a point belongs to based on geography
             * Returns the border region data or null if inside Luxembourg
             *
             * Simplified border layout (matching actual geography):
             * - Belgium: West side only (left strip, x < 350)
             * - Germany: East side only (right strip, x > 350)
             * - France: South side only (narrow bottom strip, y > 850)
             *
             * Priority: France > Belgium > Germany (overlapping corners go to France first)
             */
            _getBorderRegionAtPoint: function(svgX, svgY) {
                // Border region definitions matching actual geography
                // Check in order of priority

                // France - South side (narrow bottom strip, only touching south of Luxembourg)
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

            /**
             * Convert screen coordinates to SVG coordinates
             */
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

            /**
             * Set up event delegation for SVG interactions
             * Since CSP prevents inline handlers, we delegate from the container
             */
            _setupEventDelegation: function() {
                var self = this;
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                // Click handler - handles both cantons and border regions
                svg.addEventListener('click', function(e) {
                    var path = e.target.closest('[data-region-id]');

                    // If clicked on a canton, select it
                    if (path && path.classList.contains('lux-canton')) {
                        var regionId = path.getAttribute('data-region-id');
                        var regionName = path.getAttribute('data-region-name');
                        self.selectRegion(regionId, regionName);
                        return;
                    }

                    // If clicked on a border region directly (exposed area)
                    if (path && path.classList.contains('border-region')) {
                        var regionId = path.getAttribute('data-region-id');
                        var regionName = path.getAttribute('data-region-name');
                        self.selectRegion(regionId, regionName);
                        return;
                    }

                    // If clicked on background, determine which border region based on coordinates
                    if (e.target.classList.contains('map-background') || e.target.tagName === 'svg') {
                        var svgPoint = self._screenToSVGCoords(svg, e.clientX, e.clientY);
                        var borderRegion = self._getBorderRegionAtPoint(svgPoint.x, svgPoint.y);
                        if (borderRegion) {
                            self.selectRegion(borderRegion.id, borderRegion.name);
                        }
                    }
                });

                // Mouseover handler for hover effects
                svg.addEventListener('mouseover', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        var regionId = path.getAttribute('data-region-id');
                        var regionName = path.getAttribute('data-region-name');
                        self.hoverRegion(regionId, regionName);
                    }
                });

                // Mouseout handler to clear hover
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
                        var regionId = path.getAttribute('data-region-id');
                        var regionName = path.getAttribute('data-region-name');
                        self.hoverRegion(regionId, regionName);
                    }
                }, { passive: true });

                svg.addEventListener('touchend', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        var regionId = path.getAttribute('data-region-id');
                        var regionName = path.getAttribute('data-region-name');
                        self.selectRegion(regionId, regionName);
                        self.clearHover();
                    }
                });
            },

            /**
             * Set up keyboard navigation for accessibility
             */
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

                // Focus handler
                svg.addEventListener('focus', function() {
                    if (self.focusedIndex < 0 && self.regions.length > 0) {
                        // If nothing focused, focus the selected region or first
                        var selectedIndex = self._getSelectedIndex();
                        self.focusedIndex = selectedIndex >= 0 ? selectedIndex : 0;
                        self._applyFocus();
                    }
                });

                svg.addEventListener('blur', function() {
                    self._clearFocus();
                });
            },

            /**
             * Navigate to next region
             */
            _navigateNext: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex + 1) % this.regions.length;
                this._applyFocus();
            },

            /**
             * Navigate to previous region
             */
            _navigatePrevious: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex - 1 + this.regions.length) % this.regions.length;
                this._applyFocus();
            },

            /**
             * Focus first region
             */
            _focusFirst: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = 0;
                this._applyFocus();
            },

            /**
             * Focus last region
             */
            _focusLast: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = this.regions.length - 1;
                this._applyFocus();
            },

            /**
             * Select the currently focused region
             */
            _selectFocused: function() {
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    this.selectRegion(region.id, region.name);
                }
            },

            /**
             * Get index of currently selected region
             */
            _getSelectedIndex: function() {
                for (var i = 0; i < this.regions.length; i++) {
                    if (this.regions[i].id === this.selectedRegion) {
                        return i;
                    }
                }
                return -1;
            },

            /**
             * Apply focus styling to current focused region
             */
            _applyFocus: function() {
                // Clear previous focus
                this._clearFocus();

                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    region.element.classList.add('region-focused');
                    // Also show hover info
                    this.hoverRegion(region.id, region.name);
                }
            },

            /**
             * Clear focus styling from all regions
             */
            _clearFocus: function() {
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                var focusedElements = svg.querySelectorAll('.region-focused');
                for (var i = 0; i < focusedElements.length; i++) {
                    focusedElements[i].classList.remove('region-focused');
                }
            },

            /**
             * Select a region
             */
            selectRegion: function(regionId, regionName) {
                // Deselect previous region
                if (this.selectedRegion) {
                    this._unhighlightRegion(this.selectedRegion);
                }

                // Update state
                this.selectedRegion = regionId;
                this.selectedRegionName = regionName;

                // Highlight new region
                this._highlightRegion(regionId);

                // Update hidden form field
                var hiddenInput = document.getElementById('id_location');
                if (hiddenInput) {
                    hiddenInput.value = regionId;
                    // Trigger change event for form validation
                    hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
                }

                // Dispatch custom event for parent components
                this.$dispatch('region-selected', {
                    id: regionId,
                    name: regionName
                });
            },

            /**
             * Set hover state for a region
             */
            hoverRegion: function(regionId, regionName) {
                this.hoveredRegion = regionId;
                this.hoveredRegionName = regionName;

                // Add hover class to path
                var path = document.getElementById(regionId);
                if (path && !path.classList.contains('region-selected')) {
                    path.classList.add('region-hover');
                }
            },

            /**
             * Clear hover state
             */
            clearHover: function() {
                // Remove hover class from previous hovered element
                if (this.hoveredRegion) {
                    var prevPath = document.getElementById(this.hoveredRegion);
                    if (prevPath) {
                        prevPath.classList.remove('region-hover');
                    }
                }

                this.hoveredRegion = '';
                this.hoveredRegionName = '';
            },

            /**
             * Toggle fallback dropdown visibility
             */
            toggleFallbackDropdown: function() {
                this.showFallbackDropdown = !this.showFallbackDropdown;
            },

            /**
             * Handle selection from fallback dropdown
             */
            handleFallbackSelect: function(event) {
                var select = event.target;
                var option = select.options[select.selectedIndex];
                if (option && option.value) {
                    this.selectRegion(option.value, option.text);
                }
            },

            /**
             * Highlight a region as selected
             */
            _highlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.add('region-selected');
                    path.classList.remove('region-hover');
                    path.setAttribute('aria-selected', 'true');
                }
            },

            /**
             * Remove highlight from a region
             */
            _unhighlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.remove('region-selected');
                    path.setAttribute('aria-selected', 'false');
                }
            }
        };
    });

});
