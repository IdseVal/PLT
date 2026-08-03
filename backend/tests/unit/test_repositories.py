"""Every repository helper the API layer calls, exercised against a real database.

The corpus below is deliberately small and hand-checkable: three published Dutch cases, one
published EU case and one unpublished Dutch case, so each assertion states a fact that can
be read off the fixture.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from plt.db.models import (
    Case,
    CaseDocument,
    CaseTopic,
    Court,
    DocumentType,
    IngestRun,
    IngestStatus,
    LawDomain,
    Topic,
)
from plt.db.repositories import (
    CaseSearchCriteria,
    CaseSort,
    count_cases,
    get_case_by_id,
    get_case_by_source_id,
    get_case_fingerprint,
    jurisdiction_stats,
    latest_cases,
    latest_successful_runs,
    like_pattern,
    list_facets,
    search_cases,
    stream_cases,
)

#: A cartesian product or an implicit coercion is a bug in a query helper, not a warning.
pytestmark = pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")


@pytest.fixture
def corpus(seeded_session: Session) -> Session:
    """Populate the seeded session with a small, fully specified corpus."""
    court = Court(
        jurisdiction_code="NL",
        source_identifier="https://data.rechtspraak.nl/Instantie/RVS",
        name="Raad van State",
        level="supreme",
        domain="administrative",
    )
    topic = Topic(slug="spray-drift", label="Spray drift")
    seeded_session.add_all([court, topic])
    seeded_session.flush()

    lily = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RVS:2026:1",
        source_system="rechtspraak",
        court_id=court.id,
        title="Lelieteelt nabij natuurgebied",
        abstract="Gebruik van gewasbeschermingsmiddelen bij lelieteelt.",
        decision_date=date(2026, 3, 1),
        language="nl",
        law_domain=LawDomain.PUBLIC,
        law_subfield="administrative",
        content_hash="hash-lily",
    )
    glyphosate = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:HR:2026:2",
        source_system="rechtspraak",
        title="Aansprakelijkheid na drift",
        abstract="Schade door overwaaien van spuitvloeistof.",
        decision_date=date(2026, 1, 15),
        language="nl",
        law_domain=LawDomain.PRIVATE,
        content_hash="hash-drift",
    )
    ctgb = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:CBB:2026:3",
        source_system="rechtspraak",
        title="Toelating door het Ctgb",
        decision_date=None,
        language="nl",
        law_domain=LawDomain.PUBLIC,
        content_hash="hash-ctgb",
    )
    eu_case = Case(
        jurisdiction_code="EU",
        source_id="62026CJ0001",
        source_system="cellar",
        title="Commission v Member State",
        abstract="Plant protection products authorisation, comparable to the Dutch Ctgb regime.",
        decision_date=date(2026, 5, 20),
        language="en",
        law_domain=LawDomain.PUBLIC,
        content_hash="hash-eu",
    )
    draft = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RBDHA:2026:4",
        source_system="rechtspraak",
        title="Nog niet gepubliceerd",
        decision_date=date(2026, 6, 1),
        language="nl",
        is_published=False,
        content_hash="hash-draft",
    )
    seeded_session.add_all([lily, glyphosate, ctgb, eu_case, draft])
    seeded_session.flush()

    lily.documents.append(
        CaseDocument(
            doc_type=DocumentType.JUDGMENT,
            language="nl",
            format="xml",
            full_text="De rechtbank overweegt dat pesticiden zijn gebruikt.",
            raw_payload="<uitspraak/>",
        )
    )
    lily.topics.append(CaseTopic(topic_id=topic.id))
    seeded_session.flush()
    return seeded_session


def source_ids(cases: tuple[Case, ...]) -> list[str]:
    """Return the source identifiers of a result set, in order."""
    return [case.source_id for case in cases]


# --------------------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------------------


def test_search_without_criteria_returns_published_cases_newest_first(corpus: Session) -> None:
    page = search_cases(corpus)

    assert page.total == 4
    assert source_ids(page.items) == [
        "62026CJ0001",
        "ECLI:NL:RVS:2026:1",
        "ECLI:NL:HR:2026:2",
        "ECLI:NL:CBB:2026:3",
    ]


def test_search_excludes_unpublished_cases_unless_asked(corpus: Session) -> None:
    assert count_cases(corpus) == 4
    assert count_cases(corpus, CaseSearchCriteria(include_unpublished=True)) == 5


def test_search_matches_title_abstract_and_full_text(corpus: Session) -> None:
    by_title = search_cases(corpus, CaseSearchCriteria(query="Lelieteelt"))
    by_abstract = search_cases(corpus, CaseSearchCriteria(query="spuitvloeistof"))
    by_full_text = search_cases(corpus, CaseSearchCriteria(query="pesticiden"))
    by_source_id = search_cases(corpus, CaseSearchCriteria(query="RVS:2026"))

    assert source_ids(by_title.items) == ["ECLI:NL:RVS:2026:1"]
    assert source_ids(by_abstract.items) == ["ECLI:NL:HR:2026:2"]
    assert source_ids(by_full_text.items) == ["ECLI:NL:RVS:2026:1"]
    assert source_ids(by_source_id.items) == ["ECLI:NL:RVS:2026:1"]


def test_search_is_case_insensitive(corpus: Session) -> None:
    page = search_cases(corpus, CaseSearchCriteria(query="LELIETEELT"))

    assert page.total == 1


def test_a_wildcard_in_the_query_matches_nothing(corpus: Session) -> None:
    """A user's ``%`` is escaped, so it cannot turn a search into a full table scan."""
    page = search_cases(corpus, CaseSearchCriteria(query="%"))

    assert page.total == 0
    assert like_pattern("100%_a\\b") == "%100\\%\\_a\\\\b%"


def test_search_filters_combine(corpus: Session) -> None:
    criteria = CaseSearchCriteria(
        jurisdictions=("NL",),
        law_domain=LawDomain.PUBLIC,
        law_subfield="administrative",
        language="nl",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 4, 1),
    )

    assert source_ids(search_cases(corpus, criteria).items) == ["ECLI:NL:RVS:2026:1"]


def test_search_filters_by_jurisdiction_court_and_topic(corpus: Session) -> None:
    court_id = corpus.scalars(select(Court.id)).one()

    assert count_cases(corpus, CaseSearchCriteria(jurisdictions=("EU",))) == 1
    assert count_cases(corpus, CaseSearchCriteria(jurisdictions=("EU", "NL"))) == 4
    assert count_cases(corpus, CaseSearchCriteria(court_id=court_id)) == 1
    assert count_cases(corpus, CaseSearchCriteria(topic_slug="spray-drift")) == 1
    assert count_cases(corpus, CaseSearchCriteria(topic_slug="does-not-exist")) == 0


def test_date_sorting_puts_undated_cases_last_in_both_directions(corpus: Session) -> None:
    ascending = search_cases(corpus, CaseSearchCriteria(sort=CaseSort.DATE_ASC))
    descending = search_cases(corpus, CaseSearchCriteria(sort=CaseSort.DATE_DESC))

    assert source_ids(ascending.items)[0] == "ECLI:NL:HR:2026:2"
    assert source_ids(ascending.items)[-1] == "ECLI:NL:CBB:2026:3"
    assert source_ids(descending.items)[-1] == "ECLI:NL:CBB:2026:3"


def test_relevance_ranks_a_title_hit_above_an_abstract_hit(corpus: Session) -> None:
    """Ctgb is in the title of the undated case and in the abstract of the newest one."""
    criteria = CaseSearchCriteria(query="Ctgb", sort=CaseSort.RELEVANCE)

    by_relevance = search_cases(corpus, criteria)
    by_date = search_cases(corpus, CaseSearchCriteria(query="Ctgb"))

    assert source_ids(by_relevance.items) == ["ECLI:NL:CBB:2026:3", "62026CJ0001"]
    assert source_ids(by_date.items) == ["62026CJ0001", "ECLI:NL:CBB:2026:3"]


def test_pagination_reports_totals_and_pages(corpus: Session) -> None:
    first = search_cases(corpus, page=1, page_size=3)
    second = search_cases(corpus, page=2, page_size=3)

    assert len(first.items) == 3
    assert first.page_count == 2
    assert first.has_next is True
    assert len(second.items) == 1
    assert second.has_next is False
    assert set(source_ids(first.items)).isdisjoint(source_ids(second.items))


@pytest.mark.parametrize(("page", "page_size"), [(0, 20), (1, 0), (-1, 20)])
def test_pagination_rejects_nonsense(corpus: Session, page: int, page_size: int) -> None:
    with pytest.raises(ValueError, match="1 or greater"):
        search_cases(corpus, page=page, page_size=page_size)


def test_search_can_eager_load_details(corpus: Session) -> None:
    page = search_cases(corpus, CaseSearchCriteria(query="Lelieteelt"), with_details=True)

    assert len(page.items[0].documents) == 1
    assert page.items[0].documents[0].raw_payload == "<uitspraak/>"


def test_stream_cases_yields_every_match_in_batches(corpus: Session) -> None:
    streamed = list(stream_cases(corpus, batch_size=2))

    assert source_ids(tuple(streamed)) == source_ids(search_cases(corpus, page_size=100).items)

    with pytest.raises(ValueError, match="1 or greater"):
        next(stream_cases(corpus, batch_size=0))


# --------------------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------------------


def test_latest_cases_is_the_newest_published_first(corpus: Session) -> None:
    assert source_ids(latest_cases(corpus, limit=2)) == ["62026CJ0001", "ECLI:NL:RVS:2026:1"]
    assert len(latest_cases(corpus, limit=50)) == 4

    with pytest.raises(ValueError, match="1 or greater"):
        latest_cases(corpus, limit=0)


def test_get_case_by_source_id_uses_the_dedup_key(corpus: Session) -> None:
    found = get_case_by_source_id(corpus, "NL", "ECLI:NL:RVS:2026:1", with_details=True)

    assert found is not None
    assert found.title == "Lelieteelt nabij natuurgebied"
    assert [document.doc_type for document in found.documents] == [DocumentType.JUDGMENT]
    # The same identifier under the wrong jurisdiction is a miss, not a match.
    assert get_case_by_source_id(corpus, "EU", "ECLI:NL:RVS:2026:1") is None
    assert get_case_by_source_id(corpus, "NL", "ECLI:NL:XX:9999:9") is None


def test_get_case_by_id(corpus: Session) -> None:
    known = get_case_by_source_id(corpus, "EU", "62026CJ0001")

    assert known is not None
    assert get_case_by_id(corpus, known.id) is known
    assert get_case_by_id(corpus, known.id, with_details=True) is known
    assert get_case_by_id(corpus, 10_000) is None


def test_get_case_fingerprint_returns_only_the_dedup_columns(corpus: Session) -> None:
    fingerprint = get_case_fingerprint(corpus, "NL", "ECLI:NL:RVS:2026:1")

    assert fingerprint is not None
    assert fingerprint.content_hash == "hash-lily"
    assert fingerprint.revision == 1
    assert fingerprint.last_seen_at.utcoffset() == timedelta(0)
    assert get_case_fingerprint(corpus, "NL", "unknown") is None


# --------------------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------------------


def test_jurisdiction_stats_covers_every_jurisdiction_in_one_query(corpus: Session) -> None:
    stats = {stat.code: stat for stat in jurisdiction_stats(corpus)}

    assert set(stats) == {"EU", "NL"}
    assert stats["NL"].case_count == 3  # the unpublished case is not counted
    assert stats["NL"].latest_decision_date == date(2026, 3, 1)
    assert stats["EU"].case_count == 1
    assert stats["EU"].map_feature_id == "EU"


def test_a_jurisdiction_without_cases_is_still_returned(seeded_session: Session) -> None:
    stats = {stat.code: stat for stat in jurisdiction_stats(seeded_session)}

    assert [stat.case_count for stat in stats.values()] == [0, 0]
    assert stats["EU"].latest_decision_date is None


def test_list_facets_offers_only_values_that_occur(corpus: Session) -> None:
    facets = list_facets(corpus)

    assert facets.jurisdictions == (("EU", "European Union"), ("NL", "Netherlands"))
    assert facets.courts == ((1, "Raad van State"),)
    assert set(facets.law_domains) == {"public", "private"}
    assert facets.law_subfields == ("administrative",)
    assert set(facets.languages) == {"en", "nl"}
    assert facets.topics == (("spray-drift", "Spray drift"),)
    assert facets.earliest_decision_date == date(2026, 1, 15)
    assert facets.latest_decision_date == date(2026, 5, 20)


def test_latest_successful_runs_ignores_failed_and_older_runs(corpus: Session) -> None:
    started = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    corpus.add_all(
        [
            IngestRun(
                jurisdiction_code="NL",
                connector="rechtspraak",
                started_at=started,
                finished_at=started + timedelta(minutes=5),
                status=IngestStatus.SUCCESS,
                inserted_count=2,
            ),
            IngestRun(
                jurisdiction_code="NL",
                connector="rechtspraak",
                started_at=started + timedelta(days=7),
                finished_at=started + timedelta(days=7, minutes=5),
                status=IngestStatus.SUCCESS,
                inserted_count=9,
                updated_count=1,
            ),
            IngestRun(
                jurisdiction_code="NL",
                connector="rechtspraak",
                started_at=started + timedelta(days=14),
                finished_at=started + timedelta(days=14, minutes=1),
                status=IngestStatus.FAILED,
                error_count=1,
            ),
            IngestRun(
                jurisdiction_code="EU",
                connector="eurlex",
                started_at=started,
                status=IngestStatus.RUNNING,
            ),
        ]
    )
    corpus.flush()

    summaries = latest_successful_runs(corpus)

    assert [summary.jurisdiction_code for summary in summaries] == ["NL"]
    assert summaries[0].inserted_count == 9
    assert summaries[0].finished_at == started + timedelta(days=7, minutes=5)
