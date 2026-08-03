/// <reference types="vite/client" />

/**
 * Environment variables exposed to the browser bundle.
 *
 * Every entry is documented in `frontend/.env.example`. Only `VITE_`-prefixed variables are
 * exposed by Vite, and nothing secret may be put here: these values ship in the bundle.
 */
interface ImportMetaEnv {
  /** Base URL of the PLT API. Empty or relative keeps the API on the site's own origin. */
  readonly VITE_API_BASE_URL?: string
  /** Per-request timeout in milliseconds. */
  readonly VITE_API_TIMEOUT_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
