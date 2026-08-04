"""The case endpoints of architecture section 5, exercised over HTTP.

Every assertion is against the committed corpus in ``tests/conftest.py``: four published
cases and one unpublished one, so a leak of editorial content is visible as an extra row.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from plt.db.models import Court

#: Published cases, newest decision first. The unpublished draft is the newest row in the
#: table, so its absence here is the test that editorial content stays hidden.
PUBLISHED_NEWEST_FIRST = [
    "ECLI:NL:RVS:2024:1",
    "ECLI:NL:RVS:2024:5",
    "ECLI:NL:RBDHA:2023:2",
    "62019CJ0616",
]


def get_json(client: FlaskClient, path: str, **query: str | int) -> dict[str, Any]:
    """Perform a GET expected to succeed and return its JSON body."""
    response = client.get(path, query_string=query)
    assert response.status_code == HTTPStatus.OK, response.get_data(as_text=True)
    body = response.get_json()
    assert isinstance(body, dict)
    return body


def source_ids(body: dict[str, Any]) -> list[str]:
    """Return the source identifiers of a list response, in response order."""
    return [item["source_id"] for item in body["items"]]


def court_id(session: Session, name: str) -> int:
    """Return the primary key of a court by name."""
    return session.scalars(select(Court).where(Court.name == name)).one().id


class TestListCases:
    """``GET /api/cases``."""

    def test_returns_published_cases_newest_first(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases")

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST
        assert body["total"] == len(PUBLISHED_NEWEST_FIRST)
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["has_next"] is False

    def test_result_carries_the_fields_a_result_card_renders(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases", q="glyphosate")
        item = body["items"][0]

        assert item["jurisdiction_code"] == "NL"
        assert item["jurisdiction_name"] == "Netherlands"
        assert item["court_name"] == "Raad van State"
        assert item["decision_date"] == "2024-05-01"
        assert item["law_domain"] == "public"
        assert item["case_numbers"] == ["202301234/1/A3"]

    def test_sort_date_asc_reverses_the_order(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases", sort="date_asc")

        assert source_ids(body) == list(reversed(PUBLISHED_NEWEST_FIRST))

    def test_sort_relevance_ranks_a_title_hit_above_a_full_text_hit(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        # "residue" is in the title of the 2023 case and in the full text of the 2024 one,
        # so the two orderings disagree and the ranking is doing the work.
        by_date = get_json(client, "/api/cases", q="residue")
        by_relevance = get_json(client, "/api/cases", q="residue", sort="relevance")

        assert source_ids(by_date) == ["ECLI:NL:RVS:2024:1", "ECLI:NL:RBDHA:2023:2"]
        assert source_ids(by_relevance) == ["ECLI:NL:RBDHA:2023:2", "ECLI:NL:RVS:2024:1"]

    @pytest.mark.parametrize("query", [{}, {"q": ""}, {"q": "   "}])
    def test_sort_relevance_without_a_query_falls_back_to_date_desc(
        self, client: FlaskClient, api_corpus: Session, query: dict[str, str]
    ) -> None:
        # A UI that keeps sort=relevance in the URL while the user clears the search box
        # must get results, not a 400: there is nothing to rank, so the default order
        # applies (architecture section 5.1).
        body = get_json(client, "/api/cases", sort="relevance", **query)

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST

    def test_empty_result_set_is_an_empty_page_not_an_error(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases", q="asbestos")

        assert body["items"] == []
        assert body["total"] == 0
        # page_count is never 0: an empty result set is one empty page, so a paginator
        # renders "1 of 1" rather than "1 of 0" (architecture section 5.1).
        assert body["page_count"] == 1
        assert body["has_next"] is False

    def test_unknown_but_well_formed_jurisdiction_matches_nothing(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases", jurisdiction="ZZ")

        assert body["total"] == 0


class TestPagination:
    """Page boundaries of ``GET /api/cases``."""

    def test_first_page_reports_a_next_page(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", page_size=2)

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST[:2]
        assert body["total"] == 4
        assert body["page_count"] == 2
        assert body["has_next"] is True

    def test_last_page_reports_no_next_page(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", page_size=2, page=2)

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST[2:]
        assert body["has_next"] is False

    def test_page_beyond_the_last_is_empty_but_reports_the_total(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        # 200 rather than 404: the corpus grows and shrinks under a paging client as
        # ingestion runs, and a page that briefly overshoots is not a broken URL
        # (architecture section 5.1).
        body = get_json(client, "/api/cases", page_size=2, page=99)

        assert body["items"] == []
        assert body["total"] == 4
        assert body["page"] == 99
        assert body["page_count"] == 2
        assert body["has_next"] is False

    def test_page_size_of_one_walks_the_whole_corpus(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        walked = [
            source_ids(get_json(client, "/api/cases", page_size=1, page=page))[0]
            for page in range(1, 5)
        ]

        assert walked == PUBLISHED_NEWEST_FIRST


class TestFilters:
    """Each filter of architecture section 5, one test each."""

    def test_jurisdiction_selects_one(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", jurisdiction="EU")

        assert source_ids(body) == ["62019CJ0616"]

    def test_jurisdiction_is_repeatable(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases?jurisdiction=EU&jurisdiction=NL")
        body = response.get_json()

        assert body["total"] == 4

    def test_jurisdiction_accepts_a_comma_separated_list(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases", jurisdiction="eu,nl")

        assert body["total"] == 4

    def test_law_domain(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", law_domain="private")

        assert source_ids(body) == ["ECLI:NL:RBDHA:2023:2"]

    def test_law_subfield(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", law_subfield="environmental")

        assert source_ids(body) == ["ECLI:NL:RVS:2024:1"]

    def test_topic(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", topic="spray-drift")

        assert source_ids(body) == ["ECLI:NL:RVS:2024:1"]

    def test_court(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", court=court_id(api_corpus, "Rechtbank Den Haag"))

        assert source_ids(body) == ["ECLI:NL:RBDHA:2023:2"]

    def test_language(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", language="EN")

        assert source_ids(body) == ["62019CJ0616"]

    def test_date_range(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", date_from="2023-01-01", date_to="2024-03-01")

        assert source_ids(body) == ["ECLI:NL:RVS:2024:5", "ECLI:NL:RBDHA:2023:2"]

    def test_filters_combine(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases", jurisdiction="NL", law_domain="public", q="licence")

        assert source_ids(body) == ["ECLI:NL:RVS:2024:1"]

    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("glyphosate", "ECLI:NL:RVS:2024:1"),
            ("catchment", "ECLI:NL:RBDHA:2023:2"),
            ("orchards", "ECLI:NL:RVS:2024:1"),
            ("62019CJ0616", "62019CJ0616"),
        ],
        ids=["title", "abstract", "full-text", "source-id"],
    )
    def test_q_searches_title_abstract_full_text_and_identifier(
        self, client: FlaskClient, api_corpus: Session, term: str, expected: str
    ) -> None:
        body = get_json(client, "/api/cases", q=term)

        assert source_ids(body) == [expected]


class TestSqlMetacharactersInQ:
    r"""A search term is data, never syntax.

    One case in the corpus contains ``%``, ``_`` and ``\\``. Each of them must match itself
    and nothing else: a wildcard that leaked through would return the whole corpus, and a
    metacharacter reaching the driver as syntax would return an error.
    """

    @pytest.mark.parametrize(
        "term",
        ["100%", "%", "_", "pure_extract", "\\", r"C:\field", "'", '"', ";", "--", "' OR 1=1 --"],
    )
    def test_metacharacters_never_error(
        self, client: FlaskClient, api_corpus: Session, term: str
    ) -> None:
        response = client.get("/api/cases", query_string={"q": term})

        assert response.status_code == HTTPStatus.OK

    @pytest.mark.parametrize("term", ["100%", "%", "_", "pure_extract", "\\", r"C:\field"])
    def test_a_wildcard_matches_only_the_case_containing_it(
        self, client: FlaskClient, api_corpus: Session, term: str
    ) -> None:
        body = get_json(client, "/api/cases", q=term)

        assert source_ids(body) == ["ECLI:NL:RVS:2024:5"]

    @pytest.mark.parametrize("term", ["%%", "%_%", "' OR 1=1 --", "; DROP TABLE case; --"])
    def test_an_injection_attempt_matches_nothing_and_leaves_the_corpus_intact(
        self, client: FlaskClient, api_corpus: Session, term: str
    ) -> None:
        body = get_json(client, "/api/cases", q=term)

        assert body["total"] == 0
        assert get_json(client, "/api/cases")["total"] == 4


class TestLatest:
    """``GET /api/cases/latest``."""

    def test_defaults_to_the_configured_limit(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases/latest")

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST
        assert body["limit"] == 20

    def test_limit_truncates_the_feed(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases/latest", limit=2)

        assert source_ids(body) == PUBLISHED_NEWEST_FIRST[:2]


class TestPayloadContract:
    """The exact wire shapes pinned in architecture section 5.1.

    Three frontend streams build against this JSON independently, so the field names are a
    contract rather than an implementation detail: a rename here breaks a page elsewhere,
    and these assertions are what turns that into a failing test instead of a blank card.
    """

    def test_the_paginated_envelope(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases")

        assert set(body) == {"items", "page", "page_size", "total", "page_count", "has_next"}

    def test_the_latest_feed_is_an_object_not_a_bare_array(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases/latest")

        assert set(body) == {"items", "limit"}

    def test_a_case_summary(self, client: FlaskClient, api_corpus: Session) -> None:
        item = get_json(client, "/api/cases")["items"][0]

        assert set(item) == {
            "id",
            "jurisdiction_code",
            "jurisdiction_name",
            "source_id",
            "source_system",
            "court_id",
            "court_name",
            "title",
            "abstract",
            "decision_date",
            "publication_date",
            "case_numbers",
            "language",
            "law_domain",
            "law_subfield",
            "procedure_type",
            "outcome",
            "source_url",
        }

    def test_the_summary_field_names_match_the_export_columns(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        item = get_json(client, "/api/cases")["items"][0]

        assert {"jurisdiction_code", "jurisdiction_name", "court_name"} <= set(item)
        assert "jurisdiction" not in item

    def test_the_feed_and_the_search_results_share_one_shape(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        searched = get_json(client, "/api/cases")["items"][0]
        fed = get_json(client, "/api/cases/latest")["items"][0]

        assert fed == searched

    def test_an_absent_value_is_null_rather_than_missing(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        item = next(
            entry
            for entry in get_json(client, "/api/cases")["items"]
            if entry["source_id"] == "62019CJ0616"
        )

        assert item["court_id"] is None
        assert item["court_name"] is None
        assert item["law_subfield"] is None

    def test_a_case_detail(self, client: FlaskClient, api_corpus: Session) -> None:
        body = get_json(client, "/api/cases/NL/ECLI:NL:RVS:2024:1")
        summary = get_json(client, "/api/cases", q="glyphosate")["items"][0]

        assert set(body) - set(summary) == {
            "filing_date",
            "revision",
            "first_seen_at",
            "last_seen_at",
            "updated_at",
            "documents",
            "parties",
            "topics",
            "keyword_matches",
            "citations",
        }
        assert set(summary) - set(body) == set()

    @pytest.mark.parametrize(
        ("member", "fields"),
        [
            (
                "documents",
                {
                    "id",
                    "language",
                    "doc_type",
                    "format",
                    "full_text",
                    "source_url",
                    "byte_size",
                    "retrieved_at",
                },
            ),
            ("parties", {"id", "name", "role", "party_type", "ordinal"}),
            ("topics", {"slug", "label", "confidence", "assigned_by"}),
            (
                "keyword_matches",
                {
                    "term_id",
                    "term",
                    "list_version",
                    "field",
                    "weight_applied",
                    "match_count",
                    "snippet",
                },
            ),
            (
                "citations",
                {
                    "target_identifier",
                    "target_scheme",
                    "citation_type",
                    "target_title",
                    "target_url",
                },
            ),
        ],
    )
    def test_a_detail_list_member(
        self, client: FlaskClient, api_corpus: Session, member: str, fields: set[str]
    ) -> None:
        body = get_json(client, "/api/cases/NL/ECLI:NL:RVS:2024:1")

        assert set(body[member][0]) == fields

    def test_the_editorial_switch_is_not_part_of_the_wire_format(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases/NL/ECLI:NL:RVS:2024:1")

        assert "is_published" not in body


class TestCaseDetail:
    """``GET /api/cases/<jurisdiction>/<source_id>``."""

    def test_returns_the_case_with_its_related_records(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases/NL/ECLI:NL:RVS:2024:1")

        assert body["source_id"] == "ECLI:NL:RVS:2024:1"
        assert body["parties"][0]["name"] == "Stichting Milieu"
        assert body["topics"][0]["slug"] == "spray-drift"
        assert body["keyword_matches"][0]["term"] == "glyfosaat"
        assert body["citations"][0]["target_identifier"] == "32009R1107"
        assert body["documents"][0]["full_text"].startswith("The authorisation")

    def test_never_exposes_the_raw_source_payload(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases/NL/ECLI:NL:RVS:2024:1")

        assert "raw_payload" not in response.get_data(as_text=True)
        assert "secret payload" not in response.get_data(as_text=True)

    def test_jurisdiction_is_case_insensitive(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        body = get_json(client, "/api/cases/nl/ECLI:NL:RVS:2024:1")

        assert body["source_id"] == "ECLI:NL:RVS:2024:1"

    def test_unknown_case_is_a_404_envelope(self, client: FlaskClient, api_corpus: Session) -> None:
        response = client.get("/api/cases/NL/ECLI:NL:RVS:1999:1")
        body = response.get_json()

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert body["error"]["code"] == "not_found"
        assert set(body["error"]) == {"code", "message", "details"}

    def test_unpublished_case_is_reported_as_missing(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases/NL/ECLI:NL:RVS:2025:9")

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_case_of_another_jurisdiction_is_not_reachable(
        self, client: FlaskClient, api_corpus: Session
    ) -> None:
        response = client.get("/api/cases/EU/ECLI:NL:RVS:2024:1")

        assert response.status_code == HTTPStatus.NOT_FOUND
