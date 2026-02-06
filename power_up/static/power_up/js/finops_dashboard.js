// FinOps Dashboard - Consolidated JavaScript
// Replaces all inline <script> blocks across finops templates

(function () {
    'use strict';

    // ========================================================================
    // Dashboard page
    // ========================================================================

    function initDashboard(config) {
        if (!config) return;

        var costChart = null;
        var rawCostData = [];
        var currentView = 'daily';

        fetchCostTrend();
        renderSubscriptionChart(config.topSubscriptions);
        initDiscountSlider(config.totalCost);

        function fetchCostTrend() {
            fetch('/finops/api/costs/trend/?days=' + config.periodDays)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    rawCostData = data.daily_costs;
                    renderCostTrendChart(rawCostData, 'daily');
                });
        }

        // Expose view toggle globally for onclick buttons
        window.toggleChartView = function (view) {
            currentView = view;
            var dailyBtn = document.getElementById('dailyView');
            var weeklyBtn = document.getElementById('weeklyView');
            var activeClass = 'px-3 py-1 text-sm font-medium rounded-md bg-blue-600 text-white';
            var inactiveClass = 'px-3 py-1 text-sm font-medium rounded-md bg-white text-gray-700 border border-gray-300 hover:bg-gray-50';
            if (dailyBtn) dailyBtn.className = view === 'daily' ? activeClass : inactiveClass;
            if (weeklyBtn) weeklyBtn.className = view === 'weekly' ? activeClass : inactiveClass;
            renderCostTrendChart(rawCostData, view);
        };

        function aggregateWeekly(dailyCosts) {
            var weeklyData = {};
            dailyCosts.forEach(function (day) {
                var date = new Date(day.usage_date);
                var monday = new Date(date);
                monday.setDate(date.getDate() - date.getDay() + 1);
                var weekKey = monday.toISOString().split('T')[0];
                if (!weeklyData[weekKey]) {
                    weeklyData[weekKey] = { usage_date: weekKey, total_cost: 0, usage_cost: 0, purchase_cost: 0 };
                }
                weeklyData[weekKey].total_cost += parseFloat(day.total_cost || 0);
                weeklyData[weekKey].usage_cost += parseFloat(day.usage_cost || 0);
                weeklyData[weekKey].purchase_cost += parseFloat(day.purchase_cost || 0);
            });
            return Object.values(weeklyData).sort(function (a, b) { return new Date(a.usage_date) - new Date(b.usage_date); });
        }

        function formatDateLabel(dateStr, view) {
            var date = new Date(dateStr);
            if (view === 'weekly') {
                var endDate = new Date(date);
                endDate.setDate(date.getDate() + 6);
                var opts = { month: 'short', day: 'numeric' };
                return date.toLocaleDateString('en-US', opts) + ' - ' + endDate.toLocaleDateString('en-US', opts);
            }
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        }

        function renderCostTrendChart(dailyCosts, view) {
            var data = view === 'weekly' ? aggregateWeekly(dailyCosts) : dailyCosts;
            var canvas = document.getElementById('costTrendChart');
            if (!canvas) return;
            var ctx = canvas.getContext('2d');
            var labels = data.map(function (d) { return formatDateLabel(d.usage_date, view); });
            var usageCosts = data.map(function (d) { return parseFloat(d.usage_cost || 0); });
            var purchaseCosts = data.map(function (d) { return parseFloat(d.purchase_cost || 0); });

            if (costChart) costChart.destroy();

            costChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        { label: 'Usage Cost', data: usageCosts, backgroundColor: 'rgba(59, 130, 246, 0.8)', borderWidth: 0 },
                        { label: 'Purchase Cost', data: purchaseCosts, backgroundColor: 'rgba(251, 146, 60, 0.8)', borderWidth: 0 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: { display: true, position: 'top' },
                        tooltip: {
                            mode: 'index',
                            intersect: false,
                            callbacks: {
                                label: function (ctx) { return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2); },
                                footer: function (items) { return 'Total: ' + items.reduce(function (a, b) { return a + b.parsed.y; }, 0).toFixed(2); }
                            }
                        }
                    },
                    scales: {
                        x: { stacked: true, ticks: { maxRotation: 45, minRotation: 45 } },
                        y: { stacked: true, beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } }
                    }
                }
            });
        }

        function renderSubscriptionChart(subscriptions) {
            var canvas = document.getElementById('subscriptionChart');
            if (!canvas || !subscriptions || subscriptions.length === 0) return;
            var ctx = canvas.getContext('2d');
            var labels = subscriptions.map(function (s) { return s.sub_account_name || 'Unknown'; });
            var costs = subscriptions.map(function (s) { return parseFloat(s.cost); });
            var colors = ['rgba(239,68,68,0.9)', 'rgba(59,130,246,0.9)', 'rgba(234,179,8,0.9)', 'rgba(34,197,94,0.9)', 'rgba(168,85,247,0.9)'];

            new Chart(ctx, {
                type: 'doughnut',
                data: { labels: labels, datasets: [{ data: costs, backgroundColor: colors.slice(0, costs.length), borderWidth: 0 }] },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    cutout: '60%',
                    plugins: {
                        legend: { position: 'bottom', labels: { padding: 15, font: { size: 11 }, usePointStyle: true } },
                        tooltip: {
                            callbacks: {
                                label: function (ctx) {
                                    var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                                    var pct = ((ctx.parsed / total) * 100).toFixed(1);
                                    return ctx.label + ': ' + ctx.parsed.toFixed(2) + ' (' + pct + '%)';
                                }
                            }
                        }
                    }
                }
            });
        }

        function initDiscountSlider(originalTotal) {
            var slider = document.getElementById('discountSlider');
            var input = document.getElementById('discountInput');
            var savingsEl = document.getElementById('discountSavings');
            if (!slider || !input || !savingsEl) return;

            slider.addEventListener('input', function () {
                input.value = this.value;
                applyDiscount(parseFloat(this.value));
            });

            input.addEventListener('input', function () {
                var v = Math.max(0, Math.min(15, parseFloat(this.value) || 0));
                this.value = v;
                slider.value = v;
                applyDiscount(v);
            });

            function applyDiscount(pct) {
                var savings = 0, newTotal = 0;
                document.querySelectorAll('.service-row').forEach(function (row) {
                    var name = row.dataset.serviceName;
                    var orig = parseFloat(row.dataset.originalCost.replace(',', '.'));
                    var isMicrosoft = name.toLowerCase().indexOf('azure') >= 0 || name.toLowerCase().indexOf('microsoft') >= 0;
                    var discounted = orig;
                    if (isMicrosoft && pct > 0) {
                        var disc = orig * (pct / 100);
                        discounted = orig - disc;
                        savings += disc;
                    }
                    newTotal += discounted;
                    var costCell = row.querySelector('.service-cost');
                    if (costCell) {
                        costCell.innerHTML = isMicrosoft && pct > 0
                            ? '<span class="text-green-600">' + discounted.toFixed(2) + '</span> <small class="text-gray-400">(-' + pct + '%)</small>'
                            : discounted.toFixed(2);
                    }
                });
                savingsEl.innerHTML = '<span class="text-sm text-gray-500">Savings: </span><strong class="text-green-600">' + savings.toFixed(2) + '</strong><br><small class="text-gray-500">New Total: ' + newTotal.toFixed(2) + '</small>';
            }
        }
    }

    // ========================================================================
    // Subscription view page
    // ========================================================================

    function initSubscriptionView(config) {
        if (!config || !config.subscriptions || config.subscriptions.length === 0) return;

        var labels = config.subscriptions.map(function (s) { return s.sub_account_name || 'Unknown'; });
        var costs = config.subscriptions.map(function (s) { return parseFloat(s.total_cost); });
        var colors = ['rgba(239,68,68,0.9)', 'rgba(59,130,246,0.9)', 'rgba(234,179,8,0.9)', 'rgba(34,197,94,0.9)', 'rgba(168,85,247,0.9)', 'rgba(251,146,60,0.9)'];

        var barCanvas = document.getElementById('subscriptionBarChart');
        if (barCanvas) {
            new Chart(barCanvas, {
                type: 'bar',
                data: { labels: labels, datasets: [{ label: 'Cost (EUR)', data: costs, backgroundColor: 'rgba(59,130,246,0.8)' }] },
                options: { responsive: true, scales: { y: { beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } } } }
            });
        }

        var pieCanvas = document.getElementById('subscriptionPieChart');
        if (pieCanvas) {
            new Chart(pieCanvas, {
                type: 'doughnut',
                data: { labels: labels, datasets: [{ data: costs, backgroundColor: colors.slice(0, costs.length), borderWidth: 0 }] },
                options: { responsive: true, cutout: '60%', plugins: { legend: { position: 'bottom' } } }
            });
        }
    }

    // ========================================================================
    // Service breakdown page
    // ========================================================================

    function initServiceBreakdown(config) {
        if (!config || !config.services || config.services.length === 0) return;

        var canvas = document.getElementById('serviceChart');
        if (!canvas) return;

        new Chart(canvas, {
            type: 'bar',
            data: {
                labels: config.services.map(function (s) { return s.service_name; }),
                datasets: [{ label: 'Cost (EUR)', data: config.services.map(function (s) { return parseFloat(s.total_cost); }), backgroundColor: 'rgba(20,184,166,0.8)' }]
            },
            options: { indexAxis: 'y', responsive: true, scales: { x: { beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } } } }
        });
    }

    // ========================================================================
    // Anomalies page
    // ========================================================================

    function initAnomalies() {
        // Expose acknowledge function globally
        window.acknowledgeAnomaly = function (id) {
            if (!confirm('Mark this anomaly as acknowledged?')) return;

            fetch('/finops/api/anomalies/' + id + '/acknowledge/', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken'),
                    'Content-Type': 'application/json'
                }
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    location.reload();
                } else {
                    alert('Failed to acknowledge anomaly');
                }
            })
            .catch(function (error) {
                console.error('Error:', error);
                alert('An error occurred');
            });
        };
    }

    function getCookie(name) {
        var cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            var cookies = document.cookie.split(';');
            for (var i = 0; i < cookies.length; i++) {
                var cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // ========================================================================
    // Forecast page
    // ========================================================================

    function initForecast(config) {
        if (!config || !config.hasData) return;

        var canvas = document.getElementById('forecastChart');
        if (!canvas) return;

        var historicalLabels = config.historical.map(function (h) { return h.date; });
        var historicalCosts = config.historical.map(function (h) { return h.cost; });

        var forecastLabels = config.forecasts.map(function (f) { return f.date; });
        var forecastCosts = config.forecasts.map(function (f) { return f.cost; });
        var forecastUpper = config.forecasts.map(function (f) { return f.upper; });
        var forecastLower = config.forecasts.map(function (f) { return f.lower; });

        var allLabels = historicalLabels.concat(forecastLabels);

        // Pad historical data with nulls for forecast range and vice versa
        var historicalDataset = historicalCosts.concat(forecastCosts.map(function () { return null; }));
        var forecastDataset = historicalCosts.map(function () { return null; }).concat(forecastCosts);
        var upperDataset = historicalCosts.map(function () { return null; }).concat(forecastUpper);
        var lowerDataset = historicalCosts.map(function () { return null; }).concat(forecastLower);

        // Bridge: connect last historical point to first forecast point
        if (historicalCosts.length > 0 && forecastCosts.length > 0) {
            forecastDataset[historicalCosts.length - 1] = historicalCosts[historicalCosts.length - 1];
            upperDataset[historicalCosts.length - 1] = historicalCosts[historicalCosts.length - 1];
            lowerDataset[historicalCosts.length - 1] = historicalCosts[historicalCosts.length - 1];
        }

        new Chart(canvas, {
            type: 'line',
            data: {
                labels: allLabels,
                datasets: [
                    {
                        label: 'Historical',
                        data: historicalDataset,
                        borderColor: 'rgba(59, 130, 246, 1)',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: false,
                        tension: 0.3,
                        pointRadius: 2
                    },
                    {
                        label: 'Forecast',
                        data: forecastDataset,
                        borderColor: 'rgba(139, 92, 246, 1)',
                        borderDash: [5, 5],
                        fill: false,
                        tension: 0.3,
                        pointRadius: 2
                    },
                    {
                        label: 'Upper Bound (95% CI)',
                        data: upperDataset,
                        borderColor: 'rgba(139, 92, 246, 0.3)',
                        backgroundColor: 'rgba(139, 92, 246, 0.08)',
                        fill: '+1',
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 1
                    },
                    {
                        label: 'Lower Bound (95% CI)',
                        data: lowerDataset,
                        borderColor: 'rgba(139, 92, 246, 0.3)',
                        backgroundColor: 'rgba(139, 92, 246, 0.08)',
                        fill: false,
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { position: 'top' },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            label: function (ctx) {
                                if (ctx.parsed.y === null) return null;
                                return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2);
                            }
                        }
                    }
                },
                scales: {
                    y: { beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } },
                    x: { ticks: { maxRotation: 45, minRotation: 45 } }
                }
            }
        });
    }

    // ========================================================================
    // Comparison page (Phase 3.1)
    // ========================================================================

    function initComparison(config) {
        if (!config) return;

        var canvas = document.getElementById('comparisonChart');
        if (!canvas || !config.services || config.services.length === 0) return;

        var labels = config.services.map(function (s) { return s.service_name; });
        var month1Data = config.services.map(function (s) { return s.month1_cost; });
        var month2Data = config.services.map(function (s) { return s.month2_cost; });

        new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: config.month1Label, data: month1Data, backgroundColor: 'rgba(59,130,246,0.8)' },
                    { label: config.month2Label, data: month2Data, backgroundColor: 'rgba(234,179,8,0.8)' }
                ]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                scales: { x: { beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } } },
                plugins: { legend: { position: 'top' } }
            }
        });
    }

    // ========================================================================
    // Resource groups page (Phase 3.2)
    // ========================================================================

    function initResourceGroups(config) {
        if (!config || !config.resourceGroups || config.resourceGroups.length === 0) return;

        var canvas = document.getElementById('resourceGroupChart');
        if (!canvas) return;

        var labels = config.resourceGroups.map(function (rg) { return rg.resource_group_name || 'Unknown'; });
        var costs = config.resourceGroups.map(function (rg) { return parseFloat(rg.total_cost); });

        new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{ label: 'Cost (EUR)', data: costs, backgroundColor: 'rgba(20,184,166,0.8)' }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                scales: { x: { beginAtZero: true, ticks: { callback: function (v) { return '' + v.toFixed(2); } } } }
            }
        });
    }

    // ========================================================================
    // Auto-initialization based on page config
    // ========================================================================

    document.addEventListener('DOMContentLoaded', function () {
        var configEl = document.getElementById('finops-config');
        if (!configEl) return;

        var config;
        try {
            config = JSON.parse(configEl.textContent);
        } catch (e) {
            console.error('Failed to parse finops config:', e);
            return;
        }

        switch (config.page) {
            case 'dashboard':
                initDashboard(config);
                break;
            case 'subscriptions':
                initSubscriptionView(config);
                break;
            case 'services':
                initServiceBreakdown(config);
                break;
            case 'anomalies':
                initAnomalies();
                break;
            case 'forecast':
                initForecast(config);
                break;
            case 'comparison':
                initComparison(config);
                break;
            case 'resource_groups':
                initResourceGroups(config);
                break;
        }
    });

})();
