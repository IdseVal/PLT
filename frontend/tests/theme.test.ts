// @vitest-environment node
/**
 * Theme-token guard.
 *
 * `docs/architecture.md` section 6 requires the palette to be defined once, as Tailwind
 * theme tokens, and never as an ad-hoc value in a component. That matters more than usual
 * here: the current palette and fonts are placeholders, and the Wageningen Law styling
 * package has to be able to land as a one-file change to `tailwind.config.js`. The rule is
 * easy to break by accident and invisible in review of a large diff, so it is enforced: no
 * source file under `src/` may contain a hex colour, a hand-written CSS colour function, or
 * an inline `style` attribute through which either could be smuggled in.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

import { describe, expect, it } from 'vitest'

/** Vitest runs with the frontend package root as its working directory. */
const SRC_DIR = resolve(process.cwd(), 'src')

/** Hex colours: `#abc`, `#aabbcc`, `#aabbccdd`. */
const HEX_COLOUR = /#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{1,5})?\b/
/** CSS colour functions written out by hand. */
const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|oklch|lab)\s*\(/
/** React inline styles, which Tailwind's token classes make unnecessary. */
const INLINE_STYLE = /\sstyle=\{/

/**
 * Every source file the rule applies to, as a path relative to `src/`.
 *
 * Walked by hand rather than with a glob library so the test carries no dependency of its
 * own and behaves identically on every supported Node version.
 *
 * @param directory - Directory to walk. Defaults to `src/`.
 * @returns Sorted relative paths.
 */
function sourceFiles(directory: string = SRC_DIR): readonly string[] {
  const found: string[] = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name)
    if (entry.isDirectory()) {
      found.push(...sourceFiles(absolute))
    } else if (/\.(ts|tsx|css)$/.test(entry.name)) {
      found.push(relative(SRC_DIR, absolute).split(sep).join('/'))
    }
  }
  return found.sort()
}

/**
 * Read one source file.
 *
 * @param relativePath - Path relative to `src/`.
 * @returns The file contents.
 */
function readSource(relativePath: string): string {
  return readFileSync(join(SRC_DIR, relativePath), 'utf8')
}

describe('theme tokens', () => {
  it('finds the source files it is meant to be guarding', () => {
    const files = sourceFiles()

    expect(files.length).toBeGreaterThan(10)
    expect(files).toContain('components/Header.tsx')
    expect(files).toContain('styles/index.css')
  })

  it.each(sourceFiles())('src/%s uses no ad-hoc colour value', (file) => {
    const source = readSource(file)

    expect(source).not.toMatch(HEX_COLOUR)
    expect(source).not.toMatch(COLOUR_FUNCTION)
  })

  it.each(sourceFiles())('src/%s uses no inline style attribute', (file) => {
    expect(readSource(file)).not.toMatch(INLINE_STYLE)
  })
})
