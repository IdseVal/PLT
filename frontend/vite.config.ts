/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

/** Dev-server port used when `VITE_DEV_PORT` is unset or not a usable port number. */
const DEFAULT_DEV_PORT = 5173

/** Backend the `/api` proxy forwards to when `VITE_DEV_API_PROXY` is unset. */
const DEFAULT_DEV_API_PROXY = 'http://127.0.0.1:5000'

/** Dev-server settings resolved from the environment. */
interface DevServerOptions {
  /** Port the dev server listens on. */
  port: number
  /** Backend origin the `/api` proxy forwards to. */
  proxyTarget: string
}

/**
 * Resolve the dev server's port and proxy target from a loaded environment.
 *
 * @param env - Environment as returned by Vite's `loadEnv`, dotenv files included.
 * @returns The port and proxy target to run the dev server with; the documented default for
 *   anything the environment leaves unset or sets to something unusable.
 */
function resolveDevServer(env: Record<string, string | undefined>): DevServerOptions {
  const port = Number(env.VITE_DEV_PORT)
  const portIsUsable = Number.isInteger(port) && port > 0 && port <= 65535

  return {
    port: portIsUsable ? port : DEFAULT_DEV_PORT,
    // `||`, not `??`: an empty or blank value is as unset as a missing one.
    proxyTarget: env.VITE_DEV_API_PROXY?.trim() || DEFAULT_DEV_API_PROXY,
  }
}

/**
 * Vite configuration.
 *
 * The dev server proxies `/api` to the backend so a developer can run the frontend against
 * a local Flask instance without CORS configuration or a build-time base URL. The proxy
 * target and the port are read from `VITE_DEV_API_PROXY` and `VITE_DEV_PORT` (see
 * `.env.example`), never hard-coded.
 *
 * `loadEnv` is what makes that true. Vite does not put dotenv files on `process.env`, so
 * reading `process.env` directly ignores `.env.local` entirely and silently falls back —
 * which is how a frontend once proxied to a port nobody had configured. The empty prefix
 * loads every key rather than only `VITE_`-prefixed ones; nothing loaded here is injected
 * into the bundle, because these two values configure the dev-server process itself.
 */
export default defineConfig(({ mode }) => {
  const { port, proxyTarget } = resolveDevServer(loadEnv(mode, process.cwd(), ''))

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./tests/setup.ts'],
      include: ['tests/**/*.{test,spec}.{ts,tsx}', 'src/**/*.{test,spec}.{ts,tsx}'],
      css: false,
    },
  }
})
