// FinOps Hub Chart.js utilities - Power-Up Platform

/**
 * Format value as currency
 * @param {number} value - The value to format
 * @param {string} currency - Currency code (default: EUR)
 * @returns {string} Formatted currency string
 */
function formatCurrency(value, currency = 'EUR') {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2
    }).format(value);
}

/**
 * Format date string for display
 * @param {string} dateString - ISO date string
 * @returns {string} Formatted date (e.g., "Jan 15")
 */
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// Color palette for charts (Tailwind-inspired)
const CHART_COLORS = {
    primary: 'rgba(37, 99, 235, 0.8)',    // blue-600
    secondary: 'rgba(59, 130, 246, 0.8)', // blue-500
    success: 'rgba(22, 163, 74, 0.8)',    // green-600
    danger: 'rgba(220, 38, 38, 0.8)',     // red-600
    warning: 'rgba(234, 179, 8, 0.8)',    // yellow-500
    info: 'rgba(6, 182, 212, 0.8)',       // cyan-500
    teal: 'rgba(20, 184, 166, 0.8)',      // teal-500
    purple: 'rgba(147, 51, 234, 0.8)',    // purple-600
};

// Extended palette for multi-series charts
const CHART_PALETTE = [
    'rgba(37, 99, 235, 0.8)',   // blue-600
    'rgba(22, 163, 74, 0.8)',   // green-600
    'rgba(234, 179, 8, 0.8)',   // yellow-500
    'rgba(220, 38, 38, 0.8)',   // red-600
    'rgba(147, 51, 234, 0.8)', // purple-600
    'rgba(6, 182, 212, 0.8)',   // cyan-500
    'rgba(249, 115, 22, 0.8)', // orange-500
    'rgba(236, 72, 153, 0.8)', // pink-500
    'rgba(20, 184, 166, 0.8)', // teal-500
    'rgba(99, 102, 241, 0.8)', // indigo-500
];

// Common Chart.js options
const COMMON_OPTIONS = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: {
        legend: {
            display: true,
            position: 'top',
            labels: {
                usePointStyle: true,
                padding: 15,
                font: {
                    family: "'Inter', 'Segoe UI', sans-serif",
                    size: 12
                }
            }
        },
        tooltip: {
            backgroundColor: 'rgba(17, 24, 39, 0.9)', // gray-900
            titleFont: {
                family: "'Inter', 'Segoe UI', sans-serif",
                size: 13,
                weight: 'bold'
            },
            bodyFont: {
                family: "'Inter', 'Segoe UI', sans-serif",
                size: 12
            },
            padding: 12,
            cornerRadius: 6,
            callbacks: {
                label: function(context) {
                    let label = context.dataset.label || '';
                    if (label) {
                        label += ': ';
                    }
                    if (context.parsed.y !== null) {
                        label += formatCurrency(context.parsed.y);
                    }
                    return label;
                }
            }
        }
    },
    scales: {
        y: {
            beginAtZero: true,
            ticks: {
                callback: function(value) {
                    return formatCurrency(value);
                },
                font: {
                    family: "'Inter', 'Segoe UI', sans-serif",
                    size: 11
                }
            },
            grid: {
                color: 'rgba(0, 0, 0, 0.05)'
            }
        },
        x: {
            ticks: {
                font: {
                    family: "'Inter', 'Segoe UI', sans-serif",
                    size: 11
                }
            },
            grid: {
                display: false
            }
        }
    }
};

/**
 * Create a line chart for cost trends
 * @param {string} canvasId - Canvas element ID
 * @param {Array} labels - X-axis labels
 * @param {Array} data - Data points
 * @param {string} label - Dataset label
 * @returns {Chart} Chart.js instance
 */
function createCostTrendChart(canvasId, labels, data, label = 'Cost') {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: label,
                data: data,
                borderColor: CHART_COLORS.primary,
                backgroundColor: 'rgba(37, 99, 235, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: COMMON_OPTIONS
    });
}

/**
 * Create a horizontal bar chart for service breakdown
 * @param {string} canvasId - Canvas element ID
 * @param {Array} services - Array of {service_name, total_cost}
 * @returns {Chart} Chart.js instance
 */
function createServiceBarChart(canvasId, services) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !services || services.length === 0) return null;

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: services.map(s => s.service_name),
            datasets: [{
                label: 'Cost (EUR)',
                data: services.map(s => parseFloat(s.total_cost)),
                backgroundColor: CHART_COLORS.teal
            }]
        },
        options: {
            ...COMMON_OPTIONS,
            indexAxis: 'y',
            scales: {
                x: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return formatCurrency(value);
                        }
                    }
                }
            }
        }
    });
}

/**
 * Create a doughnut chart for cost distribution
 * @param {string} canvasId - Canvas element ID
 * @param {Array} labels - Segment labels
 * @param {Array} data - Segment values
 * @returns {Chart} Chart.js instance
 */
function createCostDistributionChart(canvasId, labels, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: CHART_PALETTE.slice(0, data.length),
                borderWidth: 2,
                borderColor: '#ffffff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        usePointStyle: true,
                        padding: 15
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = ((context.parsed / total) * 100).toFixed(1);
                            return `${context.label}: ${formatCurrency(context.parsed)} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });
}
