/**
 * Renderer for the static pages described by `src/types/content.ts`.
 *
 * One component renders all four of them, so About, Methodology, FAQ and Contact share a
 * layout, a reading measure and a heading hierarchy, and an editor changing the copy only
 * ever edits a content module.
 */

import { Link } from 'react-router-dom'

import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import type { ContentBlock, ContentLink, StaticPageContent } from '@/types/content'
import { isSafeHref } from '@/utils/links'

/** Number of sections above which a jump list is worth rendering. */
const JUMP_LIST_THRESHOLD = 4

/**
 * Render one link from a `links` block, internal or external.
 *
 * @param props - Component properties.
 * @param props.link - The link to render.
 * @returns The rendered link, or `null` when the target is neither a route nor a safe URL.
 */
function ContentLinkItem({ link }: { readonly link: ContentLink }): JSX.Element | null {
  const linkClasses = 'text-plt-accent-strong rounded-sm font-medium underline underline-offset-4'

  if (link.to !== undefined) {
    return (
      <>
        <Link className={linkClasses} to={link.to}>
          {link.label}
        </Link>
        {link.description !== undefined && (
          <span className="text-plt-muted"> — {link.description}</span>
        )}
      </>
    )
  }

  if (link.href === undefined || !isSafeHref(link.href)) return null

  const isExternal = !link.href.startsWith('mailto:')
  return (
    <>
      <a
        className={linkClasses}
        href={link.href}
        {...(isExternal ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      >
        {link.label}
      </a>
      {link.description !== undefined && (
        <span className="text-plt-muted"> — {link.description}</span>
      )}
    </>
  )
}

/**
 * Render a single content block.
 *
 * @param props - Component properties.
 * @param props.block - The block to render.
 * @returns The rendered block.
 */
function Block({ block }: { readonly block: ContentBlock }): JSX.Element {
  switch (block.kind) {
    case 'paragraph':
      return <p className="max-w-prose leading-relaxed">{block.text}</p>

    case 'list': {
      const classes = 'max-w-prose space-y-2 pl-5 leading-relaxed'
      return block.ordered === true ? (
        <ol className={`${classes} list-decimal`}>
          {block.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      ) : (
        <ul className={`${classes} list-disc`}>
          {block.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )
    }

    case 'definitions':
      return (
        <dl className="max-w-prose space-y-3">
          {block.items.map((item) => (
            <div key={item.term}>
              <dt className="text-plt-ink font-semibold">{item.term}</dt>
              <dd className="text-plt-ink leading-relaxed">{item.description}</dd>
            </div>
          ))}
        </dl>
      )

    case 'links':
      return (
        <ul className="max-w-prose space-y-2">
          {block.items.map((item) => (
            <li key={item.label}>
              <ContentLinkItem link={item} />
            </li>
          ))}
        </ul>
      )

    case 'callout':
      return (
        <p className="bg-plt-accent-soft border-plt-accent max-w-prose border-l-4 px-4 py-3 leading-relaxed">
          {block.text}
        </p>
      )
  }
}

/**
 * Render a static page.
 *
 * @param props - Component properties.
 * @param props.content - The page content to render.
 * @returns The page.
 */
export default function StaticPage({
  content,
}: {
  readonly content: StaticPageContent
}): JSX.Element {
  useDocumentTitle(content.title)

  return (
    <article className="space-y-10">
      {/* A `div` rather than a `header`: only the site header should be the banner landmark. */}
      <div className="space-y-4">
        <h1 className="text-plt-accent-deep font-display text-3xl font-bold sm:text-4xl">
          {content.title}
        </h1>
        <p className="text-plt-muted max-w-prose text-lg leading-relaxed">{content.lead}</p>
      </div>

      {content.sections.length >= JUMP_LIST_THRESHOLD && (
        <nav
          aria-label="On this page"
          className="border-plt-border bg-plt-panel max-w-prose border p-4"
        >
          <h2 className="text-plt-muted mb-2 text-xs font-semibold uppercase tracking-wide">
            On this page
          </h2>
          <ul className="max-w-prose space-y-1">
            {content.sections.map((section) => (
              <li key={section.id}>
                <a
                  className="text-plt-accent-strong rounded-sm underline underline-offset-4"
                  href={`#${section.id}`}
                >
                  {section.heading}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      )}

      {content.sections.map((section) => (
        <section key={section.id} id={section.id} className="scroll-mt-8 space-y-4">
          <h2 className="text-plt-accent-deep font-display text-xl font-bold sm:text-2xl">
            {section.heading}
          </h2>
          {section.blocks.map((block, index) => (
            <Block key={`${section.id}-${String(index)}`} block={block} />
          ))}
        </section>
      ))}

      {content.editorialNote !== undefined && (
        <p className="border-plt-border text-plt-muted max-w-prose border-t pt-6 text-sm">
          {content.editorialNote}
        </p>
      )}
    </article>
  )
}
