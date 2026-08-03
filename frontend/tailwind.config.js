/**
 * Tailwind configuration.
 *
 * The WUR Law group palette is defined here once, as theme tokens. Components use
 * `bg-wur-green`, `text-wur-ink` and so on - a raw hex value in a component is a review
 * comment (`docs/architecture.md` section 6).
 *
 * `wur.green` is the Wageningen University & Research corporate green. The supporting
 * tones are derived from it and are provisional until the Law group brand kit is supplied;
 * replacing them here updates the whole application.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        wur: {
          green: '#34b233',
          'green-dark': '#25801f',
          'green-darker': '#16511a',
          'green-light': '#7ac943',
          'green-tint': '#edf6e8',
          blue: '#0a6ea1',
          soil: '#4b3a2f',
          sand: '#e8e2d8',
          ink: '#1b1b1b',
          muted: '#5b6670',
          border: '#d8dcd6',
          surface: '#f7f9f5',
          white: '#ffffff',
        },
      },
      fontFamily: {
        sans: ['"Open Sans"', 'Segoe UI', 'system-ui', 'sans-serif'],
        display: ['"Open Sans"', 'Georgia', 'serif'],
      },
      maxWidth: {
        content: '80rem',
      },
    },
  },
  plugins: [],
}
