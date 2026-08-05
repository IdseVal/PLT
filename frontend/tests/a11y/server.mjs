/**
 * The server the accessibility harness drives a browser against.
 *
 * It serves the **production build** from `dist/` — the same bytes a reader gets — plus a
 * stub of the API pinned to one data state. No dev server, no Vite middleware, no backend
 * and no network: the harness measures the site as it ships, and does so identically on
 * every machine.
 *
 * One server is started per data state, each on its own ephemeral port, so the three states
 * can be visited in parallel without a shared mutable "current state" that would make two
 * concurrent page loads interfere. A port is never chosen by the harness; the kernel hands
 * one out (`listen(0)`), which is what keeps a second run on the same machine from colliding
 * with the first.
 *
 * Written as a dependency-free Node script for the same reason
 * `scripts/generate-map-geometry.mjs` is: a check that runs on every pull request should not
 * add a server framework to the tree it is checking.
 */

import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, normalize, resolve, sep } from 'node:path'

import { ACCEPTED, SERVICE_UNAVAILABLE, apiResponse, subscriptionResponse } from './fixtures.mjs'

/** Content types for everything a Vite build emits. */
const CONTENT_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

/** Largest request body the stub will read, so a stuck client cannot exhaust memory. */
const MAX_BODY_BYTES = 64 * 1024

/**
 * Send a JSON response.
 *
 * @param response - The response to write to.
 * @param status - HTTP status.
 * @param body - Value to serialise.
 * @returns Nothing.
 */
function sendJson(response, status, body) {
  const payload = JSON.stringify(body)
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    // The harness must see the state it asked for, never a copy of the previous state.
    'cache-control': 'no-store',
  })
  response.end(payload)
}

/**
 * Read a request body, bounded.
 *
 * @param request - Incoming request.
 * @returns The body as text, truncated at {@link MAX_BODY_BYTES}.
 */
async function readBody(request) {
  let size = 0
  const chunks = []
  for await (const chunk of request) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) break
    chunks.push(chunk)
  }
  return Buffer.concat(chunks).toString('utf8')
}

/**
 * Resolve a URL path to a file inside `dist/`, refusing anything that escapes it.
 *
 * The harness only ever asks for its own paths, so this cannot be reached by an attacker
 * here. It is written anyway because a directory-serving function that trusts its input is
 * the same function whatever it is used for, and the check costs two lines.
 *
 * @param distDir - Absolute path of the build output.
 * @param pathname - Percent-encoded URL path.
 * @returns The absolute file path, or `null` when the path escapes `distDir` or is malformed.
 */
function resolveStaticPath(distDir, pathname) {
  let decoded
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0')) return null

  const candidate = resolve(distDir, `.${normalize(decoded)}`)
  const root = distDir.endsWith(sep) ? distDir : `${distDir}${sep}`
  return candidate === distDir || candidate.startsWith(root) ? candidate : null
}

/**
 * Answer a request for a built asset, falling back to `index.html`.
 *
 * The fallback is what makes a deep link such as `/cases/NL/ECLI:NL:HR:2024:1` load: the
 * site is a single-page application, so every path that is not a file on disk is the
 * application's own route table's business.
 *
 * @param distDir - Absolute path of the build output.
 * @param pathname - Percent-encoded URL path.
 * @param response - The response to write to.
 * @returns Nothing.
 */
async function serveStatic(distDir, pathname, response) {
  const filePath = resolveStaticPath(distDir, pathname)
  if (filePath === null) {
    response.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' })
    response.end('Bad request')
    return
  }

  let target = filePath
  try {
    const info = await stat(target)
    if (info.isDirectory()) target = join(target, 'index.html')
    else if (!info.isFile()) target = join(distDir, 'index.html')
  } catch {
    target = join(distDir, 'index.html')
  }

  try {
    await stat(target)
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
    response.end('Not found')
    return
  }

  response.writeHead(200, {
    'content-type': CONTENT_TYPES[extname(target).toLowerCase()] ?? 'application/octet-stream',
    'cache-control': 'no-store',
  })
  createReadStream(target)
    .on('error', () => {
      response.destroy()
    })
    .pipe(response)
}

/**
 * Start a server for one data state.
 *
 * @param options - Build directory and data state.
 * @param options.distDir - Absolute path of `frontend/dist`.
 * @param options.state - `populated`, `empty` or `error`.
 * @returns The origin to point a browser at, and a function that shuts the server down.
 */
export async function startStubServer({ distDir, state }) {
  // Normalised once, here, rather than trusted as given: a path written with the wrong
  // separator for the platform never matches its own prefix check, and every request would
  // then be refused as an escape attempt.
  const root = resolve(distDir)

  const server = createServer((request, response) => {
    void handle(request, response)
  })

  /**
   * Route one request.
   *
   * @param request - Incoming request.
   * @param response - Response to write to.
   * @returns Nothing.
   */
  async function handle(request, response) {
    const { pathname } = new URL(request.url ?? '/', 'http://127.0.0.1')

    if (!pathname.startsWith('/api/') && pathname !== '/api') {
      // A body on a static request is nothing the harness sends, but leaving one unread
      // holds the socket open, so it is drained either way.
      if (request.method !== 'GET' && request.method !== 'HEAD') await readBody(request)
      await serveStatic(root, pathname, response)
      return
    }

    if (request.method === 'POST') {
      const body = await readBody(request)
      const path = pathname.replace(/^\/api/, '')

      if (state === 'error') {
        sendJson(response, SERVICE_UNAVAILABLE.status, SERVICE_UNAVAILABLE.body)
        return
      }

      if (path === '/subscriptions/confirm' || path === '/subscriptions/unsubscribe') {
        let token = ''
        try {
          token = String(JSON.parse(body).token ?? '')
        } catch {
          token = ''
        }
        const answer = subscriptionResponse(path, token)
        sendJson(response, answer.status, answer.body)
        return
      }

      sendJson(response, ACCEPTED.status, ACCEPTED.body)
      return
    }

    const answer = apiResponse(state, request.method ?? 'GET', pathname)
    sendJson(response, answer.status, answer.body)
  }

  await new Promise((resolvePromise, rejectPromise) => {
    server.once('error', rejectPromise)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', rejectPromise)
      resolvePromise(undefined)
    })
  })

  const address = server.address()
  if (address === null || typeof address === 'string') {
    throw new Error('The stub server did not bind to a port.')
  }

  return {
    state,
    origin: `http://127.0.0.1:${address.port}`,
    /**
     * Stop the server and release the port.
     *
     * @returns Nothing.
     */
    close: () =>
      new Promise((resolvePromise) => {
        server.closeAllConnections()
        server.close(() => {
          resolvePromise(undefined)
        })
      }),
  }
}
