/**
 * `/subscribe/confirm` — the second half of the double opt-in.
 *
 * The confirmation email links here with a token. The page exchanges it for a confirmation
 * as soon as it loads: the reader has already acted by opening the link, and asking them to
 * press a second button only loses the ones who close the tab.
 *
 * A link that has expired or was never issued is reported plainly, with the way to get a new
 * one. The message says nothing about any address, because the token is all the page knows —
 * which is what keeps this route from being a way to find out who is subscribed.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, confirmSubscription } from '@/api/client'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** What the page knows about the token it was given. */
type ConfirmState =
  | { readonly status: 'working' }
  | { readonly status: 'confirmed'; readonly message: string }
  | { readonly status: 'failed'; readonly message: string }

/**
 * Turn a failure into a sentence for the reader.
 *
 * @param cause - Whatever the request rejected with.
 * @returns What to show.
 */
function message(cause: unknown): string {
  if (!(cause instanceof ApiError)) return 'Something went wrong. Please try again.'
  switch (cause.code) {
    case 'invalid_token':
      return cause.message
    case 'network_error':
      return 'The tracker could not be reached. It may be offline for maintenance.'
    case 'request_aborted':
      return 'The request took too long. Please try again.'
    default:
      return cause.status === 429
        ? 'That is a lot of requests from this connection. Please wait a while and try again.'
        : cause.message
  }
}

/**
 * Render the confirmation page.
 *
 * @returns The page.
 */
export default function ConfirmSubscription(): JSX.Element {
  useDocumentTitle('Confirm your email alerts')
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<ConfirmState>({ status: 'working' })

  const confirm = useCallback((value: string, signal: AbortSignal): void => {
    confirmSubscription(value, signal)
      .then((response) => {
        setState({ status: 'confirmed', message: response.message })
      })
      .catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.code === 'request_aborted') return
        setState({ status: 'failed', message: message(cause) })
      })
  }, [])

  useEffect(() => {
    if (token === '') {
      setState({
        status: 'failed',
        message: 'This page needs the link from the confirmation email we sent you.',
      })
      return undefined
    }
    const controller = new AbortController()
    setState({ status: 'working' })
    confirm(token, controller.signal)
    return () => {
      controller.abort()
    }
  }, [token, confirm])

  return (
    <div className="space-y-6">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold">Email alerts</h1>

      {state.status === 'working' && (
        <p role="status" className="text-plt-muted text-base leading-relaxed">
          Confirming your address…
        </p>
      )}

      {state.status === 'confirmed' && (
        <div className="space-y-4">
          <p role="status" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-base leading-relaxed">
            {state.message}
          </p>
          <p className="text-plt-muted max-w-prose text-sm leading-relaxed">
            Every alert carries a one-click link to stop them, and you can{' '}
            <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/unsubscribe">
              unsubscribe here
            </Link>{' '}
            at any time. In the meantime, the whole collection is open without registration:{' '}
            <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/cases">
              browse the cases
            </Link>
            .
          </p>
        </div>
      )}

      {state.status === 'failed' && (
        <div className="space-y-4">
          <p role="alert" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-base leading-relaxed">
            {state.message}
          </p>
          <p className="text-plt-muted max-w-prose text-sm leading-relaxed">
            Ask for a new link by entering your address on the{' '}
            <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/">
              home page
            </Link>
            . Until a link is used, no address is on the list and nothing is sent to it.
          </p>
        </div>
      )}
    </div>
  )
}
