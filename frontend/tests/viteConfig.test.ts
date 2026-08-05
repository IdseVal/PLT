// @vitest-environment node
/**
 * The dev server's port and `/api` proxy target must come from the environment.
 *
 * `frontend/.env.example` documents both, and `CONTRIBUTING.md` tells a developer to copy it
 * to `.env.local` and adjust. Vite does not load dotenv files into `process.env`, so a config
 * that reads `process.env` gets `undefined` and falls back — quietly, with the page still
 * loading and every API call proxied to a port nobody configured. Reading a dotenv file is
 * therefore what is asserted here, rather than the mapping from a value to a setting.
 *
 * The config file is evaluated through Vite's own `loadConfigFromFile`, the way the dev
 * server evaluates it, so nothing about how the values are resolved is assumed. The
 * fallbacks are written out again below rather than imported, so that a default quietly
 * changed in the config fails here instead of agreeing with itself.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { ProxyOptions, UserConfig } from 'vite'
import { loadConfigFromFile } from 'vite'

/** The dev-server defaults `frontend/.env.example` documents. */
const DOCUMENTED_PORT = 5173
const DOCUMENTED_PROXY_TARGET = 'http://127.0.0.1:5000'

/** Variables the config reads; cleared per call so a developer's shell decides nothing. */
const DEV_SERVER_VARIABLES = ['VITE_DEV_PORT', 'VITE_DEV_API_PROXY'] as const

const CONFIG_FILE = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'vite.config.ts')

const cleanups: Array<() => void> = []

/**
 * Evaluate `vite.config.ts` in a throwaway directory holding the given dotenv file.
 *
 * The directory becomes the working directory for the call because that is where Vite loads
 * dotenv files from when a developer runs `npm run dev` inside `frontend/`.
 *
 * @param fileName - Dotenv file to write, such as `.env` or `.env.local`.
 * @param contents - Contents of that file.
 * @returns The user config the dev server would be started with.
 */
async function buildConfigWithEnvFile(fileName: string, contents: string): Promise<UserConfig> {
  const directory = mkdtempSync(join(tmpdir(), 'plt-vite-'))
  writeFileSync(join(directory, fileName), contents, 'utf-8')

  const originalCwd = process.cwd()
  const originalValues = new Map(DEV_SERVER_VARIABLES.map((key) => [key, process.env[key]]))
  for (const key of DEV_SERVER_VARIABLES) delete process.env[key]

  cleanups.push(() => {
    process.chdir(originalCwd)
    for (const [key, value] of originalValues) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
    // Windows reports EBUSY for a directory it has only just stopped being the cwd of.
    rmSync(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 50 })
  })

  process.chdir(directory)
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' }, CONFIG_FILE)
  expect(loaded, 'vite.config.ts must be loadable').not.toBeNull()
  return loaded!.config
}

/** Return the `/api` proxy entry, failing the test if it is not an options object. */
function apiProxyOptions(built: UserConfig): ProxyOptions {
  const entry = built.server?.proxy?.['/api']
  expect(entry, '/api must be proxied by the dev server').toBeTypeOf('object')
  return entry as ProxyOptions
}

beforeAll(async () => {
  // Loading a config starts esbuild's long-lived service process, which inherits the working
  // directory and keeps it open. Start it here, from the repository, so that no throwaway
  // directory is left behind undeletable on Windows.
  await loadConfigFromFile({ command: 'serve', mode: 'development' }, CONFIG_FILE)
})

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.()
})

describe('the dev-server configuration', () => {
  it('takes its proxy target and port from .env.local', async () => {
    const built = await buildConfigWithEnvFile(
      '.env.local',
      'VITE_DEV_PORT=5199\nVITE_DEV_API_PROXY=http://127.0.0.1:5055\n',
    )

    expect(built.server?.port).toBe(5199)
    expect(apiProxyOptions(built).target).toBe('http://127.0.0.1:5055')
  })

  it('takes them from .env too, and rewrites the Host header for the backend', async () => {
    const built = await buildConfigWithEnvFile('.env', 'VITE_DEV_API_PROXY=http://127.0.0.1:5066\n')

    const proxy = apiProxyOptions(built)
    expect(proxy.target).toBe('http://127.0.0.1:5066')
    expect(proxy.changeOrigin).toBe(true)
  })

  it('uses the documented defaults when no dotenv file sets them', async () => {
    const built = await buildConfigWithEnvFile('.env', 'VITE_API_BASE_URL=/api\n')

    expect(built.server?.port).toBe(DOCUMENTED_PORT)
    expect(apiProxyOptions(built).target).toBe(DOCUMENTED_PROXY_TARGET)
  })

  it('ignores a port that is not a usable port number', async () => {
    const built = await buildConfigWithEnvFile('.env.local', 'VITE_DEV_PORT=not-a-port\n')

    expect(built.server?.port).toBe(DOCUMENTED_PORT)
  })
})
