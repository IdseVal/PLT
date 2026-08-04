// @vitest-environment node
/**
 * Source guards for two rules that no unit test can catch after the fact.
 *
 * 1. **All server data flows through `src/api/client.ts`** (`docs/architecture.md` section
 *    6). A component calling `fetch` itself would work, and would quietly lose the timeout,
 *    the cancellation and the uniform error envelope.
 * 2. **Court text is untrusted input.** Case titles, abstracts and full texts come from
 *    public sources and are rendered for every visitor, so a single
 *    `dangerouslySetInnerHTML` anywhere under `src/` is a stored-XSS vector. React escapes
 *    text by default; the rule is simply that nothing opts out.
 *
 * Both are cheap to break by accident in a large diff and invisible in review, so they are
 * enforced here rather than trusted to it.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

import { describe, expect, it } from 'vitest'

/** Vitest runs with the frontend package root as its working directory. */
const SRC_DIR = resolve(process.cwd(), 'src')

/** The one module allowed to call `fetch`. */
const FETCH_OWNER = 'api/client.ts'

/** A call to the global `fetch`, as opposed to a mention of it in prose. */
const FETCH_CALL = /(?<![.\w])fetch\s*\(/

/**
 * React's opt-out of escaping, *used* rather than merely named: the trailing `=` or `:` is
 * what distinguishes a JSX attribute or an object property from a mention of the rule in a
 * docstring, which several files here make deliberately.
 */
const RAW_HTML = /dangerouslySetInnerHTML\s*[=:]/

/**
 * Every TypeScript source file, as a path relative to `src/`.
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
    } else if (/\.tsx?$/.test(entry.name)) {
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

describe('source rules', () => {
  it('finds the files it is meant to be guarding', () => {
    const files = sourceFiles()

    expect(files).toContain(FETCH_OWNER)
    expect(files).toContain('components/LatestCasesSidebar.tsx')
    expect(files).toContain('pages/Home.tsx')
  })

  it.each(sourceFiles().filter((file) => file !== FETCH_OWNER))(
    'src/%s calls the API client rather than fetch',
    (file) => {
      expect(readSource(file)).not.toMatch(FETCH_CALL)
    },
  )

  it.each(sourceFiles())('src/%s renders court text as text, never as raw HTML', (file) => {
    expect(readSource(file)).not.toMatch(RAW_HTML)
  })
})
