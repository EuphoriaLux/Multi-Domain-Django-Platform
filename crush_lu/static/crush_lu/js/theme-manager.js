/**
 * Theme Manager - Crush.lu Dark Mode
 *
 * Initializes theme BEFORE page renders to prevent flash of wrong theme.
 * Supports automatic system preference detection and manual override.
 *
 * Priority: localStorage > system preference > default (light)
 */
(function() {
    'use strict';

    /**
     * Get initial theme preference
     * @returns {string} 'dark' or 'light'
     */
    function getInitialTheme() {
        // Check localStorage first
        const saved = localStorage.getItem('theme');
        if (saved === 'dark' || saved === 'light') {
            return saved;
        }

        // Fall back to system preference
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }

        // Default to light mode
        return 'light';
    }

    /**
     * Apply theme by toggling 'dark' class on <html> element
     * @param {string} theme - 'dark' or 'light'
     */
    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
            document.documentElement.style.colorScheme = 'dark';
        } else {
            document.documentElement.classList.remove('dark');
            document.documentElement.style.colorScheme = 'light';
        }
        localStorage.setItem('theme', theme);
    }

    /**
     * Toggle between light and dark themes
     */
    function toggleTheme() {
        const current = getInitialTheme();
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    // Initialize theme immediately (blocking execution)
    const initialTheme = getInitialTheme();
    applyTheme(initialTheme);

    // Listen for system preference changes
    if (window.matchMedia) {
        const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

        // Modern browsers
        if (mediaQuery.addEventListener) {
            mediaQuery.addEventListener('change', (e) => {
                // Only auto-switch if user hasn't manually set preference
                const saved = localStorage.getItem('theme');
                if (!saved) {
                    applyTheme(e.matches ? 'dark' : 'light');
                }
            });
        }
        // Legacy browsers
        else if (mediaQuery.addListener) {
            mediaQuery.addListener((e) => {
                const saved = localStorage.getItem('theme');
                if (!saved) {
                    applyTheme(e.matches ? 'dark' : 'light');
                }
            });
        }
    }

    // Expose API for Alpine.js component
    window.themeManager = {
        getTheme: () => localStorage.getItem('theme') || getInitialTheme(),
        setTheme: applyTheme,
        toggleTheme: toggleTheme
    };
})();
