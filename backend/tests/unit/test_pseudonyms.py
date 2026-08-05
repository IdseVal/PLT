"""The keyed digest that replaces a withdrawn address, and the normalisation under it.

The properties here are the ones that decide whether core document section 2.12 delivers what
it claims. A digest that is not keyed is a lookup value, not a pseudonym. A digest computed
over a differently normalised address silently stops recognising anybody — silently, because
an unrecognised returning address looks exactly like a first-time subscriber.
"""

from __future__ import annotations

import hashlib

import pytest

from plt.api.schemas import parse_subscription_request
from plt.config import AppEnv
from plt.notifications.pseudonyms import address_digest, matches_digest, normalise_address
from tests.conftest import build_settings

PEPPER = b"a-test-pepper-value"


class TestNormalisation:
    """One canonical form, used for what is stored and for what is hashed."""

    @pytest.mark.parametrize(
        "raw",
        [
            "reader@example.org",
            "Reader@Example.ORG",
            "READER@EXAMPLE.ORG",
            "  reader@example.org  ",
        ],
    )
    def test_case_and_surrounding_space_do_not_change_the_address(self, raw: str) -> None:
        assert normalise_address(raw) == "reader@example.org"

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            # Dots in the local part are significant at most hosts, and folding them would
            # suppress an address that never unsubscribed.
            ("a.reader@example.org", "areader@example.org"),
            # So is a plus-tag, which is a convention of particular providers and not of email.
            ("reader+alerts@example.org", "reader@example.org"),
            ("reader@example.org", "reader@example.com"),
        ],
    )
    def test_distinct_addresses_stay_distinct(self, first: str, second: str) -> None:
        """Over-broad normalisation is the dangerous direction: it suppresses the wrong person."""
        assert address_digest(first, PEPPER) != address_digest(second, PEPPER)

    def test_the_stored_address_and_the_hashed_address_are_normalised_the_same_way(self) -> None:
        """The API layer must not have its own idea of what an address looks like.

        If these two ever diverge, an address stored one way and digested another stops being
        recognised when it comes back, and nothing fails loudly.
        """
        stored = parse_subscription_request({"email": "  Reader@Example.ORG "})

        assert stored == normalise_address("  Reader@Example.ORG ")


class TestDigest:
    """Keyed, deterministic, and useless on its own."""

    def test_the_same_address_always_gives_the_same_digest(self) -> None:
        assert address_digest("reader@example.org", PEPPER) == address_digest(
            "reader@example.org", PEPPER
        )

    def test_a_different_pepper_gives_a_different_digest(self) -> None:
        """What makes a database dump alone worthless.

        Without this the digest is a plain hash of an enumerable value, and any candidate
        list recovers the address.
        """
        assert address_digest("reader@example.org", PEPPER) != address_digest(
            "reader@example.org", b"another-pepper"
        )

    def test_the_digest_is_not_a_bare_hash_of_the_address(self) -> None:
        """The specific failure the decision's caveat is about."""
        bare = hashlib.sha256(b"reader@example.org").hexdigest()

        assert address_digest("reader@example.org", PEPPER) != bare

    def test_the_digest_fits_the_column(self) -> None:
        assert len(address_digest("reader@example.org", PEPPER)) == 64

    def test_an_empty_pepper_is_refused(self) -> None:
        """Rather than producing an unkeyed digest that looks like it worked."""
        with pytest.raises(ValueError, match="pepper"):
            address_digest("reader@example.org", b"")

    def test_matching_compares_the_whole_value(self) -> None:
        digest = address_digest("reader@example.org", PEPPER)

        assert matches_digest(digest, digest)
        assert not matches_digest(digest, digest.upper())
        flipped = "0" if digest[-1] != "0" else "1"
        assert not matches_digest(digest, digest[:-1] + flipped)


class TestPepperConfiguration:
    """The pepper lives outside the database, and outside a routine credential rotation."""

    def test_production_refuses_to_start_without_an_explicit_pepper(self) -> None:
        """Falling back to secret_key in production would tie every suppression to it.

        Rotating ``secret_key`` is routine; rotating the pepper makes every withdrawn address
        unrecognisable and cannot be undone.
        """
        with pytest.raises(ValueError, match="PLT_SUBSCRIPTION_ADDRESS_PEPPER"):
            build_settings(
                app_env=AppEnv.PRODUCTION,
                secret_key="a-generated-production-secret",
                mail_backend="smtp",
                smtp_host="smtp.example.org",
            )

    def test_the_pepper_is_kept_apart_from_the_token_secret(self) -> None:
        settings = build_settings(
            subscription_address_pepper="a-pepper", subscription_token_secret="a-token-secret"
        )

        assert settings.address_pepper == b"a-pepper"
        assert settings.token_secret == b"a-token-secret"

    def test_the_pepper_is_not_in_the_settings_repr(self) -> None:
        """It is a secret, and a settings dump reaches logs and error reports."""
        settings = build_settings(subscription_address_pepper="the-actual-pepper")

        assert "the-actual-pepper" not in repr(settings)
