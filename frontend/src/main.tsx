/**
 * Application entry point.
 *
 * Mounts the router and the root component. Nothing else belongs here: routes live in
 * `App.tsx` and data access goes through `src/api/client.ts`.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from '@/App'
import '@/styles/index.css'

const container = document.getElementById('root')
if (container === null) {
  throw new Error('Root element #root is missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
