"""Outbound notifications: the mailing list, the weekly digest and the admin review notice.

Two audiences, deliberately kept apart.

**Readers** subscribe to the digest from the front page. Their addresses are personal data,
so the whole flow is built around that: double opt-in before anything is sent
(:mod:`plt.notifications.subscriptions`), an unguessable, purpose-bound token in every link
(:mod:`plt.notifications.tokens`), an unsubscribe that needs no account and is honoured
immediately, and the minimum stored — ``subscriber`` holds an address, a state, timestamps
and a token seed, and nothing else.

**The administrator** is told when the review queue gains items
(:mod:`plt.notifications.reviews`). That is operational mail to one configured address; the
flags themselves stay off every public endpoint, exactly as core document section 2.7
requires.

Everything leaves through :mod:`plt.notifications.mailer`, whose default backend writes to
the log rather than to a mail server, so sending real mail is always a deliberate change of
configuration and never something a checkout does by itself.
"""

from __future__ import annotations

__all__: list[str] = []
