/**
 * The All-cases filter panel.
 *
 * The filters are the classification of `docs/core-document.md` section 2.2 — jurisdiction,
 * law domain, law subfield, topic, court, language and the decision-date range — plus the
 * full-text query. Their values come from `GET /api/filters`, which returns only values that
 * occur on a published case, so the panel cannot offer a filter that yields nothing.
 *
 * The panel edits a draft and applies it on submit rather than searching on every keystroke.
 * That keeps one filtered view to one URL and one request — a half-typed query never lands
 * in the address bar, and the back button steps through selections a reader actually made.
 * Enter in any field submits, as it would in any form.
 *
 * Below `lg` the panel collapses behind a disclosure button so the results stay reachable on
 * a phone. It is a disclosure, not a modal: focus is never trapped, and the same single copy
 * of every control is used at all widths.
 */

import { useEffect, useId, useState } from 'react'

import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  DATE_INPUT,
  INPUT,
  LABEL,
  PANEL,
  SELECT,
} from '@/components/cases/controls'
import { activeFilterCount, type CaseFilterState } from '@/utils/caseFilters'
import { categoryLabel, cleanInlineText } from '@/utils/caseText'
import type { FilterFacets, KeywordOption } from '@/types/api'

/** Properties of {@link CaseFilters}. */
export interface CaseFiltersProps {
  /** The selection currently in the URL. */
  readonly filters: CaseFilterState
  /** Facet values, or `null` while they load or if they could not be loaded. */
  readonly facets: FilterFacets | null
  /** Whether the facet request failed, which is shown rather than hidden. */
  readonly facetsFailed: boolean
  /** Apply a change to the selection. */
  readonly onApply: (patch: Partial<CaseFilterState>) => void
  /** Drop every filter. */
  readonly onClear: () => void
}

/** One option of a facet select. */
interface FacetOption {
  readonly value: string
  readonly label: string
}

/** Properties of {@link FacetSelect}. */
interface FacetSelectProps {
  readonly id: string
  readonly label: string
  readonly value: string
  readonly options: readonly FacetOption[]
  readonly onChange: (value: string) => void
}

/**
 * Build the options of a facet select, keeping a selected value the facets do not list.
 *
 * A link can be shared after the last case carrying that value was unpublished. Dropping the
 * value silently would make the page contradict its own URL, so it is offered as an option
 * and the reader can see what is being filtered on.
 *
 * @param options - Options from the facet payload.
 * @param selected - The currently selected value.
 * @returns The options to render.
 */
function withSelectedOption(
  options: readonly FacetOption[],
  selected: string,
): readonly FacetOption[] {
  if (selected === '' || options.some((option) => option.value === selected)) return options
  return [...options, { value: selected, label: selected }]
}

/**
 * A labelled select over one facet.
 *
 * @param props - Component properties.
 * @returns The control.
 */
function FacetSelect({ id, label, value, options, onChange }: FacetSelectProps): JSX.Element {
  const choices = withSelectedOption(options, value)

  return (
    <div className="space-y-1">
      <label className={LABEL} htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className={SELECT}
        value={value}
        disabled={choices.length === 0}
        onChange={(event) => {
          onChange(event.target.value)
        }}
      >
        <option value="">Any</option>
        {choices.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/**
 * Turn a list of plain facet strings into select options.
 *
 * @param values - Facet values, possibly absent while the payload loads.
 * @returns Options with the raw value as both value and label.
 */
function toOptions(values: readonly string[] | undefined): readonly FacetOption[] {
  return (values ?? []).map((value) => ({ value, label: cleanInlineText(value) }))
}

/** Properties of {@link KeywordPicker}. */
interface KeywordPickerProps {
  readonly id: string
  readonly value: string
  readonly options: readonly KeywordOption[]
  readonly onChange: (value: string) => void
}

/**
 * The keyword filter: a text box with typeahead over every curated term.
 *
 * A `<select>` is the wrong control here. The lists carry roughly fourteen hundred terms
 * between them — almost all of them active substances with names like
 * `natriumdichloorisocyanuraat` — and no reader scrolls to one. A `datalist` gives the
 * browser's own typeahead over the whole roster, needs no dependency and stays usable with a
 * keyboard and a screen reader.
 *
 * The reader types and sees the term; what travels in the URL is the term's stable id. A
 * typed value that matches no term is not applied, and says so, rather than silently
 * filtering on nothing.
 *
 * Terms that no case carries are listed with a count of zero rather than hidden: a curator
 * looking for "did anything match glyfosaat" needs to see the term and the zero.
 *
 * @param props - Component properties.
 * @returns The control.
 */
function KeywordPicker({ id, value, options, onChange }: KeywordPickerProps): JSX.Element {
  const listId = `${id}-options`
  const selected = options.find((option) => option.id === value)
  // The box shows the term; the state holds the id. While the roster is still loading a
  // selected id has no term to show, so the id itself stands in rather than an empty box.
  const [text, setText] = useState(selected?.term ?? value)
  const [unknown, setUnknown] = useState(false)

  useEffect(() => {
    setText(options.find((option) => option.id === value)?.term ?? value)
    setUnknown(false)
  }, [value, options])

  /**
   * Resolve what the reader typed to a term id, or report that it is not one.
   *
   * @param typed - The current contents of the box.
   */
  const resolve = (typed: string): void => {
    const trimmed = typed.trim()
    if (trimmed === '') {
      setUnknown(false)
      onChange('')
      return
    }
    const match = options.find(
      (option) => option.term.toLowerCase() === trimmed.toLowerCase() || option.id === trimmed,
    )
    setUnknown(match === undefined)
    if (match !== undefined) onChange(match.id)
  }

  return (
    <div className="space-y-1">
      <label className={LABEL} htmlFor={id}>
        Keyword
      </label>
      <input
        id={id}
        className={INPUT}
        list={listId}
        type="text"
        value={text}
        placeholder="Any term, e.g. glyfosaat"
        disabled={options.length === 0}
        aria-describedby={unknown ? `${id}-unknown` : undefined}
        onChange={(event) => {
          setText(event.target.value)
        }}
        onBlur={(event) => {
          resolve(event.target.value)
        }}
      />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={`${option.jurisdiction}-${option.id}`} value={option.term}>
            {`${categoryLabel(option.category)} · ${option.jurisdiction} · ${String(option.case_count)} case(s)`}
          </option>
        ))}
      </datalist>
      {unknown ? (
        <p id={`${id}-unknown`} role="alert" className="text-plt-accent-deep text-sm font-medium">
          No curated term is written exactly that way, so no keyword filter was applied.
        </p>
      ) : null}
    </div>
  )
}

/**
 * The filter panel.
 *
 * @param props - Component properties.
 * @returns The panel, collapsed behind a disclosure button below `lg`.
 */
export default function CaseFilters({
  filters,
  facets,
  facetsFailed,
  onApply,
  onClear,
}: CaseFiltersProps): JSX.Element {
  const [draft, setDraft] = useState<CaseFilterState>(filters)
  const [isOpen, setIsOpen] = useState(false)
  const [rangeError, setRangeError] = useState('')
  const fieldId = useId()
  const panelId = `${fieldId}-panel`
  const errorId = `${fieldId}-range-error`

  // The URL is the source of truth: a back button, a cleared filter or a shared link that
  // lands mid-session has to be reflected in the controls.
  useEffect(() => {
    setDraft(filters)
    setRangeError('')
  }, [filters])

  const count = activeFilterCount(filters)

  /**
   * Change one field of the draft.
   *
   * @param patch - Fields to change.
   */
  const edit = (patch: Partial<CaseFilterState>): void => {
    setDraft((current) => ({ ...current, ...patch }))
  }

  /**
   * Add or remove a jurisdiction from the draft selection.
   *
   * @param code - Jurisdiction code.
   * @param checked - Whether it is now selected.
   */
  const toggleJurisdiction = (code: string, checked: boolean): void => {
    setDraft((current) => ({
      ...current,
      jurisdiction: checked
        ? [...current.jurisdiction, code]
        : current.jurisdiction.filter((value) => value !== code),
    }))
  }

  /**
   * Validate the draft and hand it to the page.
   *
   * @param event - The submit event.
   */
  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault()

    if (draft.date_from !== '' && draft.date_to !== '' && draft.date_from > draft.date_to) {
      setRangeError('The start of the date range falls after its end. Swap the two dates.')
      return
    }

    setRangeError('')
    onApply({
      q: draft.q,
      jurisdiction: draft.jurisdiction,
      law_domain: draft.law_domain,
      law_subfield: draft.law_subfield,
      topic: draft.topic,
      keyword: draft.keyword,
      category: draft.category,
      court: draft.court,
      language: draft.language,
      date_from: draft.date_from,
      date_to: draft.date_to,
    })
  }

  const jurisdictions =
    facets?.jurisdictions ?? filters.jurisdiction.map((code) => ({ code, name: code }))

  return (
    <section className={`${PANEL} p-4`} aria-labelledby={`${fieldId}-heading`}>
      <div className="flex items-center justify-between gap-3">
        <h2 id={`${fieldId}-heading`} className="text-plt-accent-deep font-display text-lg font-bold">
          Filters
        </h2>
        <button
          type="button"
          className={`${BUTTON_SECONDARY} lg:hidden`}
          aria-expanded={isOpen}
          aria-controls={panelId}
          onClick={() => {
            setIsOpen((open) => !open)
          }}
        >
          {isOpen ? 'Hide filters' : `Show filters${count > 0 ? ` (${String(count)})` : ''}`}
        </button>
      </div>

      {facetsFailed ? (
        <p className="text-plt-muted mt-3 text-sm">
          The filter values could not be loaded, so the lists below are empty. Search and the
          date range still work, and a filter already in the address bar is still applied.
        </p>
      ) : null}

      <form
        id={panelId}
        className={`mt-4 space-y-5 ${isOpen ? '' : 'hidden lg:block'}`}
        onSubmit={handleSubmit}
      >
        <div className="space-y-1">
          <label className={LABEL} htmlFor={`${fieldId}-q`}>
            Search
          </label>
          <input
            id={`${fieldId}-q`}
            className={INPUT}
            type="search"
            value={draft.q}
            placeholder="Words in the full text"
            onChange={(event) => {
              edit({ q: event.target.value })
            }}
          />
        </div>

        <fieldset className="space-y-2">
          <legend className={LABEL}>Jurisdiction</legend>
          {jurisdictions.length === 0 ? (
            <p className="text-plt-muted text-sm">No jurisdictions available.</p>
          ) : (
            <ul className="space-y-1">
              {jurisdictions.map((jurisdiction) => (
                <li key={jurisdiction.code}>
                  <label className="text-plt-ink flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="border-plt-border h-4 w-4 rounded-sm"
                      checked={draft.jurisdiction.includes(jurisdiction.code)}
                      onChange={(event) => {
                        toggleJurisdiction(jurisdiction.code, event.target.checked)
                      }}
                    />
                    <span>
                      {cleanInlineText(jurisdiction.name)}{' '}
                      <span className="text-plt-muted">({jurisdiction.code})</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>

        <FacetSelect
          id={`${fieldId}-law-domain`}
          label="Law domain"
          value={draft.law_domain}
          options={toOptions(facets?.law_domains)}
          onChange={(value) => {
            edit({ law_domain: value })
          }}
        />

        <FacetSelect
          id={`${fieldId}-law-subfield`}
          label="Law subfield"
          value={draft.law_subfield}
          options={toOptions(facets?.law_subfields)}
          onChange={(value) => {
            edit({ law_subfield: value })
          }}
        />

        <FacetSelect
          id={`${fieldId}-topic`}
          label="Topic"
          value={draft.topic}
          options={(facets?.topics ?? []).map((topic) => ({
            value: topic.slug,
            label: cleanInlineText(topic.label),
          }))}
          onChange={(value) => {
            edit({ topic: value })
          }}
        />

        <KeywordPicker
          id={`${fieldId}-keyword`}
          value={draft.keyword}
          options={facets?.keywords ?? []}
          onChange={(value) => {
            edit({ keyword: value })
          }}
        />

        <FacetSelect
          id={`${fieldId}-category`}
          label="Category"
          value={draft.category}
          options={(facets?.categories ?? []).map((category) => ({
            value: category,
            label: categoryLabel(category),
          }))}
          onChange={(value) => {
            edit({ category: value })
          }}
        />

        <FacetSelect
          id={`${fieldId}-court`}
          label="Court"
          value={draft.court}
          options={(facets?.courts ?? []).map((court) => ({
            value: String(court.id),
            label: cleanInlineText(court.name),
          }))}
          onChange={(value) => {
            edit({ court: value })
          }}
        />

        <FacetSelect
          id={`${fieldId}-language`}
          label="Language"
          value={draft.language}
          options={toOptions(facets?.languages)}
          onChange={(value) => {
            edit({ language: value })
          }}
        />

        <fieldset className="space-y-2">
          <legend className={LABEL}>Decision date</legend>
          <div className="space-y-1">
            <label className="text-plt-muted block text-sm" htmlFor={`${fieldId}-date-from`}>
              From
            </label>
            <input
              id={`${fieldId}-date-from`}
              className={DATE_INPUT}
              type="date"
              value={draft.date_from}
              min={facets?.decision_date_range?.from ?? undefined}
              max={facets?.decision_date_range?.to ?? undefined}
              aria-describedby={rangeError === '' ? undefined : errorId}
              onChange={(event) => {
                edit({ date_from: event.target.value })
              }}
            />
          </div>
          <div className="space-y-1">
            <label className="text-plt-muted block text-sm" htmlFor={`${fieldId}-date-to`}>
              To
            </label>
            <input
              id={`${fieldId}-date-to`}
              className={DATE_INPUT}
              type="date"
              value={draft.date_to}
              min={facets?.decision_date_range?.from ?? undefined}
              max={facets?.decision_date_range?.to ?? undefined}
              aria-describedby={rangeError === '' ? undefined : errorId}
              onChange={(event) => {
                edit({ date_to: event.target.value })
              }}
            />
          </div>
          {rangeError === '' ? null : (
            <p id={errorId} role="alert" className="text-plt-accent-deep text-sm font-medium">
              {rangeError}
            </p>
          )}
        </fieldset>

        <div className="flex flex-wrap gap-2">
          <button type="submit" className={BUTTON_PRIMARY}>
            Apply filters
          </button>
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={count === 0}
            onClick={onClear}
          >
            Clear all
          </button>
        </div>
      </form>
    </section>
  )
}
