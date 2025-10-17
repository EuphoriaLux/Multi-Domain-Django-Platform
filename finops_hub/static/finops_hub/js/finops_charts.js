// FinOps Hub Chart.js utilities

function formatCurrency(value, currency = 'EUR') {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2
    }).format(value);
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// Color palette for charts
const CHART_COLORS = {
    primary: 'rgba(75, 192, 192, 0.8)',
    secondary: 'rgba(54, 162, 235, 0.8)',
    success: 'rgba(75, 192, 75, 0.8)',
    danger: 'rgba(255, 99, 132, 0.8)',
    warning: 'rgba(255, 206, 86, 0.8)',
    info: 'rgba(153, 102, 255, 0.8)',
};

// Common Chart.js options
const COMMON_OPTIONS = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: {
        legend: {
            display: true,
            position: 'top'
        },
        tooltip: {
            callbacks: {
                label: function(context) {
                    let label = context.dataset.label || '';
                    if (label) {
                        label += ': ';
                    }
                    label += formatCurrency(context.parsed.y);
                    return label;
                }
            }
        }
    }
};
