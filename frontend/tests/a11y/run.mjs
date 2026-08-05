/**
 * Accessibility and responsive checks against the production build, in a real browser.
 *
 * ## Why this exists
 *
 * Every front-end pull request so far reported a headless-Chrome pass — axe-core across
 * several viewport widths and data states, zero violations. The runs were real and found
 * real defects: a WCAG 1.4.1 contrast failure on a footer link, and Chrome's date inputs
 * matching neither `:focus` nor `:focus-visible` so that the site-wide focus ring silently
 * never applied to them (see `src/components/cases/controls.ts`). But the harness was never
 * in the repository, so none of it was repeatable and none of it stopped a regression. This
 * file is that harness, checked in.
 *
 * ## What it checks, and why these three
 *
 * 1. **axe-core** over `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` and `best-practice` — the
 *    same five rulesets the earlier runs used.
 * 2. **Colour contrast measured on rendered pixels.** `tests/theme.test.ts` bans ad-hoc
 *    colour values in `src/`, which is a different thing: it says where a colour came from,
 *    not what it looks like once composited on the background it ends up over. Only a
 *    browser can answer that, which is why it belongs here.
 * 3. **No horizontal overflow** — `documentElement.scrollWidth === clientWidth` at every
 *    width. jsdom has no layout, so no unit test can see this at all.
 *
 * Plus a fourth that costs almost nothing and guards a defect this project has already had:
 * **every element in the tab order draws a visible focus indicator**.
 *
 * ## Contrast is enforced, with one documented way out
 *
 * The palette in `tailwind.config.js` is a placeholder until the Wageningen Law styling
 * package arrives (README §7), and that file records a contrast ratio per token. A contrast
 * failure today is therefore a regression against a budget the project has written down, not
 * an artefact of the placeholder — so it fails the build.
 *
 * The styling package will replace every one of those values at once, and if the supplied
 * palette does not hold AA this job will go red. That is the correct outcome and the whole
 * reason for measuring: an inaccessible palette should be found in the pull request that
 * introduces it. So that such a pull request can still be *assembled* — assets in one
 * commit, contrast fixes in the next — `A11Y_CONTRAST=report` downgrades contrast
 * violations to a printed report for that one run. It is off by default, and it is the only
 * check with an override.
 *
 * ## Flakiness
 *
 * A flaky accessibility job is worse than none, because it teaches people to ignore a red
 * check. Everything below is fixed rather than sampled:
 *
 * - `puppeteer` and `axe-core` are pinned to exact versions in `package.json`. A caret would
 *   let a new axe minor add a rule, or a new Chrome change a layout, and turn CI red on a
 *   pull request that touched neither.
 * - The API is a local stub serving constant payloads (`fixtures.mjs`); no clock, no
 *   randomness, no network. Requests to anything but the stub origin are blocked and
 *   reported, so the run cannot depend on the network being up.
 * - Ports come from the kernel (`listen(0)`), so two runs never collide.
 * - Readiness is a state, never a sleep: the harness waits until no `fetch` is in flight, no
 *   element is `aria-busy`, and two animation frames have passed. Animations and transitions
 *   are disabled and `prefers-reduced-motion: reduce` is emulated, so nothing is sampled
 *   mid-transition.
 * - Only infrastructure failures (a navigation timeout, a lost browser target) are retried,
 *   once. A violation is never retried.
 *
 * ## The one thing this cannot pin down: fonts
 *
 * The site runs on a system font stack until the Wageningen Law fonts arrive (README §7), so
 * the glyph widths are the *runner's*, not the reader's. A run is therefore reproducible on
 * a given platform and only approximate across platforms: the 3 px overflow on `/cases` that
 * this harness first found is visible with Segoe UI and not with the Liberation faces on
 * `ubuntu-latest`. Two consequences, both worth knowing before trusting a green tick:
 *
 * - **The CI job is the authority for CI.** A local run is a good approximation of it, not
 *   the same measurement, and a width that only just fits is not really passing anywhere.
 * - **A reflow defect can be real on a reader's platform and invisible here.** Nothing in a
 *   browser can fix that while the typeface is whatever the reader happens to have. It stops
 *   being a limitation the day the styling package supplies real font files, because then
 *   every reader and this harness measure the same glyphs.
 *

 * ## Running it
 *
 * ```bash
 * cd frontend
 * npm run build
 * npm run test:a11y                       # everything
 * npm run test:a11y -- --only=home        # one route
 * npm run test:a11y -- --width=320        # one width
 * A11Y_CONTRAST=report npm run test:a11y  # contrast reported, not enforced
 * ```
 *
 * The browser is not installed by `npm ci` (see `puppeteer.config.cjs`); install it once
 * with `npx puppeteer browsers install chrome`.
 */

import { createRequire } from 'node:module'
import { availableParallelism } from 'node:os'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer from 'puppeteer'

import { STATES } from './fixtures.mjs'
import { AXE_TAGS, HEIGHT, PAGE_CASES, WIDTHS } from './matrix.mjs'
import { startStubServer } from './server.mjs'

const require = createRequire(import.meta.url)
const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = resolve(HERE, '..', '..')

/** How long any single navigation or wait may take before it counts as an infrastructure failure. */
const TIMEOUT_MS = 30_000

/** Hard cap on the tab walk, so a page with a focus trap ends the run instead of hanging it. */
const MAX_TAB_STOPS = 200

/** Width the focus-indicator walk is performed at. One is enough: the rule is not responsive. */
const FOCUS_WALK_WIDTH = 1280

/** axe rule ids treated as "contrast", and therefore subject to `A11Y_CONTRAST`. */
const CONTRAST_RULES = new Set(['color-contrast', 'color-contrast-enhanced'])

/**
 * Parse the command line.
 *
 * @param argv - Arguments after the script name.
 * @returns The resolved options.
 */
function parseArguments(argv) {
  const options = {
    only: null,
    widths: WIDTHS,
    distDir: join(FRONTEND_ROOT, 'dist'),
    concurrency: Math.max(1, Math.min(4, availableParallelism())),
    contrast: process.env.A11Y_CONTRAST === 'report' ? 'report' : 'enforce',
  }

  for (const argument of argv) {
    const [flag, value = ''] = argument.split('=', 2)
    switch (flag) {
      case '--only':
        options.only = value
        break
      case '--width': {
        const width = Number.parseInt(value, 10)
        if (!Number.isInteger(width) || width < 200 || width > 4000) {
          throw new Error(`--width needs a viewport width between 200 and 4000, got "${value}".`)
        }
        options.widths = [width]
        break
      }
      case '--dist':
        options.distDir = resolve(process.cwd(), value)
        break
      case '--concurrency': {
        const workers = Number.parseInt(value, 10)
        if (!Number.isInteger(workers) || workers < 1 || workers > 16) {
          throw new Error(`--concurrency needs a number between 1 and 16, got "${value}".`)
        }
        options.concurrency = workers
        break
      }
      case '--contrast':
        if (value !== 'enforce' && value !== 'report') {
          throw new Error(`--contrast takes "enforce" or "report", got "${value}".`)
        }
        options.contrast = value
        break
      default:
        throw new Error(`Unknown argument "${argument}".`)
    }
  }

  return options
}

/**
 * Script the browser runs before anything on the page does.
 *
 * Three jobs, all of them about determinism: count requests in flight so readiness is a fact
 * rather than a guess, kill animations so nothing is measured mid-transition, and leave
 * axe-core on the page so a resize does not need a reload to re-check it.
 *
 * @param axeSource - Contents of `axe.min.js`.
 * @returns The script to install with `evaluateOnNewDocument`.
 */
function preloadScript(axeSource) {
  return `
    (() => {
      window.__pltPending = 0;
      const original = window.fetch;
      window.fetch = function (...args) {
        window.__pltPending += 1;
        return original.apply(this, args).finally(() => { window.__pltPending -= 1; });
      };

      const disableMotion = () => {
        const style = document.createElement('style');
        style.setAttribute('data-a11y-harness', '');
        style.textContent =
          '*,*::before,*::after{transition-duration:0s !important;animation-duration:0s !important;' +
          'animation-delay:0s !important;transition-delay:0s !important;caret-color:transparent !important;' +
          'scroll-behavior:auto !important}';
        document.head.appendChild(style);
      };
      if (document.head) disableMotion();
      else document.addEventListener('DOMContentLoaded', disableMotion, { once: true });
    })();
    ${axeSource}
  `
}

/**
 * Wait until the page has finished settling.
 *
 * Not a sleep: the condition is that the document is complete, no `fetch` the application
 * started is still outstanding, and nothing is still marked `aria-busy`. Two animation
 * frames afterwards let style and layout flush before anything is measured.
 *
 * @param page - The page.
 * @returns Nothing.
 */
async function settle(page) {
  await page.waitForFunction(
    () =>
      document.readyState === 'complete' &&
      window.__pltPending === 0 &&
      document.querySelector('[aria-busy="true"]') === null,
    { timeout: TIMEOUT_MS, polling: 'raf' },
  )
  await page.evaluate(
    () =>
      new Promise((done) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            done(undefined)
          })
        })
      }),
  )
}

/**
 * Run axe-core over the current document.
 *
 * The result is reduced inside the page: a full axe report carries every passing node and is
 * megabytes on a page with a 27-shape map, all of which would otherwise be serialised across
 * the CDP connection for nothing.
 *
 * @param page - The page.
 * @param tags - Rulesets to run.
 * @returns Violations, compactly, and the ids of anything axe could not decide.
 */
async function runAxe(page, tags) {
  return await page.evaluate(async (values) => {
    const results = await window.axe.run(document, {
      runOnly: { type: 'tag', values },
      resultTypes: ['violations'],
      elementRef: false,
      performanceTimer: false,
    })

    return {
      violations: results.violations.map((violation) => ({
        id: violation.id,
        impact: violation.impact,
        help: violation.help,
        helpUrl: violation.helpUrl,
        // Three examples name the problem; a fourth only lengthens the log.
        nodes: violation.nodes.slice(0, 3).map((node) => ({
          target: Array.isArray(node.target) ? node.target.join(' ') : String(node.target),
          summary: (node.failureSummary ?? '').replace(/\s+/g, ' ').trim(),
        })),
        total: violation.nodes.length,
      })),
      incomplete: results.incomplete.map((entry) => entry.id),
    }
  }, tags)
}

/**
 * Measure horizontal overflow.
 *
 * The assertion is the document-level equality, and only that: an element wider than the
 * viewport *inside* a container that scrolls on purpose is correct, and a naive per-element
 * scan would call it a bug. The per-element scan is used only to name the culprits once the
 * document-level check has already failed, so a failure says which element to look at.
 *
 * @param page - The page.
 * @returns Whether the page overflows, and what is sticking out if it does.
 */
async function measureOverflow(page) {
  return await page.evaluate(() => {
    const root = document.documentElement
    const { scrollWidth, clientWidth } = root
    if (scrollWidth === clientWidth) return { overflows: false, by: 0, culprits: [] }

    const culprits = []
    for (const element of document.body.querySelectorAll('*')) {
      const box = element.getBoundingClientRect()
      if (box.width === 0 || box.right <= clientWidth + 1) continue
      culprits.push({
        selector:
          element.tagName.toLowerCase() +
          (element.id === '' ? '' : `#${element.id}`) +
          (typeof element.className === 'string' && element.className !== ''
            ? `.${element.className.trim().split(/\s+/).slice(0, 3).join('.')}`
            : ''),
        right: Math.round(box.right),
      })
      if (culprits.length === 5) break
    }

    return { overflows: true, by: scrollWidth - clientWidth, culprits }
  })
}

/**
 * Walk the tab order and check that every stop draws a visible focus indicator.
 *
 * Real `Tab` presses, not `element.focus()`: `:focus-visible` is defined in terms of how
 * focus arrived, and a programmatic focus does not match it on a button. This is the check
 * that would have caught the date-input defect, where the host element matched neither
 * `:focus` nor `:focus-visible` and the site-wide ring silently did not apply.
 *
 * An indicator counts as visible when the element has a non-zero outline or a box shadow.
 *
 * @param page - The page.
 * @returns Stops with no visible indicator, and how many stops were walked.
 */
async function walkFocusOrder(page) {
  await page.evaluate(() => {
    window.scrollTo(0, 0)
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  })

  const failures = []
  let stops = 0

  for (let index = 0; index < MAX_TAB_STOPS; index += 1) {
    await page.keyboard.press('Tab')
    const stop = await page.evaluate(() => {
      const element = document.activeElement
      if (element === null || element === document.body) return null
      if (element.hasAttribute('data-a11y-first')) return { wrapped: true }
      if (document.querySelector('[data-a11y-first]') === null) {
        element.setAttribute('data-a11y-first', '')
      }

      const style = getComputedStyle(element)
      const outlineWidth = Number.parseFloat(style.outlineWidth)
      const visible =
        (style.outlineStyle !== 'none' && Number.isFinite(outlineWidth) && outlineWidth > 0) ||
        style.boxShadow !== 'none'

      return {
        wrapped: false,
        visible,
        describe:
          element.tagName.toLowerCase() +
          (element.getAttribute('type') === null ? '' : `[type=${element.getAttribute('type')}]`) +
          ' "' +
          (element.getAttribute('aria-label') ?? element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 48) +
          '"',
        outline: `${style.outlineStyle} ${style.outlineWidth}`,
      }
    })

    if (stop === null || stop.wrapped) break
    stops += 1
    if (!stop.visible) failures.push(`${stop.describe} — outline: ${stop.outline}`)
  }

  await page.evaluate(() => {
    document.querySelector('[data-a11y-first]')?.removeAttribute('data-a11y-first')
  })

  return { failures, stops }
}

/**
 * @typedef {object} CaseResult
 * @property {string} label - How the case is named in the report.
 * @property {string[]} lines - One report line per width.
 * @property {string[]} failures - Reasons the case failed, empty when it passed.
 * @property {string[]} contrastReports - Contrast findings when contrast is not enforced.
 * @property {number} incomplete - How many checks axe could not decide.
 */

/**
 * Check one page in one data state, at every width.
 *
 * @param page - A page reserved for this worker.
 * @param pageCase - The route and state to check.
 * @param origin - Origin of the stub server for that state.
 * @param options - Resolved command-line options.
 * @returns What to report.
 */
async function checkCase(page, pageCase, origin, options) {
  const label = `${pageCase.name} [${pageCase.state}]`
  const lines = []
  const failures = []
  const contrastReports = []
  let incomplete = 0

  const blocked = []
  const crashed = []
  const onRequest = (request) => {
    if (request.url().startsWith(origin) || request.url().startsWith('data:')) {
      void request.continue()
      return
    }
    // Nothing the site ships may reach a third party (docs/architecture.md §6). If something
    // starts to, the run must say so rather than quietly depending on the network.
    blocked.push(request.url())
    void request.abort('blockedbyclient')
  }
  const onPageError = (error) => {
    crashed.push(String(error.message ?? error))
  }

  page.on('request', onRequest)
  page.on('pageerror', onPageError)

  try {
    await page.setViewport({ width: options.widths[0], height: HEIGHT, deviceScaleFactor: 1 })
    await page.goto(`${origin}${pageCase.path}`, { waitUntil: 'load', timeout: TIMEOUT_MS })
    await settle(page)

    for (const width of options.widths) {
      await page.setViewport({ width, height: HEIGHT, deviceScaleFactor: 1 })
      await settle(page)

      const [axeResult, overflow] = [await runAxe(page, AXE_TAGS), await measureOverflow(page)]
      incomplete += axeResult.incomplete.length

      const contrast = axeResult.violations.filter((violation) => CONTRAST_RULES.has(violation.id))
      const other = axeResult.violations.filter((violation) => !CONTRAST_RULES.has(violation.id))
      const enforced = options.contrast === 'enforce' ? [...other, ...contrast] : other

      for (const violation of enforced) {
        failures.push(
          `@${width}px  axe ${violation.id} (${violation.impact ?? 'unknown'}, ${violation.total} node(s)): ${violation.help}\n` +
            violation.nodes.map((node) => `        ${node.target} — ${node.summary}`).join('\n') +
            `\n        ${violation.helpUrl}`,
        )
      }
      if (options.contrast === 'report') {
        for (const violation of contrast) {
          contrastReports.push(`@${width}px  ${violation.id}: ${violation.total} node(s) — ${violation.nodes[0]?.target ?? ''}`)
        }
      }

      if (overflow.overflows) {
        failures.push(
          `@${width}px  horizontal overflow: scrollWidth exceeds clientWidth by ${overflow.by}px\n` +
            overflow.culprits.map((culprit) => `        ${culprit.selector} extends to ${culprit.right}px`).join('\n'),
        )
      }

      const contrastNote =
        options.contrast === 'report' && contrast.length > 0 ? `  contrast: ${contrast.length} reported` : ''
      lines.push(
        `${String(width).padStart(4)}px  axe violations: ${String(enforced.length).padStart(2)}` +
          `  overflow: ${overflow.overflows ? `${overflow.by}px` : 'none'}${contrastNote}`,
      )
    }

    if (options.widths.includes(FOCUS_WALK_WIDTH)) {
      await page.setViewport({ width: FOCUS_WALK_WIDTH, height: HEIGHT, deviceScaleFactor: 1 })
      await settle(page)
      const focus = await walkFocusOrder(page)
      if (focus.failures.length > 0) {
        failures.push(
          `@${FOCUS_WALK_WIDTH}px  ${focus.failures.length} of ${focus.stops} tab stops draw no focus indicator\n` +
            focus.failures.map((entry) => `        ${entry}`).join('\n'),
        )
      }
      lines.push(`  focus: ${focus.stops} tab stops, all visible: ${focus.failures.length === 0 ? 'yes' : 'no'}`)
    }

    if (blocked.length > 0) {
      failures.push(`request to a third party: ${[...new Set(blocked)].slice(0, 5).join(', ')}`)
    }
    if (crashed.length > 0) {
      failures.push(`uncaught error on the page: ${[...new Set(crashed)].slice(0, 3).join(' | ')}`)
    }
  } finally {
    page.off('request', onRequest)
    page.off('pageerror', onPageError)
  }

  return { label, lines, failures, contrastReports, incomplete }
}

/**
 * Whether a thrown error is infrastructure rather than a finding.
 *
 * Only these are retried. A violation is a result, and re-running until it disappears is
 * exactly the habit this harness exists to prevent.
 *
 * @param error - The thrown value.
 * @returns Whether one retry is warranted.
 */
function isInfrastructureFailure(error) {
  const message = String(error?.message ?? error)
  return (
    message.includes('Navigation timeout') ||
    message.includes('net::ERR_CONNECTION') ||
    message.includes('Target closed') ||
    message.includes('Session closed') ||
    message.includes('detached Frame')
  )
}

/**
 * Run every case, `concurrency` at a time.
 *
 * @param browser - The browser.
 * @param cases - Cases to run.
 * @param origins - Stub origin per data state.
 * @param options - Resolved command-line options.
 * @param axeSource - Contents of `axe.min.js`.
 * @returns One result per case, in the order the cases were listed.
 */
async function runAll(browser, cases, origins, options, axeSource) {
  const results = new Array(cases.length)
  let next = 0

  /**
   * One worker: take the next case until there are none left.
   *
   * @returns Nothing.
   */
  async function worker() {
    const page = await browser.newPage()
    await page.setRequestInterception(true)
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }])
    await page.evaluateOnNewDocument(preloadScript(axeSource))
    page.setDefaultTimeout(TIMEOUT_MS)

    try {
      for (;;) {
        const index = next
        next += 1
        if (index >= cases.length) return

        const pageCase = cases[index]
        try {
          results[index] = await checkCase(page, pageCase, origins[pageCase.state], options)
        } catch (error) {
          if (!isInfrastructureFailure(error)) throw error
          process.stderr.write(`  retrying ${pageCase.name} [${pageCase.state}]: ${error.message}\n`)
          results[index] = await checkCase(page, pageCase, origins[pageCase.state], options)
        }
      }
    } finally {
      await page.close().catch(() => undefined)
    }
  }

  await Promise.all(Array.from({ length: options.concurrency }, () => worker()))
  return results
}

/**
 * Entry point.
 *
 * @returns The process exit code.
 */
async function main() {
  const options = parseArguments(process.argv.slice(2))

  try {
    readFileSync(join(options.distDir, 'index.html'))
  } catch {
    process.stderr.write(
      `No production build at ${options.distDir}.\nRun \`npm run build\` first — this harness checks the built site, not the dev server.\n`,
    )
    return 1
  }

  let axeSource
  try {
    axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8')
  } catch {
    process.stderr.write('axe-core is not installed. Run `npm ci` in frontend/.\n')
    return 1
  }

  const cases = options.only === null ? PAGE_CASES : PAGE_CASES.filter((entry) => entry.name.includes(options.only))
  if (cases.length === 0) {
    process.stderr.write(`No route matches --only=${options.only}.\n`)
    return 1
  }

  const servers = []
  let browser = null
  /** Set by the signal handlers so the run stops between cases instead of being killed mid-check. */
  let interrupted = false

  /**
   * Release the browser and the ports, whatever happened.
   *
   * @returns Nothing.
   */
  async function shutdown() {
    if (browser !== null) await browser.close().catch(() => undefined)
    await Promise.all(servers.map((server) => server.close().catch(() => undefined)))
  }

  const onSignal = () => {
    if (interrupted) process.exit(130)
    interrupted = true
    process.stderr.write('\nInterrupted — shutting the browser and stub servers down.\n')
    void shutdown().then(() => {
      process.exit(130)
    })
  }
  process.on('SIGINT', onSignal)
  process.on('SIGTERM', onSignal)

  const started = Date.now()
  try {
    for (const state of STATES) {
      servers.push(await startStubServer({ distDir: options.distDir, state }))
    }
    const origins = Object.fromEntries(servers.map((server) => [server.state, server.origin]))

    browser = await puppeteer.launch({
      headless: true,
      args: [
        // The browser only ever loads this repository's own build from 127.0.0.1, so the
        // sandbox is guarding against nothing here; without these it does not start on the
        // hardened kernels CI runners use.
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--force-device-scale-factor=1',
        '--font-render-hinting=none',
        '--force-prefers-reduced-motion',
        '--hide-scrollbars',
      ],
    })

    process.stdout.write(
      `Accessibility and responsive checks\n` +
        `  routes/states: ${cases.length}   widths: ${options.widths.join(', ')}\n` +
        `  rulesets: ${AXE_TAGS.join(', ')}\n` +
        `  contrast: ${options.contrast}   workers: ${options.concurrency}\n` +
        `  axe-core ${JSON.parse(readFileSync(require.resolve('axe-core/package.json'), 'utf8')).version}\n\n`,
    )

    const results = await runAll(browser, cases, origins, options, axeSource)

    let failed = 0
    let incomplete = 0
    for (const result of results) {
      const verdict = result.failures.length === 0 ? 'PASS' : 'FAIL'
      if (result.failures.length > 0) failed += 1
      incomplete += result.incomplete
      process.stdout.write(`${verdict}  ${result.label}\n`)
      for (const line of result.lines) process.stdout.write(`        ${line}\n`)
      for (const report of result.contrastReports) {
        process.stdout.write(`      REPORTED (contrast not enforced) ${report}\n`)
      }
      for (const failure of result.failures) process.stdout.write(`      ${failure}\n`)
    }

    const seconds = ((Date.now() - started) / 1000).toFixed(1)
    const checks = results.length * options.widths.length
    process.stdout.write(
      `\n${results.length} route/state combinations × ${options.widths.length} widths = ${checks} checks in ${seconds}s\n`,
    )
    if (incomplete > 0) {
      process.stdout.write(
        `${incomplete} axe check(s) returned "incomplete" — not failures; axe could not decide and a human should look if the number moves.\n`,
      )
    }

    if (failed > 0) {
      process.stdout.write(`\n${failed} of ${results.length} combinations FAILED\n`)
      return 1
    }
    process.stdout.write('\nALL COMBINATIONS PASSED\n')
    return 0
  } finally {
    process.off('SIGINT', onSignal)
    process.off('SIGTERM', onSignal)
    await shutdown()
  }
}

process.exitCode = await main()
