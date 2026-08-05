"""The confirmation and unsubscribe tokens.

A subscriber never logs in, so a token is the whole of the authorisation. These tests hold
the four properties that makes acceptable: it cannot be guessed, it cannot be forged without
the key, it cannot be used for a purpose it was not issued for, and the stored half of it is
not enough to reconstruct it.
"""

from __future__ import annotations

import pytest

from plt.notifications.tokens import TokenPurpose, issue_token, new_seed, verify_token

SECRET = b"a-test-token-secret"
OTHER_SECRET = b"a-different-token-secret"


def test_a_token_verifies_against_the_seed_it_was_issued_for() -> None:
    seed = new_seed()
    token = issue_token(TokenPurpose.CONFIRM, seed, SECRET)

    assert verify_token(TokenPurpose.CONFIRM, token, SECRET) == seed


def test_a_token_is_bound_to_its_purpose() -> None:
    # The defect this guards: a confirmation link that also cancels a subscription, or an
    # unsubscribe link that silently re-confirms one.
    seed = new_seed()
    confirm = issue_token(TokenPurpose.CONFIRM, seed, SECRET)
    cancel = issue_token(TokenPurpose.UNSUBSCRIBE, seed, SECRET)

    assert confirm != cancel
    assert verify_token(TokenPurpose.UNSUBSCRIBE, confirm, SECRET) is None
    assert verify_token(TokenPurpose.CONFIRM, cancel, SECRET) is None


def test_a_token_does_not_verify_under_a_different_key() -> None:
    token = issue_token(TokenPurpose.CONFIRM, new_seed(), SECRET)

    assert verify_token(TokenPurpose.CONFIRM, token, OTHER_SECRET) is None


def test_the_seed_alone_does_not_make_a_token() -> None:
    # What a database dump yields: the seed. Without the key it cannot be turned into a link,
    # which is why the verifier is derived and never stored.
    seed = new_seed()

    assert verify_token(TokenPurpose.CONFIRM, seed, SECRET) is None
    assert verify_token(TokenPurpose.CONFIRM, f"{seed}.", SECRET) is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        "no-separator",
        ".",
        "seed.",
        ".verifier",
        "seed.tooshort",
        "a" * 200,
        "seed.verifier with a space",
        "seed.verifier\nBcc: victim@example.org",
        "../../etc/passwd.aaaa",
    ],
)
def test_a_malformed_token_is_refused_without_a_lookup(token: str) -> None:
    assert verify_token(TokenPurpose.UNSUBSCRIBE, token, SECRET) is None


def test_one_character_of_the_verifier_is_enough_to_reject() -> None:
    seed = new_seed()
    token = issue_token(TokenPurpose.CONFIRM, seed, SECRET)
    head, _, verifier = token.partition(".")
    flipped = "A" if verifier[0] != "A" else "B"
    tampered = f"{head}.{flipped}{verifier[1:]}"

    assert verify_token(TokenPurpose.CONFIRM, tampered, SECRET) is None


def test_seeds_are_unique_and_unguessable() -> None:
    seeds = {new_seed() for _ in range(200)}

    assert len(seeds) == 200
    # 16 random bytes, URL-safe base64: 22 characters, no padding.
    assert all(len(seed) >= 22 for seed in seeds)
