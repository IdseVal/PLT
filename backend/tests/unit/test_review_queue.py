"""The review queue: the flag, the decision and the endpoint that serves them.

``docs/CORE_DOCUMENT.md`` section 2.7 buys precision back by review, so the properties
asserted here are the ones that policy stands on:

* a flagged case is published exactly like any other — the flag adds a review, it does not
  withhold the case;
* a decision, once taken, survives the weekly run over the same content;
* a genuine upstream revision puts the case back in the queue instead of letting the new text
  inherit the old verdict;
* a rejection hides a case without deleting it or its evidence;
* re-running a window produces the same flags — section 2.8's repeatability requirement,
  asserted rather than assumed.

**Nothing raises a flag automatically any more.** "Borderline" meant "just above the
threshold", and selection is now a word search with no threshold to be near, so the flag is
the content manager's to raise.

:class:`FlagsAlfa` stands in for whatever raises it, which is what keeps everything downstream
of the flag under test. The stage tests run against synthetic keyword lists written into
``tmp_path``. The shipped lists are curated data and their contents are the content manager's to
change; a test that depended on them would turn a curation decision into a build failure.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from plt.app import create_app
from plt.config import Settings
from plt.db.base import Base
from plt.db.models import (
    Case,
    CaseReview,
    CaseReviewDecision,
    Jurisdiction,
    JurisdictionType,
    KeywordMatch,
    ReviewDecision,
    ReviewStatus,
)
from plt.db.repositories import (
    ReviewSearchCriteria,
    ReviewSort,
    record_review_decision,
    search_reviews,
)
from plt.db.session import create_session_factory, make_engine
from plt.extensions import dispose_database
from plt.pipeline.filters.base import Filter, FilterableDocument, FilterChain, FilterResult
from plt.pipeline.filters.keywords import KeywordFilter, load_keyword_list
from plt.pipeline.runner import IngestReport, run_jurisdiction
from tests.conftest import REPO_ROOT, build_settings
from tests.fakes import EPOCH, FakeConnector, FakeDocument

SCHEMA_PATH = REPO_ROOT / "data" / "keywords" / "schema.json"

#: The token the test application accepts on the review endpoints.
TOKEN = "review-token-for-tests"

#: Filler so a synthetic document reads like a judgment rather than like a term list.
BOILERPLATE = (
    "De rechtbank overweegt dat het bestuursorgaan in redelijkheid tot het besluit heeft "
    "kunnen komen en dat het beroep ongegrond is. "
)

#: Names the term the flagging stage below reacts to.
BAND_TEXT = f"{BOILERPLATE} Het geschil betreft alfamiddel in de sloot."

#: Selected by the list, but not flagged.
CLEAR_TEXT = f"{BOILERPLATE} Het geschil betreft betamiddel in de sloot."

#: Matches no term: rejected outright, and therefore never a review item.
UNRELATED_TEXT = f"{BOILERPLATE} Het geschil betreft een huurovereenkomst."


# ----------------------------------------------------------------------------------------
# Synthetic lists
# ----------------------------------------------------------------------------------------


def make_list() -> dict[str, Any]:
    """Build a schema-valid Dutch list with two terms.

    Returns:
        The list document.

    Both terms select on their own, which is the whole of the selection rule now; ``alfamiddel``
    is the one the flagging stage below reacts to.
    """
    return {
        "schema_version": "2.0.0",
        "jurisdiction": "NL",
        "jurisdiction_name": "Netherlands",
        "list_version": "9.9.9",
        "updated": "2026-08-17",
        "languages": ["nl"],
        "fields": ["title", "abstract", "full_text"],
        "terms": [
            {
                "id": "nl-alfa",
                "term": "alfamiddel",
                "lang": "nl",
                "category": "product_class",
            },
            {
                "id": "nl-beta",
                "term": "betamiddel",
                "lang": "nl",
                "category": "product_class",
            },
        ],
    }


def write_list(directory: Path, document: dict[str, Any]) -> Path:
    """Write a synthetic list next to a copy of the real schema.

    Args:
        directory: Directory to write into.
        document: The list document.

    Returns:
        Path to the written list.
    """
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCHEMA_PATH, directory / "schema.json")
    path = directory / "nl.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def build_filter(directory: Path, document: dict[str, Any]) -> KeywordFilter:
    """Compile a synthetic list into a filter stage.

    Args:
        directory: Directory to write the list into.
        document: The list document.

    Returns:
        The compiled stage.
    """
    return KeywordFilter(load_keyword_list(write_list(directory, document)))


@dataclass
class Doc:
    """A normalised case as far as a filter stage is concerned."""

    jurisdiction_code: str = "NL"
    title: str | None = None
    abstract: str | None = None
    full_text: str | None = None
    subject: str | None = None


# ----------------------------------------------------------------------------------------
# Flagging
# ----------------------------------------------------------------------------------------


class FlagsAlfa(Filter):
    """A stage that flags any case labelled ``nl-alfa`` for review.

    Nothing in the pipeline raises a review flag by itself any more.

    "Borderline" meant "only just above the threshold", and a word search has no threshold, so
    the flag became the content manager's to raise. This stage stands in for whatever raises it
    - a rule, a person, a later classifier - so that everything downstream of the flag stays
    under test.
    """

    name = "flags-alfa"

    def evaluate(self, case: FilterableDocument) -> FilterResult:
        """Pass every document, flagging the ones a curator would want to look at."""
        text = (getattr(case, "full_text", None) or "").lower()
        return FilterResult(
            passed=True,
            reason="flagged for review" if "alfamiddel" in text else "not flagged",
            stage=self.name,
            needs_review="alfamiddel" in text,
        )


def test_nothing_is_flagged_by_the_keyword_stage_alone(tmp_path: Path) -> None:
    """The keyword stage selects; it no longer decides that a case looks uncertain."""
    stage = build_filter(tmp_path, make_list())

    flagged = stage.evaluate(Doc(full_text=BAND_TEXT))
    clear = stage.evaluate(Doc(full_text=CLEAR_TEXT))

    assert flagged.passed
    assert clear.passed
    assert not flagged.needs_review
    assert not clear.needs_review


def test_a_rejected_case_is_never_flagged(tmp_path: Path) -> None:
    stage = build_filter(tmp_path, make_list())

    result = stage.evaluate(Doc(full_text=f"{BOILERPLATE} Een gewoon huurgeschil."))

    assert not result.passed
    assert not result.needs_review


def test_a_chain_keeps_a_flag_raised_by_an_earlier_stage(tmp_path: Path) -> None:
    """A flag is a statement about the document, not about the stage that noticed it.

    A later stage that passes the document must not erase it, or appending a stage would
    quietly empty the queue.
    """
    chain = FilterChain.of(FlagsAlfa(), build_filter(tmp_path, make_list()))

    result = chain.evaluate(Doc(full_text=BAND_TEXT))

    assert result.passed
    assert result.needs_review, "the later stage passed it; the earlier stage's flag stands"


# ----------------------------------------------------------------------------------------
# Ingestion
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """What one stored case and its review row look like, read while the session is open.

    Values rather than ORM objects, so an assertion cannot accidentally lazy-load from a
    closed session — and so a comparison between two runs compares data, which is what the
    repeatability requirement is about.
    """

    published: bool
    needs_review: bool
    matched_term_count: int | None
    revision: int
    content_hash: str | None
    status: ReviewStatus | None = None
    decision: ReviewDecision | None = None
    decided_by: str | None = None
    decided_revision: int | None = None
    decision_note: str | None = None
    list_version: str | None = None
    flagged_at: datetime | None = None
    flagged_revision: int | None = None
    flagged_content_hash: str | None = None
    decisions: tuple[tuple[str, str, str | None], ...] = ()


def _snapshot(case: Case) -> Snapshot:
    """Read one case and its review row into a detached value.

    Args:
        case: The case, loaded in an open session.

    Returns:
        The snapshot.
    """
    review = case.review
    if review is None:
        return Snapshot(
            published=case.is_published,
            needs_review=case.needs_review,
            matched_term_count=case.matched_term_count,
            revision=case.revision,
            content_hash=case.content_hash,
        )
    return Snapshot(
        published=case.is_published,
        needs_review=case.needs_review,
        matched_term_count=case.matched_term_count,
        revision=case.revision,
        content_hash=case.content_hash,
        status=review.status,
        decision=review.decision,
        decided_by=review.decided_by,
        decided_revision=review.decided_revision,
        decision_note=review.decision_note,
        list_version=review.list_version,
        flagged_at=review.flagged_at,
        flagged_revision=review.flagged_revision,
        flagged_content_hash=review.flagged_content_hash,
        decisions=tuple(
            (str(entry.decision.value), entry.decided_by, entry.note) for entry in review.decisions
        ),
    )


@dataclass
class Harness:
    """A temporary database, a synthetic chain, and the settings a run is driven with."""

    settings: Settings
    factory: sessionmaker[Session]
    chain: FilterChain

    def run(self, connector: FakeConnector, **overrides: object) -> IngestReport:
        """Run the connector against the harness's database."""
        options: dict[str, object] = {
            "connector": connector,
            "settings": self.settings,
            "session_factory": self.factory,
            "chain": self.chain,
        }
        options.update(overrides)
        return run_jurisdiction("NL", **options)  # type: ignore[arg-type]

    def session(self) -> Session:
        """Open a session on the harness's database."""
        return self.factory()

    def stored(self) -> Snapshot:
        """Return a snapshot of the only case in the database."""
        with self.session() as session:
            return _snapshot(session.scalars(select(Case)).one())

    def snapshots(self) -> dict[str, Snapshot]:
        """Return a snapshot per stored case, keyed by source identifier."""
        with self.session() as session:
            return {case.source_id: _snapshot(case) for case in session.scalars(select(Case))}


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    """Build a temporary database and a chain built on the synthetic list."""
    settings = build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'plt.db'}",
        pipeline_report_dir=tmp_path / "reports",
    )
    engine: Engine = make_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add(
            Jurisdiction(
                code="NL", name="Netherlands", type=JurisdictionType.STATE, map_feature_id="NL"
            )
        )
        session.commit()
    chain = FilterChain.of(FlagsAlfa(), build_filter(tmp_path / "keywords", make_list()))
    try:
        yield Harness(settings=settings, factory=factory, chain=chain)
    finally:
        engine.dispose()


def document(source_id: str = "ECLI:NL:RBTEST:2026:1", **overrides: Any) -> FakeDocument:  # noqa: ANN401
    """Build one fake source document, borderline by default."""
    fields: dict[str, Any] = {"source_id": source_id, "text": BAND_TEXT, "title": "Uitspraak"}
    fields.update(overrides)
    return FakeDocument(**fields)


def decide(
    harness: Harness,
    decision: ReviewDecision,
    decided_by: str = "content-manager@example.invalid",
    note: str | None = None,
) -> None:
    """Record a decision on the only review item, as the endpoint would."""
    with harness.session() as session:
        review = session.scalars(select(CaseReview)).one()
        record_review_decision(session, review, decision=decision, decided_by=decided_by, note=note)
        session.commit()


def test_a_flagged_case_is_ingested_published_and_queued(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))

    stored = harness.stored()

    assert stored.published, "a flag adds a review; it does not withhold the case"
    assert stored.needs_review
    assert stored.matched_term_count == 1, "one curated term selected it"
    assert stored.status is ReviewStatus.PENDING
    assert stored.decision is None
    assert stored.list_version == "9.9.9"
    assert stored.flagged_revision == 1
    assert stored.flagged_content_hash == stored.content_hash


def test_a_case_above_the_band_is_ingested_without_a_queue_item(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document(text=CLEAR_TEXT)]))

    stored = harness.stored()

    assert stored.published
    assert not stored.needs_review
    assert stored.status is None, "no queue item exists for a confident pass"


def test_a_decision_survives_a_re_ingestion_of_the_same_case(harness: Harness) -> None:
    docs = [document()]
    harness.run(FakeConnector(docs=docs))
    decide(harness, ReviewDecision.CONFIRMED, decided_by="agent:triage-1", note="in scope")
    before = harness.stored()

    harness.run(FakeConnector(docs=docs), since=EPOCH)

    after = harness.stored()
    assert after.status is ReviewStatus.CONFIRMED
    assert after.decision is ReviewDecision.CONFIRMED
    assert after.decided_by == "agent:triage-1"
    assert after.decision_note == "in scope"
    assert after.flagged_at == before.flagged_at, "the same content must not re-raise the flag"
    assert after.decisions == before.decisions == (("confirmed", "agent:triage-1", "in scope"),)


def test_an_upstream_revision_of_a_decided_case_is_flagged_again(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))
    decide(harness, ReviewDecision.CONFIRMED, decided_by="content-manager")

    revised = document(title="Uitspraak (gerectificeerd)", payload_suffix="<!-- corrected -->")
    harness.run(FakeConnector(docs=[revised]), since=EPOCH)

    stored = harness.stored()
    assert stored.revision == 2
    assert stored.status is ReviewStatus.PENDING, "the new text has not been reviewed"
    assert stored.decision is ReviewDecision.CONFIRMED, "the previous verdict stays visible"
    assert stored.decided_revision == 1
    assert stored.flagged_revision == 2
    assert stored.flagged_content_hash == stored.content_hash
    assert len(stored.decisions) == 1, "re-flagging is not a decision"


def test_a_rejection_unpublishes_the_case_without_deleting_it(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))

    decide(harness, ReviewDecision.REJECTED, decided_by="content-manager", note="off topic")

    with harness.session() as session:
        case = session.scalars(select(Case)).one()
        kept = (case.title is not None, len(case.documents))
        matches = session.scalars(select(KeywordMatch)).all()
        entries = [
            (entry.decision, entry.decided_by, entry.note, entry.decided_at)
            for entry in session.scalars(select(CaseReviewDecision))
        ]

    stored = harness.stored()
    assert not stored.published
    assert kept == (True, 1), "the case row and its documents are kept"
    assert matches, "the evidence of why it was selected is kept"
    assert entries == [(ReviewDecision.REJECTED, "content-manager", "off topic", entries[0][3])]
    assert entries[0][3] is not None


def test_a_rejected_case_stays_unpublished_across_a_revision(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))
    decide(harness, ReviewDecision.REJECTED)

    revised = document(title="Uitspraak (gerectificeerd)", payload_suffix="<!-- corrected -->")
    harness.run(FakeConnector(docs=[revised]), since=EPOCH)

    stored = harness.stored()
    assert not stored.published, "a revision must not republish what a reviewer rejected"
    assert stored.status is ReviewStatus.PENDING


def test_confirming_a_rejected_case_publishes_it_again(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))
    decide(harness, ReviewDecision.REJECTED)
    assert not harness.stored().published

    decide(harness, ReviewDecision.CONFIRMED, decided_by="content-manager")

    stored = harness.stored()
    assert stored.published
    assert stored.status is ReviewStatus.CONFIRMED
    assert len(stored.decisions) == 2, "both verdicts are on the record"


def test_a_revision_that_leaves_the_band_withdraws_an_undecided_item(harness: Harness) -> None:
    harness.run(FakeConnector(docs=[document()]))

    revised = document(text=CLEAR_TEXT, payload_suffix="<!-- expanded -->")
    harness.run(FakeConnector(docs=[revised]), since=EPOCH)

    stored = harness.stored()
    assert not stored.needs_review
    assert stored.status is ReviewStatus.WITHDRAWN
    assert stored.published


def test_re_running_a_window_produces_identical_flags(harness: Harness) -> None:
    """Repeatability, asserted rather than assumed (core document section 2.8)."""
    docs = [
        document("ECLI:NL:RBTEST:2026:1"),
        document("ECLI:NL:RBTEST:2026:2", text=CLEAR_TEXT),
        document("ECLI:NL:RBTEST:2026:3", text=UNRELATED_TEXT),
        document("ECLI:NL:RBTEST:2026:4"),
    ]

    harness.run(FakeConnector(docs=docs))
    first = harness.snapshots()
    harness.run(FakeConnector(docs=docs), since=EPOCH)
    second = harness.snapshots()

    assert {
        source_id: (stored.needs_review, stored.matched_term_count, stored.status)
        for source_id, stored in first.items()
    } == {
        "ECLI:NL:RBTEST:2026:1": (True, 1, ReviewStatus.PENDING),
        "ECLI:NL:RBTEST:2026:2": (False, 1, None),
        "ECLI:NL:RBTEST:2026:4": (True, 1, ReviewStatus.PENDING),
    }
    assert second == first, "the second run reproduced the first exactly"


def test_the_match_report_records_the_flag(harness: Harness) -> None:
    report_path = harness.settings.pipeline_report_dir / "run.jsonl"

    harness.run(FakeConnector(docs=[document()]), dry_run=True, report_path=report_path)

    lines = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    judged = [line for line in lines if line["type"] == "case"]
    assert judged[0]["needs_review"] is True
    assert judged[0]["matched_term_count"] == 1
    assert [entry["term"] for entry in judged[0]["terms"]] == ["alfamiddel"]
    assert [entry["category"] for entry in judged[0]["terms"]] == ["product_class"]


# ----------------------------------------------------------------------------------------
# The repository layer
# ----------------------------------------------------------------------------------------


def test_the_queue_lists_pending_items_oldest_first_by_default(harness: Harness) -> None:
    harness.run(
        FakeConnector(docs=[document("ECLI:NL:RBTEST:2026:1"), document("ECLI:NL:RBTEST:2026:2")])
    )

    with harness.session() as session:
        page = search_reviews(session, ReviewSearchCriteria(), page=1, page_size=10)
        decided = session.scalars(select(CaseReview)).first()
        assert decided is not None
        record_review_decision(
            session, decided, decision=ReviewDecision.CONFIRMED, decided_by="agent"
        )
        session.commit()
        remaining = search_reviews(session, ReviewSearchCriteria())
        everything = search_reviews(session, ReviewSearchCriteria(statuses=()))

    assert page.total == 2
    assert page.page_count == 1
    assert not page.has_next
    assert remaining.total == 1, "a decided item leaves the pending queue"
    assert everything.total == 2


def test_an_empty_queue_is_one_empty_page(harness: Harness) -> None:
    with harness.session() as session:
        page = search_reviews(session, ReviewSearchCriteria(), page=1, page_size=20)

    assert page.items == ()
    assert page.total == 0
    assert page.page_count == 1
    assert not page.has_next


def test_the_queue_can_be_ordered_newest_first(harness: Harness) -> None:
    """A queue is ordered by when it was flagged.

    The score orderings went with the band: they ranked items by distance from a
    threshold, and there is no longer a threshold to be at a distance from.
    """
    harness.run(
        FakeConnector(
            docs=[
                document("ECLI:NL:RBTEST:2026:1"),
                document("ECLI:NL:RBTEST:2026:2"),
            ]
        )
    )

    with harness.session() as session:
        newest = search_reviews(session, ReviewSearchCriteria(sort=ReviewSort.FLAGGED_DESC))
        oldest = search_reviews(session, ReviewSearchCriteria(sort=ReviewSort.FLAGGED_ASC))

    assert [item.case.source_id for item in newest.items] == list(
        reversed([item.case.source_id for item in oldest.items])
    )


# ----------------------------------------------------------------------------------------
# The API
# ----------------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Override the shared fixture with one that enables the review endpoints."""
    return build_settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        rate_limit_enabled=False,
        review_api_token=TOKEN,
    )


@pytest.fixture
def queued(seeded_session: Session) -> Session:
    """Commit one pending and one rejected review item, with their evidence."""
    borderline = Case(
        jurisdiction_code="NL",
        source_id="ECLI:NL:RBTEST:2026:1",
        source_system="fake",
        title="Borderline case",
        content_hash="hash-1",
        needs_review=True,
        matched_term_count=1,
    )
    borderline.keyword_matches.append(
        KeywordMatch(
            term_id="nl-alfa",
            term="alfamiddel",
            list_version="9.9.9",
            category="product_class",
            field="full_text",
            match_count=1,
            snippet="… alfamiddel in de sloot …",
        )
    )
    borderline.review = CaseReview(
        status=ReviewStatus.PENDING,
        list_version="9.9.9",
        reason="matched 1 curated term (NL list v9.9.9): nl-alfa",
        flagged_revision=1,
        flagged_content_hash="hash-1",
    )
    rejected = Case(
        jurisdiction_code="EU",
        source_id="62019CJ0616",
        source_system="fake",
        title="Rejected case",
        content_hash="hash-2",
        needs_review=True,
        matched_term_count=1,
        is_published=False,
    )
    rejected.review = CaseReview(
        status=ReviewStatus.REJECTED,
        decision=ReviewDecision.REJECTED,
        decided_by="content-manager",
        decided_revision=1,
        list_version="9.9.9",
        flagged_revision=1,
        flagged_content_hash="hash-2",
        suppressed_publication=True,
    )
    seeded_session.add_all([borderline, rejected])
    seeded_session.commit()
    return seeded_session


def auth() -> dict[str, str]:
    """Return the headers a review request is made with."""
    return {"Authorization": f"Bearer {TOKEN}"}


def test_the_queue_requires_a_token(client: FlaskClient, queued: Session) -> None:
    del queued

    response = client.get("/api/reviews")

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"


def test_a_wrong_token_is_refused(client: FlaskClient, queued: Session) -> None:
    del queued

    response = client.get("/api/reviews", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_the_queue_is_disabled_without_a_configured_token(tmp_path: Path) -> None:
    application: Flask = create_app(
        build_settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'closed.db'}",
            rate_limit_enabled=False,
        )
    )
    try:
        with application.test_client() as closed:
            response = closed.get("/api/reviews", headers=auth())
    finally:
        dispose_database(application)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "review_queue_disabled"


def test_the_queue_lists_pending_items_with_their_evidence(
    client: FlaskClient, queued: Session
) -> None:
    del queued

    payload = client.get("/api/reviews", headers=auth()).get_json()

    assert [item["case"]["source_id"] for item in payload["items"]] == ["ECLI:NL:RBTEST:2026:1"]
    assert payload["total"] == 1
    assert payload["page_count"] == 1
    assert payload["has_next"] is False

    item = payload["items"][0]
    assert item["status"] == "pending"
    assert item["matched_term_count"] == 1
    assert item["list_version"] == "9.9.9"
    assert item["decision"] is None, "a field with no value is present and null"
    assert item["decided_by"] is None
    assert item["decision_is_current"] is False
    assert item["published"] is True
    assert item["decisions"] == []
    assert item["keyword_matches"] == [
        {
            "term_id": "nl-alfa",
            "term": "alfamiddel",
            "category": "product_class",
            "list_version": "9.9.9",
            "field": "full_text",
            "match_count": 1,
            "snippet": "… alfamiddel in de sloot …",
        }
    ]


def test_the_queue_is_filterable_and_paginated(client: FlaskClient, queued: Session) -> None:
    del queued

    everything = client.get("/api/reviews?status=any", headers=auth()).get_json()
    rejected = client.get("/api/reviews?status=rejected", headers=auth()).get_json()
    dutch = client.get("/api/reviews?status=any&jurisdiction=NL", headers=auth()).get_json()
    versioned = client.get("/api/reviews?status=any&list_version=0.0.1", headers=auth()).get_json()
    second = client.get("/api/reviews?status=any&page=2&page_size=1", headers=auth()).get_json()

    assert everything["total"] == 2
    assert [item["status"] for item in rejected["items"]] == ["rejected"]
    assert [item["case"]["jurisdiction_code"] for item in dutch["items"]] == ["NL"]
    assert versioned["total"] == 0
    assert second["page"] == 2
    assert second["total"] == 2
    assert len(second["items"]) == 1


def test_a_bad_status_is_rejected(client: FlaskClient, queued: Session) -> None:
    del queued

    response = client.get("/api/reviews?status=maybe", headers=auth())

    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["parameter"] == "status"


def test_a_decision_is_recorded_and_returned(client: FlaskClient, queued: Session) -> None:
    review_id = (
        queued.scalars(select(CaseReview).where(CaseReview.status == ReviewStatus.PENDING)).one().id
    )

    response = client.post(
        f"/api/reviews/{review_id}/decision",
        headers=auth(),
        json={"decision": "rejected", "decided_by": "agent:triage-1", "note": "not pesticides"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["status"] == "rejected"
    assert payload["decision"] == "rejected"
    assert payload["decided_by"] == "agent:triage-1"
    assert payload["decided_at"] is not None
    assert payload["decision_is_current"] is True
    assert payload["published"] is False
    assert [entry["decision"] for entry in payload["decisions"]] == ["rejected"]

    queued.expire_all()
    case = queued.scalars(select(Case).where(Case.source_id == "ECLI:NL:RBTEST:2026:1")).one()
    assert not case.is_published
    assert case.keyword_matches, "the evidence is retained"


def test_a_rejected_case_disappears_from_the_public_api(
    client: FlaskClient, queued: Session
) -> None:
    review_id = (
        queued.scalars(select(CaseReview).where(CaseReview.status == ReviewStatus.PENDING)).one().id
    )
    assert client.get("/api/cases/NL/ECLI:NL:RBTEST:2026:1").status_code == 200

    client.post(
        f"/api/reviews/{review_id}/decision",
        headers=auth(),
        json={"decision": "rejected", "decided_by": "content-manager"},
    )

    assert client.get("/api/cases/NL/ECLI:NL:RBTEST:2026:1").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"decision": "maybe", "decided_by": "agent"},
        {"decision": "confirmed"},
        {"decision": "confirmed", "decided_by": "   "},
        {"decision": "confirmed", "decided_by": "agent", "note": 7},
        {"decision": "confirmed", "decided_by": "agent\x00"},
    ],
)
def test_a_malformed_decision_is_rejected(
    client: FlaskClient, queued: Session, body: dict[str, Any]
) -> None:
    review_id = (
        queued.scalars(select(CaseReview).where(CaseReview.status == ReviewStatus.PENDING)).one().id
    )

    response = client.post(f"/api/reviews/{review_id}/decision", headers=auth(), json=body)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_deciding_an_unknown_item_is_a_404(client: FlaskClient, queued: Session) -> None:
    del queued

    response = client.post(
        "/api/reviews/9999/decision",
        headers=auth(),
        json={"decision": "confirmed", "decided_by": "agent"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"
