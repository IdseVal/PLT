/**
 * Puppeteer configuration.
 *
 * Puppeteer downloads a ~150 MB Chrome build in its `postinstall` hook by default. Only
 * `tests/a11y/run.mjs` needs it, so every other `npm ci` — a developer's, and the `Frontend
 * (eslint, vitest, build)` job in CI — would be paying for a browser it never launches.
 *
 * The download is therefore turned off here and requested explicitly by whoever needs it:
 *
 * ```bash
 * npx puppeteer browsers install chrome
 * ```
 *
 * That command installs the exact build the pinned `puppeteer` version expects, so the
 * browser stays as fixed as the library. The accessibility job in `.github/workflows/ci.yml`
 * runs it behind a cache keyed on the `puppeteer` version, which is why that job does not
 * re-download Chrome on every pull request.
 *
 * @type {import('puppeteer').Configuration}
 */
module.exports = {
  skipDownload: true,
}
