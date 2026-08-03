import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiBaseUrl, getHealth, request } from '@/api/client'

function mockFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('api client', () => {
  it('reads the base URL from the environment', () => {
    expect(apiBaseUrl).toBe(import.meta.env.VITE_API_BASE_URL ?? '/api')
  })

  it('requests the health endpoint under the base URL', async () => {
    const fetchMock = mockFetch(
      jsonResponse({ status: 'ok', service: 'plt-api', version: '0.1.0', ingest: {} }),
    )

    const health = await getHealth()

    expect(health.status).toBe('ok')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${apiBaseUrl}/health`)
  })

  it('serialises query parameters and repeats array values', async () => {
    const fetchMock = mockFetch(jsonResponse({ items: [] }))

    await request('/cases', {
      query: { q: 'glyfosaat', jurisdiction: ['NL', 'EU'], page: 2, empty: undefined },
    })

    const url = String(fetchMock.mock.calls[0]?.[0])
    expect(url).toContain('q=glyfosaat')
    expect(url).toContain('jurisdiction=NL&jurisdiction=EU')
    expect(url).toContain('page=2')
    expect(url).not.toContain('empty')
  })

  it('turns the error envelope into an ApiError', async () => {
    mockFetch(
      jsonResponse(
        { error: { code: 'validation_error', message: 'page_size too large', details: {} } },
        400,
      ),
    )

    await expect(request('/cases')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      code: 'validation_error',
      message: 'page_size too large',
    })
  })

  it('reports a transport failure as a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error: unknown = await request('/cases').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).isNetworkError).toBe(true)
  })
})
