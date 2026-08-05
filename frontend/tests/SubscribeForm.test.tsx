/**
 * The front-page signup form.
 *
 * No test here touches the network: `fetch` is stubbed per test so the real
 * `src/api/client.ts` path runs, because the contract between the form and the client — that
 * the address goes in a POST body and never in a URL — is part of what is being tested.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SubscribeForm from '@/components/SubscribeForm'

/** The one answer the API gives whatever it found. */
const ACCEPTED = {
  status: 'accepted',
  message: 'If that address needs an email from us, one is on its way.',
}

/**
 * Stub `fetch` with a fixed JSON body.
 *
 * @param body - Response body.
 * @param status - HTTP status. Defaults to 200.
 * @returns The mock, for asserting on the request.
 */
function stubJson(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/**
 * Render the form inside a router.
 *
 * @returns The user-event instance for driving the interaction.
 */
function renderForm(): ReturnType<typeof userEvent.setup> {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/']}>
      <SubscribeForm />
    </MemoryRouter>,
  )
  return user
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('subscribe form', () => {
  it('asks for an address and nothing else', () => {
    stubJson(ACCEPTED, 202)
    renderForm()

    expect(screen.getByRole('heading', { level: 2, name: /email alerts/i })).toBeInTheDocument()
    const inputs = screen.getAllByRole('textbox')
    expect(inputs).toHaveLength(1)
    expect(screen.getByLabelText(/your email address/i)).toBeInTheDocument()
  })

  it('posts the address in a body, never in the URL', async () => {
    const fetchMock = stubJson(ACCEPTED, 202)
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })
    const [url, init] = fetchMock.mock.calls[0] as [string, { method?: string; body?: string }]
    expect(url).toBe('/api/subscriptions')
    expect(url).not.toContain('reader@example.org')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body ?? '')).toEqual({ email: 'reader@example.org' })
  })

  it('shows the API message and says a confirmation is needed', async () => {
    stubJson(ACCEPTED, 202)
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    // The form reports what the server said, so it cannot say more than the server does —
    // in particular it never reports whether the address was already subscribed.
    expect(await screen.findByRole('status')).toHaveTextContent(ACCEPTED.message)
    expect(screen.queryByLabelText(/your email address/i)).not.toBeInTheDocument()
  })

  it('catches an obvious typo without a request', async () => {
    const fetchMock = stubJson(ACCEPTED, 202)
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'not-an-address')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/email address/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('explains a rejected address without losing what was typed', async () => {
    stubJson({ error: { code: 'validation_error', message: 'email is not a valid address.' } }, 400)
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/does not look like an email/i)
    expect(screen.getByLabelText(/your email address/i)).toHaveValue('reader@example.org')
  })

  it('explains a rate-limited submission', async () => {
    stubJson({ error: { code: 'ratelimit_exceeded', message: '5 per 1 hour' } }, 429)
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/wait a while/i)
  })

  it('survives an unreachable API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const user = renderForm()

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /weekly alert/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be reached/i)
  })

  it('says what is stored and links to the way out', () => {
    stubJson(ACCEPTED, 202)
    renderForm()

    expect(screen.getByRole('link', { name: /unsubscribe here/i })).toHaveAttribute(
      'href',
      '/unsubscribe',
    )
    expect(screen.getByText(/never passed on/i)).toBeInTheDocument()
  })
})
