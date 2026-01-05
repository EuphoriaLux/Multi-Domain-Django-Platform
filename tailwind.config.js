/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './crush_lu/templates/**/*.html',
    './crush_lu/**/*.py',
    './static/crush_lu/js/**/*.js',
    // Power-Up corporate site
    './power_up/templates/**/*.html',
    './power_up/**/*.py',
    // Tableau AI Art e-commerce site
    './tableau/templates/**/*.html',
    './tableau/**/*.py',
  ],
  // No prefix - using native Tailwind classes
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Override default purple palette with Crush.lu brand colors
        // This ensures purple-500, purple-600, etc. use our brand color
        purple: {
          50: '#F5EBF8',
          100: '#EAD6F0',
          200: '#D5ADE1',
          300: '#C085D2',
          400: '#AB5CC3',
          500: '#9B59B6',   // --crush-purple (brand primary)
          600: '#8E44AD',   // --crush-purple-dark
          700: '#7D3C9B',
          800: '#6C3589',
          900: '#5B2D77',
        },
        // Pink palette matching Crush.lu brand
        pink: {
          50: '#FFF0F5',
          100: '#FFE0EB',
          200: '#FFC1D7',
          300: '#FFA1C3',
          400: '#FF82AF',
          500: '#FF6B9D',   // --crush-pink (brand secondary)
          600: '#D94D7B',   // --crush-pink-dark
          700: '#B33D63',
          800: '#8C304D',
          900: '#662438',
        },
        // Crush-specific color aliases for explicit brand usage
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
          'gray-dark': '#4b5563',
          success: '#28a745',
          warning: '#f39c12',
          danger: '#dc3545',
          info: '#17a2b8',
        },
        // Power-Up brand colors (from logo color extraction)
        powerup: {
          orange: '#fe4901',         // Primary brand color
          'orange-light': '#ff6a2d', // Lighter variant
          'orange-lighter': '#ff8c5a', // Even lighter
          'orange-dark': '#d93e00',  // Darker variant
          'orange-darker': '#b33300', // Even darker
          'dark-blue': '#151625',    // Dark blue accent
          'darker-blue': '#010106',  // Darkest blue
          navy: '#0a0b15',           // Navy variant
          tan: '#80735f',            // Brown/tan accent
          'tan-light': '#a09580',    // Lighter tan
          cream: '#faf8f5',          // Warm cream background
          'warm-gray': '#f5f3f0',    // Warm gray
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
        // Power-Up shadows
        'powerup-sm': '0 2px 10px rgba(254, 73, 1, 0.1)',
        'powerup-md': '0 4px 20px rgba(254, 73, 1, 0.15)',
        'powerup-lg': '0 10px 30px rgba(254, 73, 1, 0.2)',
        'powerup-orange': '0 5px 15px rgba(254, 73, 1, 0.4)',
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
