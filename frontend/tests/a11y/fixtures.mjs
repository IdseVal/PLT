/**
 * API payloads the accessibility harness serves.
 *
 * Everything here is a constant. No timestamp is read from the clock, no value is randomised
 * and no request reaches a network: a run of `tests/a11y/run.mjs` on a laptop and the same
 * run on a CI machine see byte-identical responses, which is what makes a failure a real
 * finding rather than something to re-run until it goes away.
 *
 * The shapes are `docs/architecture.md` section 5.1, as `src/types/api.ts` declares them.
 * The *content* is chosen to be hostile to the layout in the ways real court data is: an
 * ECLI is a long unbreakable-looking token, a chamber name is a long phrase, and a CELLAR
 * notice can have no title at all. Anything wider than that is not evidence about the site.
 *
 * Three states, named by what the reader sees:
 *
 * - `populated` — the launch-realistic collection. A few jurisdictions hold cases, spanning
 *   orders of magnitude; the rest are present with `case_count: 0`.
 * - `empty` — nothing has been ingested anywhere. Every list is empty and every count is
 *   zero. This is not an edge case: at launch most of the map is the muted "no cases yet"
 *   treatment and `/cases` can legitimately hold nothing.
 * - `error` — every endpoint answers 503 with the section 5.2 error envelope, so every page
 *   has to render its failure state.
 */

/** The three data states the harness renders every route in. */
export const STATES = /** @type {const} */ (['populated', 'empty', 'error'])

/**
 * @typedef {'populated' | 'empty' | 'error'} DataState
 */

/**
 * @typedef {object} StubResponse
 * @property {number} status - HTTP status to answer with.
 * @property {unknown} body - JSON body.
 */

/** Source identifier the detail-page fixtures are published under. */
export const CASE_SOURCE_ID = 'ECLI:NL:HR:2024:1'

/** An identifier no fixture defines, so the detail page renders its not-found state. */
export const MISSING_SOURCE_ID = 'ECLI:NL:RBDHA:1999:999999'

/** Token every subscription fixture accepts. */
export const VALID_TOKEN = 'a11y-valid-token'

/**
 * The section 5.2 error envelope.
 *
 * @param code - Machine-readable code.
 * @param message - Sentence for the reader.
 * @returns The envelope.
 */
function envelope(code, message) {
  return { error: { code, message, details: {} } }
}

/** What every endpoint answers in the `error` state. */
export const SERVICE_UNAVAILABLE = {
  status: 503,
  body: envelope('service_unavailable', 'The case database is unavailable. Try again shortly.'),
}

/**
 * What the two "send me a link" endpoints always answer.
 *
 * `accepted` whatever the server found is the section 5.1 rule: an answer that varied with
 * the state of an address would let anyone test addresses against the tracker.
 */
export const ACCEPTED = {
  status: 202,
  body: {
    status: 'accepted',
    message:
      'If that address is not already on the list, a message is on its way to it. Use the link in it to finish.',
  },
}

/**
 * Twenty cases for the home sidebar and the listing, deterministic and varied.
 *
 * The variation is the point: one case has no title (a metadata-only ECLI, which §5.1 puts
 * on the wire as `null`), one has a title long enough to wrap three times at 320 px, and the
 * court names are the full formal names courts actually publish.
 */
const COURTS = [
  { id: 1, name: 'Hoge Raad der Nederlanden' },
  { id: 2, name: 'Gerechtshof Den Haag, team handel' },
  { id: 3, name: 'College van Beroep voor het bedrijfsleven' },
  { id: 4, name: 'Court of Justice of the European Union (Grand Chamber)' },
]

/** Case titles, including one that is deliberately long and one that is absent. */
const TITLES = [
  'Stichting Bijenbescherming v. State of the Netherlands',
  'Approval of the plant protection product Sumitomo Cyantraniliprole 100 SC',
  null,
  'Association Générale des Producteurs de Maïs and Others v. Ministre de l’Agriculture, de l’Agroalimentaire et de la Forêt, on the interpretation of Article 53 of Regulation (EC) No 1107/2009 concerning emergency authorisations for neonicotinoid seed treatments',
  'Greenpeace Nederland v. College voor de toelating van gewasbeschermingsmiddelen en biociden',
]

/**
 * Build one summary row.
 *
 * @param index - Zero-based position in the feed.
 * @returns A `CaseSummary`.
 */
function summary(index) {
  const court = COURTS[index % COURTS.length]
  const day = String((index % 28) + 1).padStart(2, '0')
  const month = String((index % 12) + 1).padStart(2, '0')
  const euro = index % 5 === 3

  return {
    id: 1000 + index,
    jurisdiction_code: euro ? 'EU' : 'NL',
    jurisdiction_name: euro ? 'European Union' : 'Netherlands',
    source_id: euro ? `62024CJ${String(100 + index)}` : `ECLI:NL:HR:2024:${1000 + index}`,
    title: TITLES[index % TITLES.length],
    court_name: court.name,
    court_id: court.id,
    decision_date: `2024-${month}-${day}`,
    language: euro ? 'en' : 'nl',
    law_domain: index % 2 === 0 ? 'public' : 'private',
    law_subfield: 'environmental law',
    source_url: null,
  }
}

/** The populated feed: twenty cases, newest first, fixed. */
const CASES = Array.from({ length: 20 }, (_, index) => summary(index))

/** One case with everything a detail page can render. */
const FULL_CASE = {
  ...summary(0),
  source_id: CASE_SOURCE_ID,
  filing_date: '2023-11-02',
  publication_date: '2024-03-15',
  abstract:
    'Whether the competent authority may authorise a plant protection product containing a neonicotinoid active substance under the emergency derogation in Article 53 of Regulation (EC) No 1107/2009 where a Union-wide restriction on that substance is already in force.',
  outcome: 'Appeal dismissed',
  procedure_type: 'cassation',
  case_numbers: ['23/01234', '23/01235'],
  revision: 2,
  parties: [
    { id: 1, name: 'Stichting Bijenbescherming', role: 'applicant', ordinal: 1 },
    { id: 2, name: 'State of the Netherlands (Ministry of Agriculture, Nature and Food Quality)', role: 'defendant', ordinal: 1 },
    { id: 3, name: 'Nefyto (Nederlandse Stichting voor Fytofarmacie)', role: 'intervener', ordinal: 1 },
  ],
  topics: [
    { slug: 'neonicotinoids', label: 'Neonicotinoids', confidence: 0.93, assigned_by: 'classifier' },
    { slug: 'emergency-authorisation', label: 'Emergency authorisation', confidence: null, assigned_by: 'manual' },
    { slug: 'pollinators', label: 'Pollinators', confidence: 0.71, assigned_by: 'classifier' },
  ],
  keyword_matches: [
    {
      term_id: 'imidacloprid',
      term: 'imidacloprid',
      list_version: '2026-01',
      field: 'full_text',
      weight_applied: 3,
      match_count: 11,
      snippet: '… het middel bevat imidacloprid, een werkzame stof waarvoor een Unierechtelijke beperking geldt …',
    },
  ],
  citations: [
    {
      target_identifier: '32009R1107',
      target_scheme: 'celex',
      citation_type: 'applies',
      target_title: 'Regulation (EC) No 1107/2009 concerning the placing of plant protection products on the market',
      target_url: null,
    },
  ],
  documents: [
    {
      id: 1,
      language: 'nl',
      doc_type: 'judgment',
      format: 'xml',
      byte_size: 48213,
      retrieved_at: '2024-03-16T04:12:00Z',
      full_text: Array.from(
        { length: 8 },
        (_, index) =>
          `${index + 1}. De Hoge Raad overweegt dat de toelating van een gewasbeschermingsmiddel op grond van artikel 53 van Verordening (EG) nr. 1107/2009 slechts is toegestaan in bijzondere omstandigheden, en dat het bestuursorgaan de noodzaak daarvan zelfstandig moet vaststellen.`,
      ).join('\n\n'),
    },
    {
      id: 2,
      language: 'nl',
      doc_type: 'opinion',
      format: 'html',
      byte_size: 21044,
      retrieved_at: '2024-03-16T04:12:00Z',
      full_text: 'Conclusie van de Procureur-Generaal. Het cassatiemiddel faalt.',
    },
  ],
}

/**
 * The same case with nothing but the two identifying fields populated.
 *
 * A metadata-only record is a real record (`src/api/client.ts` refuses to drop one), and it
 * is the state in which the detail page has the least to lay out — which is where a heading
 * with no content beneath it, or a list with no items, shows up.
 */
const SPARSE_CASE = {
  jurisdiction_code: 'NL',
  jurisdiction_name: null,
  source_id: CASE_SOURCE_ID,
  title: null,
  court_name: null,
  decision_date: null,
  abstract: null,
  documents: [],
  parties: [],
  topics: [],
  keyword_matches: [],
  citations: [],
}

/**
 * Per-jurisdiction counts for the map.
 *
 * `populated` is the launch state the brief describes plus a few more: counts spanning
 * orders of magnitude so the shading bands are all exercised, and every other jurisdiction
 * present with zero, because §5.1 requires zero-case jurisdictions on the wire and the map
 * draws them muted rather than dropping them.
 */
const JURISDICTION_CODES = [
  ['AT', 'Austria'], ['BE', 'Belgium'], ['BG', 'Bulgaria'], ['HR', 'Croatia'],
  ['CY', 'Cyprus'], ['CZ', 'Czechia'], ['DK', 'Denmark'], ['EE', 'Estonia'],
  ['FI', 'Finland'], ['FR', 'France'], ['DE', 'Germany'], ['GR', 'Greece'],
  ['HU', 'Hungary'], ['IE', 'Ireland'], ['IT', 'Italy'], ['LV', 'Latvia'],
  ['LT', 'Lithuania'], ['LU', 'Luxembourg'], ['MT', 'Malta'], ['NL', 'Netherlands'],
  ['PL', 'Poland'], ['PT', 'Portugal'], ['RO', 'Romania'], ['SK', 'Slovakia'],
  ['SI', 'Slovenia'], ['ES', 'Spain'],
]

/** Counts used in the `populated` state; anything unlisted holds nothing yet. */
const POPULATED_COUNTS = { NL: 128, DE: 41, FR: 9, BE: 3, ES: 1 }

/**
 * Build the `/api/stats/jurisdictions` payload.
 *
 * @param populated - Whether any jurisdiction holds cases.
 * @returns The array §5.1 fixes.
 */
function jurisdictionStats(populated) {
  const states = JURISDICTION_CODES.map(([code, name]) => {
    const count = populated ? (POPULATED_COUNTS[code] ?? 0) : 0
    return {
      code,
      name,
      type: 'state',
      map_feature_id: code,
      is_active: code === 'NL',
      case_count: count,
      latest_decision_date: count === 0 ? null : '2024-11-28',
    }
  })

  const euCount = populated ? 1420 : 0
  return [
    ...states,
    {
      code: 'EU',
      name: 'European Union',
      type: 'supranational',
      map_feature_id: 'EU',
      is_active: true,
      case_count: euCount,
      latest_decision_date: euCount === 0 ? null : '2024-12-05',
    },
  ]
}

/** Facet values for the All-cases filters. */
const FACETS = {
  jurisdictions: [
    { code: 'EU', name: 'European Union' },
    { code: 'NL', name: 'Netherlands' },
  ],
  courts: COURTS,
  topics: [
    { slug: 'neonicotinoids', label: 'Neonicotinoids' },
    { slug: 'glyphosate', label: 'Glyphosate' },
    { slug: 'emergency-authorisation', label: 'Emergency authorisation' },
  ],
  law_domains: ['public', 'private', 'criminal'],
  law_subfields: ['environmental law', 'administrative law'],
  languages: ['nl', 'en', 'fr'],
  sorts: ['date_desc', 'date_asc', 'relevance'],
  export_formats: ['csv', 'jsonl'],
  decision_date_range: { from: '2015-01-01', to: '2024-12-05' },
  page_size_default: 20,
  page_size_max: 100,
  latest_limit_max: 50,
}

/** The same facets with nothing in them, for the zero-data state. */
const EMPTY_FACETS = {
  ...FACETS,
  jurisdictions: [],
  courts: [],
  topics: [],
  law_domains: [],
  law_subfields: [],
  languages: [],
  decision_date_range: { from: null, to: null },
}

/**
 * Answer one API request.
 *
 * The dispatch is on the path only. Query parameters change nothing: the harness measures
 * how a page lays out and reads, not whether the API filters correctly, and a fixture that
 * varied with the query would make a run harder to reproduce for no gain.
 *
 * The `POST /api/subscriptions/*` routes are not handled here: their outcome depends on the
 * token in the request body rather than on the data state, so the server reads the body and
 * calls {@link subscriptionResponse}.
 *
 * @param state - Which data state to answer in.
 * @param method - HTTP method.
 * @param pathname - Request path below the origin, `/api/...`, still percent-encoded.
 * @returns The response.
 */
export function apiResponse(state, method, pathname) {
  const path = pathname.replace(/^\/api/, '')
  const populated = state === 'populated'

  if (state === 'error') return SERVICE_UNAVAILABLE

  if (path === '/health') {
    return { status: 200, body: { status: 'ok', service: 'plt', version: '0.1.0', ingest: {} } }
  }

  if (path === '/stats/jurisdictions') {
    return { status: 200, body: jurisdictionStats(populated) }
  }

  if (path === '/cases/latest') {
    return { status: 200, body: { items: populated ? CASES : [], limit: 20 } }
  }

  if (path === '/filters') {
    return { status: 200, body: populated ? FACETS : EMPTY_FACETS }
  }

  if (path === '/cases') {
    const items = populated ? CASES : []
    return {
      status: 200,
      body: {
        items,
        page: 1,
        page_size: 20,
        total: populated ? 128 : 0,
        page_count: populated ? 7 : 0,
        has_next: populated,
      },
    }
  }

  const detail = /^\/cases\/([^/]+)\/(.+)$/.exec(path)
  if (detail !== null && method === 'GET') {
    const sourceId = decodeURIComponent(detail[2])
    if (sourceId !== CASE_SOURCE_ID) {
      return {
        status: 404,
        body: envelope('not_found', 'No case in the tracker has that identifier.'),
      }
    }
    return { status: 200, body: populated ? FULL_CASE : SPARSE_CASE }
  }

  return { status: 404, body: envelope('not_found', 'No such endpoint.') }
}

/**
 * Answer a subscription confirmation or cancellation.
 *
 * Split out from {@link apiResponse} because the outcome depends on the token in the body
 * rather than on the data state, and the body has to be read to see it.
 *
 * @param path - Path below `/api`, e.g. `/subscriptions/confirm`.
 * @param token - The token the page posted, or an empty string.
 * @returns The response.
 */
export function subscriptionResponse(path, token) {
  if (token !== VALID_TOKEN) {
    return {
      status: 400,
      body: envelope(
        'invalid_token',
        'That link has expired or was not issued by the tracker. Ask for a new one below.',
      ),
    }
  }

  return path === '/subscriptions/confirm'
    ? {
        status: 200,
        body: {
          status: 'confirmed',
          message: 'Your address is confirmed. The next weekly digest will reach you.',
        },
      }
    : {
        status: 200,
        body: {
          status: 'unsubscribed',
          message: 'Your address has been removed from the mailing list. Nothing further will be sent.',
        },
      }
}
