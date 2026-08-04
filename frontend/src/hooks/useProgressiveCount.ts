/**
 * Reveal a long list a chunk at a time instead of all at once.
 *
 * A judgment can run to thousands of paragraphs. Committing all of them to the DOM in one
 * render blocks the main thread long enough to be felt: the page appears, then freezes while
 * layout catches up, and the reader's first scroll janks. Growing the rendered count across
 * animation frames keeps every individual render small, so the first screenful is
 * interactive immediately and the rest arrives without the page ever locking up.
 *
 * It is a rendering strategy, not a "show more" control. No interaction is required and the
 * count always finishes at the total, so the complete text ends up in the DOM and the
 * browser's own find-in-page still works on the whole judgment — which for a legal scholar
 * is not negotiable.
 */

import { useEffect, useState } from 'react'

/**
 * Schedule work for the next frame, falling back to a timer where frames are not available
 * (server rendering, or a test environment without a visual loop).
 *
 * @param callback - Work to run.
 * @returns A function that cancels it.
 */
function onNextFrame(callback: () => void): () => void {
  if (typeof requestAnimationFrame === 'function') {
    const handle = requestAnimationFrame(() => {
      callback()
    })
    return (): void => {
      cancelAnimationFrame(handle)
    }
  }

  const handle = setTimeout(callback, 0)
  return (): void => {
    clearTimeout(handle)
  }
}

/**
 * Grow a rendered item count from one chunk up to the total.
 *
 * @param total - Number of items that will eventually be rendered.
 * @param chunkSize - Items added per frame. Must be at least 1.
 * @returns How many items to render right now.
 */
export function useProgressiveCount(total: number, chunkSize: number): number {
  const step = Math.max(1, chunkSize)
  const [count, setCount] = useState(() => Math.min(total, step))

  // A new document starts the reveal again from the top.
  useEffect(() => {
    setCount(Math.min(total, step))
  }, [total, step])

  useEffect(() => {
    if (count >= total) return undefined

    return onNextFrame(() => {
      setCount((current) => Math.min(total, current + step))
    })
  }, [count, total, step])

  return Math.min(count, total)
}
