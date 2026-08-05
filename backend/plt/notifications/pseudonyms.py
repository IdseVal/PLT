"""Turning a subscriber's address into a keyed digest, per core document section 2.12.

When somebody unsubscribes, the purpose they consented to has ended, so the address is
replaced by ``HMAC-SHA256(pepper, normalised_address)`` and the row keeps everything else.
The project keeps its records and its statistics; it stops holding the address.

**This is pseudonymisation, not anonymisation, and nothing here may call it otherwise.** The
digest is deterministic, which is the whole point — a returning address has to be
recognisable — and determinism is exactly what makes the value reversible to anyone holding
the pepper and a list of candidate addresses. Under the GDPR a pseudonymised record is still
personal data: a subject access request can still reach these rows, and the retention clock
does not stop. Core document section 2.12 states the trade plainly: suppression and full
anonymisation are mutually exclusive, and this project chose suppression.

**The digest is keyed, and the key is not in the database.** A bare ``sha256`` of an email
address is not a protection at all: the address space is enumerable, so anybody with a word
list hashes it once and matches every stored digest. The pepper comes from
:attr:`plt.config.Settings.address_pepper` — an environment variable or a deployment secret,
never a column, never a migration, never a fixture — so a database dump on its own yields no
addresses. That is the only property that distinguishes this from a lookup table.

**The pepper is long-lived by design.** Rotating it changes every digest this module would
produce, so every row already pseudonymised stops being recognised: an address that
unsubscribed under the old pepper would be treated as a stranger and could be re-subscribed
by anybody who typed it in. There is no re-keying path, because re-keying would require the
addresses the rows exist to no longer hold. Rotate it only as a deliberate decision to
discard every existing suppression.

**Normalisation happens before hashing, and only here.** A digest of ``Ann@Wur.nl`` and a
digest of ``ann@wur.nl`` are different values, so an address normalised one way on the way in
and another way on the way to the HMAC would silently stop being recognised — silently,
because the failure looks exactly like a first-time subscriber. :func:`normalise_address` is
therefore the single definition, and ``plt.api.schemas`` stores what it returns.

*What the normalisation does, and what it deliberately does not:*

* **Surrounding whitespace is stripped and the whole address is lower-cased.** Lower-casing
  the domain is uncontroversial — DNS is case-insensitive. Lower-casing the *local* part is
  not: RFC 5321 section 2.3.11 leaves its interpretation to the receiving host, so
  ``Ann@wur.nl`` and ``ann@wur.nl`` may in principle be two mailboxes. This project already
  made that call when the list was built: ``subscriber.email`` is unique over the lower-cased
  address, so the two capitalisations are one subscriber and one row. The digest must match
  the address as it is *stored*, or recognition breaks, so it applies the same rule rather
  than a stricter one of its own.
* **Provider-specific rules are not applied.** No dots are removed from the local part and no
  ``+tag`` suffix is cut off. Those are conventions of particular mail hosts, not of email:
  applying them everywhere would fold addresses that belong to different people at other
  hosts into one digest, and a digest that is too broad *suppresses the wrong person* — it
  would silently refuse a subscription from somebody who never unsubscribed. An over-narrow
  digest costs one recognition; an over-broad one denies somebody a service they asked for.
* **No Unicode normalisation.** ``plt.api.schemas`` accepts an ASCII address grammar only, so
  there is no non-ASCII input to fold and no case-mapping surprise (``İ``, ``ß``) to guard
  against. An internationalised-address grammar would have to revisit this function first.

No cryptography is invented here: :mod:`hmac` and :mod:`hashlib` from the standard library,
and :func:`hmac.compare_digest` for the one comparison that happens in Python.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

__all__ = [
    "address_digest",
    "matches_digest",
    "normalise_address",
]

#: Version tag inside the HMAC input, mirroring :mod:`plt.notifications.tokens`. Changing how
#: an address is normalised or digested changes this, so values produced by two different
#: rules can never be compared as though they meant the same thing.
_VERSION: Final[str] = "plt.subscriber.v1"


def normalise_address(email: str) -> str:
    """Return the one canonical form of an address, for storage and for hashing alike.

    The single definition of what "the same address" means in this project. See the module
    docstring for why it lower-cases the local part and why it applies no provider-specific
    rule.

    Args:
        email: An address, already validated by ``plt.api.schemas`` in every production
            path.

    Returns:
        The address with surrounding whitespace removed and every character lower-cased.
    """
    return email.strip().lower()


def address_digest(email: str, pepper: bytes) -> str:
    """Return the keyed digest that stands in for an address once it has been withdrawn.

    Args:
        email: The address. Normalised here, so a caller cannot get it wrong by passing a
            form that was normalised differently.
        pepper: The key, from :attr:`plt.config.Settings.address_pepper`. It lives outside
            the database, which is what stops a dump of the table from being reversed with a
            word list.

    Returns:
        The HMAC-SHA256 of the normalised address under the pepper, as 64 hex characters.

    Raises:
        ValueError: If the pepper is empty. An unkeyed digest of an email address is an
            enumerable lookup value rather than a pseudonym, so producing one is refused
            outright rather than left to be noticed later.
    """
    if not pepper:
        message = "the address pepper is empty; an unkeyed digest of an address is reversible"
        raise ValueError(message)
    payload = f"{_VERSION}:{normalise_address(email)}".encode()
    return hmac.new(pepper, payload, hashlib.sha256).hexdigest()


def matches_digest(candidate: str, stored: str) -> bool:
    """Return whether two digests are the same value.

    The database index is what *finds* a candidate row; this is what confirms it. The two are
    not the same check: string equality in SQL is decided by the column's collation, and a
    case-insensitive or accent-insensitive one — the default in some MySQL builds, and
    reachable in PostgreSQL through ``citext`` — would match two digests that differ, which
    here would mean suppressing an address that never unsubscribed. Comparing the bytes in
    Python removes that dependency, and :func:`hmac.compare_digest` keeps the comparison from
    varying with how much of the value matched.

    Args:
        candidate: The digest computed from the address in hand.
        stored: The digest read from the row.

    Returns:
        ``True`` when the two are byte-for-byte equal.
    """
    return hmac.compare_digest(candidate, stored)
