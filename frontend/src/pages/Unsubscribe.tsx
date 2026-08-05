/**
 * `/unsubscribe` — the way off the mailing list, from an email or from the site.
 *
 * Two entrances, because a reader has to be able to leave whether or not they still have one
 * of our emails to hand.
 *
 * **With a token** (`/unsubscribe?token=…`, the link in every message): the page cancels the
 * subscription as soon as it loads. No button to find, no login, no "are you sure" — an
 * unsubscribe that asks a question is one that fails for the reader who has already decided.
 * The request is a `POST`, so a mailbox provider's link scanner following the URL to check it
 * is safe cannot cancel anybody's subscription by doing so.
 *
 * **Without a token**: a form that asks for an address and has the link emailed to it. That
 * is the only way to offer this without a login and without letting a stranger cancel
 * somebody else's subscription, and the answer is the same whether or not the address is on
 * the list, so the page cannot be used to check who is.
 */

import { useCallback, useEffect, useId, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, requestUnsubscribeLink, unsubscribe } from '@/api/client'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

/** State of the tokened path. */
type TokenState =
  | { readonly status: 'working' }
  | { readonly status: 'done'; readonly message: string }
  | { readonly status: 'failed'; readonly message: string }

/** State of the "email me the link" form. */
type FormState =
  | { readonly status: 'idle' }
  | { readonly status: 'submitting' }
  | { readonly status: 'accepted'; readonly message: string }
  | { readonly status: 'error'; readonly message: string }

/** Loose client-side check; the server's validation is the one that decides. */
const LOOKS_LIKE_AN_ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** Longest address the API accepts. */
const MAX_EMAIL_LENGTH = 254

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
    case 'validation_error':
      return 'That does not look like an email address. Check it and try again.'
    case 'mail_unavailable':
      return 'The email could not be sent just now. Please try again later.'
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

/** Properties of {@link RequestLinkForm}. */
interface RequestLinkFormProps {
  /** Whether the page reached this form after a token failed, which changes the wording. */
  readonly afterFailure: boolean
}

/**
 * The address form, for a reader with no unsubscribe link to hand.
 *
 * @param props - Component properties.
 * @returns The form.
 */
function RequestLinkForm({ afterFailure }: RequestLinkFormProps): JSX.Element {
  const inputId = useId()
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
    requestUnsubscribeLink(address)
      .then((response) => {
        setState({ status: 'accepted', message: response.message })
        setEmail('')
      })
      .catch((cause: unknown) => {
        setState({ status: 'error', message: message(cause) })
      })
  }

  if (state.status === 'accepted') {
    return (
      <p role="status" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-sm leading-relaxed">
        {state.message}
      </p>
    )
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-md space-y-3">
      <p className="text-plt-muted text-sm leading-relaxed">
        {afterFailure
          ? 'Enter your address and we will send a fresh unsubscribe link to it.'
          : 'Every alert we send carries an unsubscribe link. If you no longer have one, enter your address and we will send a link to it.'}
      </p>
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
        className="bg-plt-accent-deep text-plt-inverse rounded px-4 py-2.5 text-sm font-semibold disabled:opacity-60"
      >
        {state.status === 'submitting' ? 'Sending…' : 'Email me an unsubscribe link'}
      </button>
    </form>
  )
}

/**
 * Render the unsubscribe page.
 *
 * @returns The page.
 */
export default function Unsubscribe(): JSX.Element {
  useDocumentTitle('Unsubscribe')
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [state, setState] = useState<TokenState | null>(token === '' ? null : { status: 'working' })

  const cancel = useCallback((value: string, signal: AbortSignal): void => {
    unsubscribe(value, signal)
      .then((response) => {
        setState({ status: 'done', message: response.message })
      })
      .catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.code === 'request_aborted') return
        setState({ status: 'failed', message: message(cause) })
      })
  }, [])

  useEffect(() => {
    if (token === '') {
      setState(null)
      return undefined
    }
    const controller = new AbortController()
    setState({ status: 'working' })
    cancel(token, controller.signal)
    return () => {
      controller.abort()
    }
  }, [token, cancel])

  return (
    <div className="space-y-6">
      <h1 className="text-plt-accent-deep font-display text-3xl font-bold">Email alerts</h1>

      {state?.status === 'working' && (
        <p role="status" className="text-plt-muted text-base leading-relaxed">
          Removing your address from the mailing list…
        </p>
      )}

      {state?.status === 'done' && (
        <div className="space-y-4">
          <p role="status" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-base leading-relaxed">
            {state.message}
          </p>
          <p className="text-plt-muted max-w-prose text-sm leading-relaxed">
            Nothing further will be sent. The tracker itself stays open to everyone, without
            registration —{' '}
            <Link className="text-plt-accent-strong rounded-sm underline underline-offset-4" to="/cases">
              browse the collection
            </Link>{' '}
            whenever you like.
          </p>
        </div>
      )}

      {state?.status === 'failed' && (
        <div className="space-y-4">
          <p role="alert" className="border-plt-border bg-plt-accent-soft text-plt-ink rounded border p-4 text-base leading-relaxed">
            {state.message}
          </p>
          <RequestLinkForm afterFailure />
        </div>
      )}

      {state === null && <RequestLinkForm afterFailure={false} />}
    </div>
  )
}
