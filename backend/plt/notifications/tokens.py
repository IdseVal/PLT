"""Unguessable, purpose-bound tokens for the mailing list.

A subscriber has no account and never logs in, so a link in an email is the *only* thing
that authorises confirming or cancelling a subscription. Three properties follow, and each
is a decision rather than an implementation detail.

**Selector plus verifier.** A token reads ``<seed>.<verifier>``. The seed is a random,
unique value stored on ``subscriber.token_seed``; the verifier is
``HMAC-SHA256(secret, "plt.subscription.v1:<purpose>:<seed>")``, base64url without padding,
and is never stored. A database dump therefore contains no working link — the row alone
cannot produce one without the server key — while a link keeps working across every digest
without a recoverable secret being written down anywhere.

**Purpose binding.** The purpose is inside the HMAC input, so a confirmation link cannot be
replayed as an unsubscribe link, or the reverse. Two links to the same address are two
different, unrelated strings.

**No crypto is invented here.** The primitives are :func:`secrets.token_urlsafe` and
:func:`hmac.new` with :func:`hashlib.sha256` from the standard library, and the comparison
is :func:`hmac.compare_digest`, so a wrong verifier cannot be found byte by byte by timing
the response.

Expiry is deliberately *not* in the token. A confirmation link expires because
``subscriber.notice_sent_at`` is older than ``subscription_confirm_ttl_hours``, which means
the deadline is stored where it can be seen, changed and audited, and a token can be retired
by rotating the row's seed rather than by waiting for a claim inside it to lapse.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import re
import secrets
from typing import Final

__all__ = [
    "TokenPurpose",
    "issue_token",
    "new_seed",
    "verify_token",
]

#: Version tag inside the HMAC input. Changing the token format changes this, which retires
#: every outstanding link rather than letting two formats be confused for one another.
_VERSION: Final[str] = "plt.subscription.v1"

#: Bytes of entropy in a seed. 16 bytes is 128 bits, which is not enumerable.
_SEED_BYTES: Final[int] = 16

#: Separator between the selector and the verifier. Not a character base64url produces, so
#: the split is unambiguous.
_SEPARATOR: Final[str] = "."

#: A whole token: two base64url segments separated by a dot. Checked before anything is
#: derived from a token, so a hostile value never reaches the HMAC or a database lookup.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{8,64}\.[A-Za-z0-9_-]{43}$")

#: Longest token this module will look at. A token is a fixed shape; anything longer is a
#: probe, and is rejected without work.
_MAX_TOKEN_LENGTH: Final[int] = 128


class TokenPurpose(enum.StrEnum):
    """What a token authorises.

    Bound into the HMAC input, so a token issued for one purpose verifies for that purpose
    only.
    """

    CONFIRM = "confirm"
    UNSUBSCRIBE = "unsubscribe"


def new_seed() -> str:
    """Return a fresh, unguessable selector for a subscriber row.

    Returns:
        A URL-safe random string with 128 bits of entropy.
    """
    return secrets.token_urlsafe(_SEED_BYTES)


def _verifier(purpose: TokenPurpose, seed: str, secret: bytes) -> str:
    """Derive the verifier half of a token.

    Args:
        purpose: What the token authorises.
        seed: The subscriber's stored selector.
        secret: The server key, from :attr:`plt.config.Settings.token_secret`.

    Returns:
        The HMAC-SHA256 digest, base64url-encoded without padding.
    """
    message = f"{_VERSION}:{purpose.value}:{seed}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(purpose: TokenPurpose, seed: str, secret: bytes) -> str:
    """Build the token that goes into a link.

    Args:
        purpose: What the token authorises.
        seed: The subscriber's stored selector.
        secret: The server key.

    Returns:
        The token, as ``<seed>.<verifier>``.
    """
    return f"{seed}{_SEPARATOR}{_verifier(purpose, seed, secret)}"


def verify_token(purpose: TokenPurpose, token: str, secret: bytes) -> str | None:
    """Check a token and return the seed it authorises.

    The whole check happens before the database is touched: a caller cannot use the lookup to
    learn anything, because a token that does not verify never reaches one.

    Args:
        purpose: The purpose the caller is exercising.
        token: The token as it arrived from the client.
        secret: The server key.

    Returns:
        The seed, or ``None`` when the token is malformed or does not verify. The two are not
        distinguished: a caller learns only that this string is not a valid token.
    """
    if not token or len(token) > _MAX_TOKEN_LENGTH or not _TOKEN_RE.match(token):
        return None
    seed, _, verifier = token.partition(_SEPARATOR)
    if not hmac.compare_digest(verifier, _verifier(purpose, seed, secret)):
        return None
    return seed
