/**
 * Theme Manager - Crush.lu Dark Mode
 *
 * Initializes theme BEFORE page renders to prevent flash of wrong theme.
 * Supports automatic system preference detection and manual override.
 *
 * Priority: localStorage > system preference > default (light)
 */
(function () {
    "use strict";

    /**
     * Get initial theme preference
     * @returns {string} 'dark' or 'light'
     */
    function getInitialTheme() {
        // Check localStorage first
        const saved = localStorage.getItem("theme");
        if (saved === "dark" || saved === "light") {
            return saved;
        }

        // Fall back to system preference
        if (
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
        ) {
            return "dark";
        }

        // Default to light mode
        return "light";
    }

    // Status-bar colours of the two <meta name="theme-color"> tags in
    // base.html (light = brand purple, dark = the slate top bar).
    var THEME_COLORS = { light: "#9B59B6", dark: "#0f172a" };

    /**
     * Point the browser/status bar colour at the page theme. With no manual
     * choice the two media-scoped metas follow the OS; a manual pick or a
     * theme-locked page sets both, since the OS scheme no longer decides.
     */
    function syncThemeColor(theme, overridden) {
        var metas = document.querySelectorAll('meta[name="theme-color"]');
        for (var i = 0; i < metas.length; i++) {
            var media = metas[i].getAttribute("media") || "";
            var own = media.indexOf("dark") !== -1 ? "dark" : "light";
            metas[i].setAttribute("content", THEME_COLORS[overridden ? theme : own]);
        }
    }

    function systemTheme() {
        return window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark"
            : "light";
    }

    /**
     * Apply theme by toggling 'dark' class on <html> element
     * @param {string} theme - 'dark' or 'light'
     * @param {boolean} persist - save it as the manual choice (false on page
     *   load, so an unset preference keeps following the OS — "System")
     */
    function applyTheme(theme, persist) {
        // An always-dark surface (journey / gift pages render
        // data-theme-lock="dark" on <html>) keeps .dark whatever the saved or
        // system preference, and never overwrites the saved preference.
        const locked = document.documentElement.getAttribute("data-theme-lock");
        if (locked) {
            theme = locked;
        }
        if (theme === "dark") {
            document.documentElement.classList.add("dark");
            document.documentElement.style.colorScheme = "dark";
        } else {
            document.documentElement.classList.remove("dark");
            document.documentElement.style.colorScheme = "light";
        }
        if (!locked && persist !== false) {
            localStorage.setItem("theme", theme);
        }
        syncThemeColor(theme, !!locked || !!localStorage.getItem("theme"));
        // Keeps every themeToggle instance (navbar, drawer) in step.
        window.dispatchEvent(new CustomEvent("crush:themechange"));
    }

    /**
     * Drop the manual choice and follow the OS again ("System").
     */
    function useSystemTheme() {
        if (document.documentElement.hasAttribute("data-theme-lock")) {
            return;
        }
        localStorage.removeItem("theme");
        applyTheme(systemTheme(), false);
    }

    /**
     * Toggle between light and dark themes
     */
    function toggleTheme() {
        const current = getInitialTheme();
        applyTheme(current === "dark" ? "light" : "dark");
    }

    // Initialize theme immediately (blocking execution)
    const initialTheme = getInitialTheme();
    applyTheme(initialTheme, false);

    // Listen for system preference changes
    if (window.matchMedia) {
        const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

        // Modern browsers
        if (mediaQuery.addEventListener) {
            mediaQuery.addEventListener("change", (e) => {
                // Only auto-switch if user hasn't manually set preference
                const saved = localStorage.getItem("theme");
                if (!saved) {
                    applyTheme(e.matches ? "dark" : "light", false);
                }
            });
        }
        // Legacy browsers
        else if (mediaQuery.addListener) {
            mediaQuery.addListener((e) => {
                const saved = localStorage.getItem("theme");
                if (!saved) {
                    applyTheme(e.matches ? "dark" : "light", false);
                }
            });
        }
    }

    // Expose API for Alpine.js component
    window.themeManager = {
        getTheme: () =>
            document.documentElement.getAttribute("data-theme-lock") ||
            localStorage.getItem("theme") ||
            getInitialTheme(),
        isLocked: () => document.documentElement.hasAttribute("data-theme-lock"),
        // "light" | "dark" | "system" (no manual choice saved)
        getPreference: () => localStorage.getItem("theme") || "system",
        setTheme: applyTheme,
        useSystemTheme: useSystemTheme,
        toggleTheme: toggleTheme,
    };
})();
