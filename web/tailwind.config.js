/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        danger: {
          DEFAULT: '#ef4444',
          bright: '#f87171',
          dim: '#7f1d1d',
        },
        safe: {
          DEFAULT: '#22c55e',
          bright: '#4ade80',
          dim: '#14532d',
        },
      },
    },
  },
  plugins: [],
}
