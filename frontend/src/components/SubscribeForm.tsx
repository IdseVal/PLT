/**
 * Front-page signup for the weekly email alert.
 *
 * Placed in the home page's right-hand column, directly beneath the latest-cases sidebar, so
 * the agreed layout (`docs/core-document.md` section 3.3: title, search bar, map, twenty
 * latest cases) is untouched — the form sits *after* all four, next to the feed it is an
 * email version of.
 *
 * Three things this component deliberately does not do:
 *
 * - **It does not report whether an address is already subscribed.** The API answers
 *   identically either way and this form shows what the API said, so nothing here can be used
 *   to test whether somebody is on the list.
 * - **It asks for nothing but an address.** No name, no organisation, no "how did you hear
 *   about us": what is not collected cannot leak.
 * - **It does not pretend the subscription is finished.** The success text says a
 *   confirmation email is on its way, because until that link is used the address is on no
 *   list. That is the double opt-in, stated to the reader rather than hidden from them.
 */

import { useId, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, subscribe } from '@/api/client'

/** Where the form is in its own lifecycle. */
type FormState =
  | { readonly status: 'idle' }
  | { readonly status: 'submitting' }
  | { readonly status: 'accepted'; readonly message: string }
  | { readonly status: 'error'; readonly message: string }

/**
 * The shape an address has to have before the form will send it.
 *
 * Deliberately loose. The server's validation is the one that counts; this exists only to
 * catch a typo without a round trip, and being stricter here would reject valid addresses
 * that the server would have accepted.
 */
const LOOKS_LIKE_AN_ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** Longest address the API accepts, so the input cannot submit one that will be refused. */
const MAX_EMAIL_LENGTH = 254

/**
 * Turn a failure into a sentence for the reader.
 *
 * @param error - The failure.
 * @returns What to show.
 */
function errorMessage(error: ApiError): string {
  switch (error.code) {
    case 'validation_error':
      return 'That does not look like an email address. Check it and try again.'
    case 'mail_unavailable':
      return 'The confirmation email could not be sent just now. Please try again later.'
    case 'network_error':
      return 'The tracker could not be reached. It may be offline for maintenance.'
    case 'request_aborted':
      return 'The request took too long. Please try again.'
    default:
      return error.status === 429
        ? 'That is a lot of requests from this connection. Please wait a while and try again.'
        : error.message
  }
}

/**
 * Render the signup form.
 *
 * @returns The subscribe panel.
 */
export default function SubscribeForm(): JSX.Element {
  const inputId = useId()
  const headingId = useId()
  const [email, setEmail] = useState('')
  const [state, setState] = useState<FormState>({ status: 'idle' })

  const onSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const address = email.trim()
    if (!LOOKS_LIKE_AN_ADDRESS.test(address)) {
      setState({
        status: 'error',
        message: 'Enter an email address, for example name@university.eu.',
      })
      return
    }

    setState({ status: 'submitting' })
    subscribe(address)
      .then((response) => {
        setState({ status: 'accepted', message: response.message })
        setEmail('')
      })
      .catch((cause: unknown) => {
        const failure =
          cause instanceof ApiError
            ? cause
            : new ApiError('The request failed.', 0, 'network_error', {})
        setState({ status: 'error', message: errorMessage(failure) })
      })
  }

  return (
    <section
      aria-labelledby={headingId}
      className="border-plt-border bg-plt-panel space-y-4 rounded border p-5"
    >
      <div className="space-y-1">
        <h2 id={headingId} className="text-plt-accent-deep font-display text-xl font-bold">
          Email alerts
        </h2>
        <p className="text-plt-muted text-sm leading-relaxed">
          One email a week listing the pesticide cases newly added to the tracker. No account,
          no login.
        </p>
      </div>

      {state.status === 'accepted' ? (
        <p role="status" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-sm leading-relaxed">
          {state.message}
        </p>
      ) : (
        <form onSubmit={onSubmit} noValidate className="space-y-3">
          <div className="space-y-1">
            <label htmlFor={inputId} className="text-plt-ink block text-sm font-semibold">
              Your email address
            </label>
            <input
              id={inputId}
              type="email"
              name="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value)
              }}
              maxLength={MAX_EMAIL_LENGTH}
              autoComplete="email"
              required
              aria-describedby={state.status === 'error' ? `${inputId}-error` : undefined}
              aria-invalid={state.status === 'error'}
              className="border-plt-border text-plt-ink w-full rounded border px-3 py-2 text-sm"
            />
          </div>

          {state.status === 'error' && (
            <p id={`${inputId}-error`} role="alert" className="text-plt-ink text-sm leading-relaxed">
              {state.message}
            </p>
          )}

          <button
            type="submit"
            disabled={state.status === 'submitting'}
            className="bg-plt-accent-deep text-plt-inverse w-full rounded px-4 py-2.5 text-center text-sm font-semibold disabled:opacity-60"
          >
            {state.status === 'submitting' ? 'Sending…' : 'Send me the weekly alert'}
          </button>
        </form>
      )}

      <p className="text-plt-muted text-xs leading-relaxed">
        We store your address and the date you confirmed it, and nothing else. It is never
        passed on, and every email carries a one-click link to stop them. You can also{' '}
        <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/unsubscribe">
          unsubscribe here
        </Link>
        .
      </p>
    </section>
  )
}
