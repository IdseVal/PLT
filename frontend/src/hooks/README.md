# Hooks

Reusable React hooks. Data-fetching hooks call `src/api/client.ts` — never `fetch`
directly (`docs/architecture.md` §6) — and must accept an `AbortSignal` so a component that
unmounts mid-request cancels it.

Empty in the scaffold: hooks arrive with the features that need them.
