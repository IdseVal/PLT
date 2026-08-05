/**
 * The unsubscribe and confirmation pages.
 *
 * The properties under test are the ones the whole design rests on: a tokened link works
 * without any login and takes effect at once, the request that changes state is a `POST` so a
 * link scanner cannot cancel a subscription by following the URL, and a reader without a link
 * has a route out that cannot be used to cancel somebody else's subscription.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ConfirmSubscription from '@/pages/ConfirmSubscription'
import Unsubscribe from '@/pages/Unsubscribe'

const TOKEN = 'a-seed-value.a-verifier-value'

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
 * Render a page at a chosen location.
 *
 * @param element - The page element.
 * @param path - Location to render at, including any query string.
 */
function renderAt(element: JSX.Element, path: string): void {
  render(<MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('unsubscribe with a token', () => {
  it('cancels the subscription on load, with no button to find', async () => {
    const fetchMock = stubJson({ status: 'unsubscribed', message: 'Your address has been removed.' })

    renderAt(<Unsubscribe />, `/unsubscribe?token=${encodeURIComponent(TOKEN)}`)

    expect(await screen.findByText('Your address has been removed.')).toHaveAttribute(
      'role',
      'status',
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('changes state with a POST, so a link scanner cannot unsubscribe anybody', async () => {
    const fetchMock = stubJson({ status: 'unsubscribed', message: 'Removed.' })

    renderAt(<Unsubscribe />, `/unsubscribe?token=${encodeURIComponent(TOKEN)}`)

    await screen.findByText('Removed.')
    const [url, init] = fetchMock.mock.calls[0] as [string, { method?: string; body?: string }]
    expect(url).toBe('/api/subscriptions/unsubscribe')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body ?? '')).toEqual({ token: TOKEN })
  })

  it('offers a fresh link when the token is not valid', async () => {
    stubJson(
      { error: { code: 'invalid_token', message: 'This unsubscribe link is not valid.' } },
      400,
    )

    renderAt(<Unsubscribe />, `/unsubscribe?token=${encodeURIComponent(TOKEN)}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('This unsubscribe link is not valid.')
    expect(screen.getByRole('button', { name: /email me an unsubscribe link/i })).toBeInTheDocument()
  })
})

describe('unsubscribe without a token', () => {
  it('asks for an address rather than cancelling anything', () => {
    const fetchMock = stubJson({ status: 'accepted', message: 'unused' })

    renderAt(<Unsubscribe />, '/unsubscribe')

    expect(screen.getByLabelText(/your email address/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends the link to the address itself, and says the same thing either way', async () => {
    const accepted = {
      status: 'accepted',
      message: 'If that address needs an email from us, one is on its way.',
    }
    const fetchMock = stubJson(accepted, 202)
    const user = userEvent.setup()
    renderAt(<Unsubscribe />, '/unsubscribe')

    await user.type(screen.getByLabelText(/your email address/i), 'reader@example.org')
    await user.click(screen.getByRole('button', { name: /email me an unsubscribe link/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(accepted.message)
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })
    const [url, init] = fetchMock.mock.calls[0] as [string, { method?: string; body?: string }]
    expect(url).toBe('/api/subscriptions/unsubscribe-link')
    expect(url).not.toContain('reader@example.org')
    expect(JSON.parse(init.body ?? '')).toEqual({ email: 'reader@example.org' })
  })

  it('catches a typo before asking the server', async () => {
    const fetchMock = stubJson({ status: 'accepted', message: 'unused' }, 202)
    const user = userEvent.setup()
    renderAt(<Unsubscribe />, '/unsubscribe')

    await user.type(screen.getByLabelText(/your email address/i), 'nonsense')
    await user.click(screen.getByRole('button', { name: /email me an unsubscribe link/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/email address/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('confirming a subscription', () => {
  it('confirms on load and tells the reader they are on the list', async () => {
    const fetchMock = stubJson({
      status: 'confirmed',
      message: 'Your address is confirmed. The next weekly digest will include you.',
    })

    renderAt(<ConfirmSubscription />, `/subscribe/confirm?token=${encodeURIComponent(TOKEN)}`)

    expect(await screen.findByText(/your address is confirmed/i)).toHaveAttribute('role', 'status')
    const [url, init] = fetchMock.mock.calls[0] as [string, { method?: string; body?: string }]
    expect(url).toBe('/api/subscriptions/confirm')
    expect(init.method).toBe('POST')
  })

  it('explains an expired link and says nothing about any address', async () => {
    stubJson(
      {
        error: {
          code: 'invalid_token',
          message: 'This confirmation link is not valid. It may have expired.',
        },
      },
      400,
    )

    renderAt(<ConfirmSubscription />, `/subscribe/confirm?token=${encodeURIComponent(TOKEN)}`)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/may have expired/i)
    expect(alert.textContent).not.toContain('@')
  })

  it('does not call the API when the page was opened without a link', () => {
    const fetchMock = stubJson({ status: 'confirmed', message: 'unused' })

    renderAt(<ConfirmSubscription />, '/subscribe/confirm')

    expect(screen.getByRole('alert')).toHaveTextContent(/needs the link/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
