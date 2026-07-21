'use strict';
/*
 * Crush.lu Coach Panel sidebar behaviour.
 *
 * The sidebar (templates/admin/nav_sidebar.html) folds the "More" and
 * "Developer & Analytics" tiers into native <details> sections. Two jobs here,
 * both CSP-safe (external file, no inline handlers):
 *   1. Remember each tier's open/closed state across page loads.
 *   2. While Django's #nav-filter quick-search has text, force every tier open
 *      so matches inside a collapsed section are actually visible; restore the
 *      previous state when the search is cleared.
 */
(function () {
    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
        } else {
            document.addEventListener('DOMContentLoaded', fn);
        }
    }

    ready(function () {
        var nav = document.getElementById('nav-sidebar');
        if (!nav) {
            return;
        }

        var tiers = Array.prototype.slice.call(nav.querySelectorAll('details.nav-tier'));
        if (!tiers.length) {
            return;
        }

        var filtering = false;

        // 1. Persist open/closed state per tier.
        tiers.forEach(function (d) {
            var key = 'crush.admin.navTier.' + (d.dataset.tier || 'tier');
            if (localStorage.getItem(key) === 'open') {
                d.open = true;
            }
            d.addEventListener('toggle', function () {
                // Ignore programmatic opens driven by the filter below.
                if (filtering) {
                    return;
                }
                localStorage.setItem(key, d.open ? 'open' : 'closed');
            });
        });

        // 2. Auto-open every tier while the quick-filter is active.
        var filter = document.getElementById('nav-filter');
        if (filter) {
            var apply = function () {
                var active = filter.value.trim().length > 0;
                if (active === filtering) {
                    return;
                }
                filtering = active;
                tiers.forEach(function (d) {
                    if (active) {
                        d.dataset.prevOpen = d.open ? '1' : '0';
                        d.open = true;
                    } else if (d.dataset.prevOpen !== undefined) {
                        d.open = d.dataset.prevOpen === '1';
                        delete d.dataset.prevOpen;
                    }
                });
            };
            filter.addEventListener('input', apply);
            filter.addEventListener('keyup', apply);
            // Django's nav_sidebar.js restores a stored filter value on load;
            // reflect that here so tiers open if a filter is already applied.
            apply();
        }
    });
})();
