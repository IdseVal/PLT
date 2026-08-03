/**
 * Fallback for unknown routes.
 */

import { Link } from 'react-router-dom'

export default function NotFound(): JSX.Element {
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <Link to="/" className="text-wur-green-dark underline">
        Back to the home page
      </Link>
    </section>
  )
}
