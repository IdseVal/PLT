"""``/api/stats/jurisdictions``, ``/api/filters`` and ``/api/health``.

The map payload carries the strongest requirements of the three: every jurisdiction in one
aggregate query, the EU included, and jurisdictions with no cases retained so the map can
render them muted instead of dropping the shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import pytest
from flask.testing import FlaskClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from plt.db.models import IngestRun, IngestStatus, Jurisdiction, JurisdictionType


@pytest.fixture
def statements() -> Iterator[list[str]]:
    """Record every SQL statement executed while the fixture is active."""
    recorded: list[str] = []

    def record(
        conn: Any,  # noqa: ANN401 - SQLAlchemy hands the listener untyped DBAPI objects
        cursor: Any,  # noqa: ANN401
        statement: str,
        parameters: Any,  # noqa: ANN401
        context: Any,  # noqa: ANN401
        executemany: bool,
    ) -> None:
        recorded.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield recorded
    finally:
        event.remove(Engine, "before_cursor_execute", record)


class TestJurisdictionStats:
    """``GET /api/stats/jurisdictions`` - the map payload."""

    def test_returns_every_jurisdiction_with_its_count(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/stats/jurisdictions")
        payload = response.get_json()

        assert response.status_code == HTTPStatus.OK
        assert isinstance(payload, list)
        assert {entry["code"]: entry["case_count"] for entry in payload} == {"EU": 1, "NL": 3}

    def test_includes_the_eu_with_its_map_sentinel(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        payload = client.get("/api/stats/jurisdictions").get_json()
        union = next(entry for entry in payload if entry["code"] == "EU")

        assert union["type"] == "supranational"
        assert union["map_feature_id"] == "EU"
        assert union["name"] == "European Union"

    def test_a_state_resolves_against_its_iso_alpha2_feature(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        payload = client.get("/api/stats/jurisdictions").get_json()
        netherlands = next(entry for entry in payload if entry["code"] == "NL")

        assert netherlands["type"] == "state"
        assert netherlands["map_feature_id"] == "NL"
        assert netherlands["latest_decision_date"] == "2024-05-01"

    def test_keeps_a_jurisdiction_that_has_no_cases_yet(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        api_corpus.add(
            Jurisdiction(
                code="BE",
                name="Belgium",
                type=JurisdictionType.STATE,
                iso_alpha2="BE",
                map_feature_id="BE",
            )
        )
        api_corpus.commit()

        payload = client.get("/api/stats/jurisdictions").get_json()
        belgium = next(entry for entry in payload if entry["code"] == "BE")

        assert belgium["case_count"] == 0
        assert belgium["latest_decision_date"] is None

    def test_counts_only_published_cases(self, client: FlaskClient, api_corpus: Session) -> None:
        payload = client.get("/api/stats/jurisdictions").get_json()
        netherlands = next(entry for entry in payload if entry["code"] == "NL")

        # Four Dutch rows exist; one is unpublished, and it is also the most recent.
        assert netherlands["case_count"] == 3
        assert netherlands["latest_decision_date"] == "2024-05-01"

    def test_answers_in_a_single_aggregate_query(
        self, client: FlaskClient, api_corpus: Session, statements: list[str]
    ) -> None:
        statements.clear()

        client.get("/api/stats/jurisdictions")

        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1
        assert "count" in selects[0].lower()


class TestPayloadContract:
    """The aggregate wire shapes pinned in architecture section 5.1."""

    def test_a_map_entry(self, client: FlaskClient, api_corpus: Session) -> None:
        payload = client.get("/api/stats/jurisdictions").get_json()

        assert set(payload[0]) == {
            "code",
            "name",
            "type",
            "map_feature_id",
            "is_active",
            "case_count",
            "latest_decision_date",
        }

    def test_the_facet_payload(self, client: FlaskClient, api_corpus: Session) -> None:
        payload = client.get("/api/filters").get_json()

        assert set(payload) == {
            "jurisdictions",
            "courts",
            "law_domains",
            "law_subfields",
            "languages",
            "topics",
            "keywords",
            "categories",
            "decision_date_range",
            "sorts",
            "export_formats",
            "page_size_default",
            "page_size_max",
            "latest_limit_max",
        }
        assert set(payload["decision_date_range"]) == {"from", "to"}

    def test_the_health_payload(self, client: FlaskClient, api_corpus: Session) -> None:
        payload = client.get("/api/health").get_json()

        assert set(payload) == {"status", "service", "version", "database", "ingest"}


class TestFilters:
    """``GET /api/filters`` - the facet payload behind the All-cases filter UI."""

    def test_lists_only_values_that_occur_on_a_published_case(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        payload = client.get("/api/filters").get_json()

        assert {entry["code"] for entry in payload["jurisdictions"]} == {"EU", "NL"}
        assert {entry["name"] for entry in payload["courts"]} == {
            "Raad van State",
            "Rechtbank Den Haag",
        }
        assert set(payload["law_domains"]) == {"private", "public"}
        assert set(payload["languages"]) == {"en", "nl"}
        assert [entry["slug"] for entry in payload["topics"]] == ["spray-drift"]

    def test_reports_the_decision_date_range(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        payload = client.get("/api/filters").get_json()

        assert payload["decision_date_range"] == {"from": "2022-01-10", "to": "2024-05-01"}

    def test_reports_the_bounds_the_server_enforces(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        payload = client.get("/api/filters").get_json()

        assert payload["page_size_max"] == 100
        assert payload["latest_limit_max"] == 50
        assert payload["sorts"] == ["date_desc", "date_asc", "relevance"]


class TestHealth:
    """``GET /api/health`` - liveness plus the last successful ingest per jurisdiction."""

    def test_reports_the_last_successful_ingest_per_jurisdiction(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        finished = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
        api_corpus.add_all(
            [
                IngestRun(
                    jurisdiction_code="NL",
                    connector="rechtspraak",
                    started_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
                    finished_at=finished,
                    status=IngestStatus.SUCCESS,
                ),
                IngestRun(
                    jurisdiction_code="NL",
                    connector="rechtspraak",
                    started_at=datetime(2026, 7, 8, 9, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 8, 9, 30, tzinfo=UTC),
                    status=IngestStatus.FAILED,
                ),
            ]
        )
        api_corpus.commit()

        payload = client.get("/api/health").get_json()

        assert payload["status"] == "ok"
        assert payload["database"] == "ok"
        # The later run failed, so the last *successful* one is still the one reported.
        assert payload["ingest"] == {"NL": finished.isoformat()}

    def test_is_empty_before_the_first_run(self, client: FlaskClient, api_corpus: Session) -> None:
        payload = client.get("/api/health").get_json()

        assert payload["ingest"] == {}

    def test_answers_even_when_the_database_is_unreachable(self, client: FlaskClient) -> None:
        # No schema has been created on this database, so every query fails.
        response = client.get("/api/health")
        payload = response.get_json()

        assert response.status_code == HTTPStatus.OK
        assert payload["status"] == "ok"
        assert payload["database"] == "unavailable"
        assert payload["ingest"] == {}


def test_the_keyword_roster_never_publishes_a_pattern(
    client: FlaskClient, api_corpus: Session
) -> None:
    """The dropdown is a list of names, and a regex term's term is not one."""
    del api_corpus
    payload = client.get("/api/filters").get_json()

    assert payload["keywords"], "the roster comes from the curated lists, not from the cases"
    for option in payload["keywords"]:
        assert "(?" not in option["term"], f"pattern published as a name: {option['term']!r}"
        assert "\\" not in option["term"], f"escape published as a name: {option['term']!r}"


class TestExclusions:
    """``/api/exclusions``: what each jurisdiction's criterion deliberately keeps out."""

    def test_the_three_mechanisms_are_reported_separately(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        """They are different claims, and merging them would tell a reader less."""
        del api_corpus
        payload = client.get("/api/exclusions").get_json()

        assert payload["jurisdictions"]
        for entry in payload["jurisdictions"]:
            assert set(entry) == {
                "code",
                "name",
                "list_version",
                "excluded_terms",
                "gated_terms",
                "exclusion_patterns",
            }

    def test_a_rejected_term_carries_the_reason_it_was_rejected(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        """An exclusion nobody can argue with is an exclusion nobody can check."""
        del api_corpus
        payload = client.get("/api/exclusions").get_json()
        dutch = next(e for e in payload["jurisdictions"] if e["code"] == "NL")

        assert dutch["excluded_terms"], "the Dutch list has rejected terms on record"
        for term in dutch["excluded_terms"]:
            assert term["term"]
            assert term["reason"], f"{term['term']} was removed without a recorded reason"

    def test_a_gated_term_names_the_gate_that_opens_it(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        """A gate that is flagged but not named leaves a reader asking against what."""
        del api_corpus
        payload = client.get("/api/exclusions").get_json()
        dutch = next(e for e in payload["jurisdictions"] if e["code"] == "NL")

        assert dutch["gated_terms"]
        for term in dutch["gated_terms"]:
            assert term["requires"], f"{term['term']} is gated on nothing"

    def test_nothing_published_here_is_a_pattern_dressed_as_a_name(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        """The terms are labels; only ``exclusion_patterns`` may read as machine syntax."""
        del api_corpus
        payload = client.get("/api/exclusions").get_json()

        for entry in payload["jurisdictions"]:
            for term in [*entry["excluded_terms"], *entry["gated_terms"]]:
                assert "(?" not in term["term"], f"pattern published as a name: {term['term']!r}"
