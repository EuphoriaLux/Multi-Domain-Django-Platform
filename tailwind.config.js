/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './crush_lu/templates/**/*.html',
    './crush_lu/**/*.py',
    './static/crush_lu/js/**/*.js',
  ],
  // No prefix - using native Tailwind classes
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        crush: {
          purple: '#9B59B6',
          'purple-light': '#AF7AC5',
          'purple-dark': '#8E44AD',
          pink: '#FF6B9D',
          'pink-light': '#FF8FB3',
          'pink-dark': '#D94D7B',
          dark: '#2C3E50',
          light: '#F8F9FA',
          gray: '#6c757d',
          success: '#28a745',
          warning: '#f39c12',
          danger: '#dc3545',
          info: '#17a2b8',
        },
      },
      spacing: {
        '0': '0',
        '1': '0.25rem',
        '2': '0.5rem',
        '3': '0.75rem',
        '4': '1rem',
        '5': '1.25rem',
        '6': '1.5rem',
        '8': '2rem',
        '10': '2.5rem',
        '12': '3rem',
        '16': '4rem',
        '20': '5rem',
      },
      borderRadius: {
        'crush-sm': '8px',
        'crush-md': '12px',
        'crush-lg': '15px',
        'crush-xl': '20px',
        'crush-2xl': '25px',
        'crush-pill': '50px',
      },
      boxShadow: {
        'crush-sm': '0 2px 10px rgba(0, 0, 0, 0.08)',
        'crush-md': '0 4px 20px rgba(0, 0, 0, 0.1)',
        'crush-lg': '0 10px 30px rgba(0, 0, 0, 0.15)',
        'crush-purple': '0 5px 15px rgba(155, 89, 182, 0.4)',
        'crush-pink': '0 5px 15px rgba(255, 107, 157, 0.4)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
}
