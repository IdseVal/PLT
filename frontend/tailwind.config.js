/**
 * Tailwind configuration.
 *
 * PLACEHOLDER STYLING - REPLACE WHOLESALE
 * ---------------------------------------
 * Every colour and font stack below is a neutral placeholder. None of it is Wageningen Law
 * branding, and none of it is derived from any corporate palette. It exists so the
 * application has a restrained, readable academic look until the Wageningen Law styling
 * package - styling documentation, asset files and fonts - is supplied. When it arrives,
 * replace the values in this file and nothing else: the whole application is expressed
 * through these tokens.
 *
 * That is the point of the naming. Tokens are named for their ROLE (`ink`, `muted`,
 * `surface`, `panel`, `border`, `accent`, `accent-strong`, `accent-deep`, `accent-soft`,
 * `inverse`, `inverse-muted`), not for a colour, so a new palette drops straight in without
 * a single component changing. A raw hex value in a component is a review comment
 * (`docs/architecture.md` section 6) and `tests/theme.test.ts` fails the build if one
 * appears.
 *
 * Contrast budget (WCAG 2.1), measured against `plt.surface` unless stated. AA needs 4.5:1
 * for body text and 3:1 for non-text indicators such as focus rings. Any replacement
 * palette has to hold these ratios, so they are recorded per token.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        plt: {
          /** Body text. 15.4:1 on surface. */
          ink: '#1c1f23',
          /** Secondary text and captions. 6.1:1 on surface, so still AA for body copy. */
          muted: '#565f68',
          /** Page background. */
          surface: '#f6f7f5',
          /** Raised background: header, cards, panels. */
          panel: '#ffffff',
          /** Hairline rules and dividers. Decorative only - never carries meaning alone. */
          border: '#dcdfe3',
          /** Accent for borders and decorative marks. 5.2:1 on surface. */
          accent: '#3f6d8c',
          /** Links and focus rings. 8.4:1 on surface, 8.0:1 on panel. */
          'accent-strong': '#2a4c66',
          /** Headings and the footer band. 12.0:1 on surface; white on it is 12.9:1. */
          'accent-deep': '#1c3446',
          /** Tinted panel for callouts. `plt.ink` on it is 14.6:1. */
          'accent-soft': '#eef2f5',
          /** Text and marks on `accent-deep`. */
          inverse: '#ffffff',
          /** Secondary text on `accent-deep`. 10.2:1 against it. */
          'inverse-muted': '#dfe5ea',
        },
      },
      fontFamily: {
        /**
         * System font stack: nothing is downloaded, so the page makes no third-party
         * request and renders in the reader's own platform face. Replaced when the
         * Wageningen Law fonts are supplied.
         */
        sans: [
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        /**
         * Headings. A system serif, for the academic reading register the brief asks for
         * (`docs/core-document.md` section 3.2). Also replaced with the supplied fonts.
         */
        display: ['Georgia', 'Cambria', 'Times New Roman', 'serif'],
      },
      maxWidth: {
        content: '80rem',
      },
    },
  },
  plugins: [],
}
