/**
 * Vitest setup, loaded before every test file.
 *
 * Registers jest-dom matchers and clears the DOM between tests. No test may reach the
 * network: `fetch` is stubbed per test where a response is needed.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
