"""Sending mail: three backends, one interface, no third-party service.

``docs/architecture.md`` rule 2.1 keeps every host, credential and address in
:class:`plt.config.Settings`; nothing about a mail server is written here. What *is* written
here is which backend a deployment gets, and the default is deliberate:

* ``console`` — renders the message to the log. **The default**, so a checkout, a test run
  and a developer exercising the subscription flow never send anything to a real address.
* ``file`` — writes an RFC 5322 ``.eml`` into ``mail_outbox_dir``. Same guarantee, plus a
  message you can open in a mail client and read exactly as a subscriber would.
* ``smtp`` — the real path, over the standard library's :mod:`smtplib`. There is no
  third-party email service and no SDK: the project sends a handful of plain-text messages a
  week from a university mail server, which is precisely what SMTP is, and adding a vendor
  would add a data processor to a system holding personal data for no capability in return.

**Header injection is refused, not escaped.** Every address and subject is checked for a
carriage return or line feed before a message is built. A subscriber-supplied address reaches
this module, so a value that could split a header into two is an error rather than something
to sanitise and hope about.

**Nothing is tracked.** Messages are ``text/plain``; there is no HTML part, so there is no
pixel, and no link is rewritten through a redirector. The tracker cannot report who opened a
digest because it never learns it.
"""

from __future__ import annotations

import hashlib
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Final, Protocol, Self

from plt.config import MailBackend, Settings
from plt.db.base import utcnow
from plt.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ConsoleMailer",
    "FileMailer",
    "MailError",
    "Mailer",
    "Message",
    "SmtpMailer",
    "build_mailer",
    "render_message",
]

log = get_logger(__name__)

#: Anything that would end a header line. An address or subject carrying one of these could
#: append headers of its own, so it is rejected outright.
_HEADER_BREAK: Final[re.Pattern[str]] = re.compile(r"[\r\n]")

#: Characters of a recipient digest that go into a file backend's filename. Eight hex
#: characters distinguish one recipient's messages from another's in a local outbox without
#: putting an address into a path — and therefore into the log line that names it.
_FILENAME_DIGEST_LENGTH: Final[int] = 8

#: Domain the ``Message-ID`` is generated under when the From address carries none.
_FALLBACK_DOMAIN: Final[str] = "plt.invalid"


class MailError(RuntimeError):
    """A message could not be built or delivered.

    Never carries the server's own error text verbatim to a client: the callers turn it into
    a generic response and log the detail.
    """


@dataclass(frozen=True, slots=True)
class Message:
    """One outbound message, before it becomes RFC 5322.

    Attributes:
        to: The single recipient. One message per recipient on purpose — a digest with
            several addresses in ``To`` would disclose the mailing list to everyone on it.
        subject: Subject line.
        body: Plain-text body. There is no HTML alternative.
        headers: Extra headers, e.g. ``List-Unsubscribe``.
    """

    to: str
    subject: str
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)


def _check_header(value: str, field_name: str) -> str:
    """Reject a header value that could break out of its header.

    Args:
        value: The value to check.
        field_name: Header name, for the error message.

    Returns:
        The value, stripped of surrounding whitespace.

    Raises:
        MailError: If the value is empty or contains a line break.
    """
    candidate = value.strip()
    if not candidate:
        message = f"{field_name} must not be empty"
        raise MailError(message)
    if _HEADER_BREAK.search(candidate):
        message = f"{field_name} contains a line break"
        raise MailError(message)
    return candidate


def render_message(
    message: Message,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> EmailMessage:
    """Build the RFC 5322 message a backend sends.

    Args:
        message: The message to render.
        settings: Settings supplying the From and Reply-To addresses.
        now: Timestamp for the ``Date`` header. Defaults to the current instant.

    Returns:
        The rendered message, ``text/plain; charset=utf-8``.

    Raises:
        MailError: If any header value carries a line break, or the recipient is empty.
    """
    rendered = EmailMessage()
    rendered["From"] = _check_header(settings.mail_from, "From")
    rendered["To"] = _check_header(message.to, "To")
    rendered["Subject"] = _check_header(message.subject, "Subject")
    rendered["Date"] = formatdate((now or utcnow()).timestamp(), localtime=False, usegmt=True)
    domain = settings.mail_from.rpartition("@")[2].strip(" >") or _FALLBACK_DOMAIN
    rendered["Message-ID"] = make_msgid(domain=domain)
    if settings.mail_reply_to:
        rendered["Reply-To"] = _check_header(settings.mail_reply_to, "Reply-To")
    # Everything the tracker sends is machine-generated. Saying so keeps it out of the
    # auto-responder loops that an unattended weekly job would otherwise join.
    rendered["Auto-Submitted"] = "auto-generated"
    for name, value in message.headers.items():
        rendered[_check_header(name, "header name")] = _check_header(value, name)
    rendered.set_content(message.body, subtype="plain", charset="utf-8")
    return rendered


class Mailer(Protocol):
    """What every backend offers the rest of the application."""

    def send(self, message: Message) -> None:
        """Deliver one message.

        Args:
            message: The message to deliver.

        Raises:
            MailError: If it could not be delivered.
        """

    def close(self) -> None:
        """Release whatever the backend holds. Safe to call twice."""


class _BaseMailer:
    """Shared plumbing: settings, rendering and the context-manager protocol."""

    def __init__(self, settings: Settings) -> None:
        """Bind the backend to its settings.

        Args:
            settings: Validated settings.
        """
        self._settings = settings

    def close(self) -> None:
        """Release resources. The local backends hold none."""

    def __enter__(self) -> Self:
        """Return the mailer, so a caller can use it in a ``with`` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the mailer, whatever ended the block."""
        del exc_type, exc, traceback
        self.close()


class ConsoleMailer(_BaseMailer):
    """Render each message to the log instead of sending it.

    The default backend, and the reason a development checkout cannot mail a real person. The
    body is logged in full — it is the only way to follow a confirmation link locally — which
    is why this backend is refused in production, where a body carries a live unsubscribe
    token and the log is not the place for one.
    """

    def send(self, message: Message) -> None:
        """Log the message.

        Args:
            message: The message that would have been sent.

        Raises:
            MailError: If the message could not be rendered.
        """
        rendered = render_message(message, self._settings)
        log.info(
            "mail not sent (console backend)\n%s",
            rendered.as_string(),
            extra={"context": {"backend": "console", "subject": message.subject}},
        )


class FileMailer(_BaseMailer):
    """Write each message to a ``.eml`` file under ``mail_outbox_dir``.

    Also a local backend: it opens no socket. It exists for the flows that are easier to check
    by reading the message a subscriber would receive than by reading a log line, and for a
    deployment whose relay collects files from a spool directory.

    The filename carries a timestamp and a digest of the recipient, never the recipient: the
    path is logged, and an address does not belong in a log. The message inside names its
    recipient, of course — it is a message to them.
    """

    def send(self, message: Message) -> None:
        """Write the message to the outbox.

        Args:
            message: The message to write.

        Raises:
            MailError: If the outbox is not writable.
        """
        rendered = render_message(message, self._settings)
        directory = self._settings.mail_outbox_dir
        stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
        # A digest of the recipient rather than the recipient: the filename ends up in the log
        # line below, and an address does not belong in a log (rule 2.7). It also settles the
        # path-traversal question outright — a hex digest has no separators to escape with.
        recipient = hashlib.sha256(message.to.encode("utf-8")).hexdigest()[:_FILENAME_DIGEST_LENGTH]
        path = Path(directory) / f"{stamp}-{recipient}.eml"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered.as_string(), encoding="utf-8")
        except OSError as error:
            detail = f"could not write {path.name} to the outbox: {error}"
            raise MailError(detail) from error
        log.info(
            "mail written to the outbox",
            extra={"context": {"backend": "file", "path": str(path)}},
        )


class SmtpMailer(_BaseMailer):
    """Send over SMTP, the only backend that reaches a mail server.

    The connection is opened on first use and held for the life of the mailer, so a digest to
    a hundred subscribers is one SMTP conversation rather than a hundred. A server that drops
    the connection between messages — which a submission service will do on an idle socket —
    costs one reconnection and one retry, not a failed run.

    TLS is not optional in either mode: ``smtp_ssl`` connects with implicit TLS and
    ``smtp_starttls`` upgrades in place, both against :func:`ssl.create_default_context`, so
    the certificate and hostname are verified.
    """

    def __init__(self, settings: Settings) -> None:
        """Bind the backend without connecting.

        Args:
            settings: Validated settings carrying the host, port and credentials.
        """
        super().__init__(settings)
        self._client: smtplib.SMTP | None = None

    def _connect(self) -> smtplib.SMTP:
        """Open and prepare a connection.

        Returns:
            A connected, authenticated client.

        Raises:
            MailError: If the server could not be reached, refused TLS or refused the login.
        """
        settings = self._settings
        host = (settings.smtp_host or "").strip()
        if not host:  # pragma: no cover - the settings validator refuses this combination
            message = "the SMTP backend needs PLT_SMTP_HOST"
            raise MailError(message)
        context = ssl.create_default_context()
        try:
            client: smtplib.SMTP = (
                smtplib.SMTP_SSL(
                    host, settings.smtp_port, timeout=settings.smtp_timeout_seconds, context=context
                )
                if settings.smtp_ssl
                else smtplib.SMTP(host, settings.smtp_port, timeout=settings.smtp_timeout_seconds)
            )
            if settings.smtp_starttls:
                client.starttls(context=context)
                client.ehlo()
            if settings.smtp_username and settings.smtp_password is not None:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        except (OSError, smtplib.SMTPException) as error:
            # The message names the host and the failure class, never the credentials.
            detail = f"could not open an SMTP session to {host}: {type(error).__name__}"
            raise MailError(detail) from error
        return client

    def send(self, message: Message) -> None:
        """Send one message, reconnecting once if the session had gone.

        Args:
            message: The message to send.

        Raises:
            MailError: If the message could not be delivered.
        """
        rendered = render_message(message, self._settings)
        last_error: Exception | None = None
        for attempt in (1, 2):
            if self._client is None:
                self._client = self._connect()
            try:
                self._client.send_message(rendered)
            except smtplib.SMTPServerDisconnected as error:
                last_error = error
                self.close()
                if attempt == 2:
                    break
                log.info("SMTP session had closed; reconnecting once")
                continue
            except smtplib.SMTPException as error:
                detail = f"the server refused the message: {type(error).__name__}"
                raise MailError(detail) from error
            else:
                return
        detail = f"the SMTP session could not be re-established: {type(last_error).__name__}"
        raise MailError(detail) from last_error

    def close(self) -> None:
        """Quit the SMTP session, if one is open. Safe to call twice."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):  # pragma: no cover - closing a dead socket
            log.debug("SMTP session could not be closed cleanly")


def build_mailer(settings: Settings) -> Mailer:
    """Return the backend the configuration asks for.

    Args:
        settings: Validated settings.

    Returns:
        A :class:`Mailer`. ``console`` unless a deployment deliberately configured otherwise,
        which is what keeps a development checkout incapable of mailing a real address.
    """
    match settings.mail_backend:
        case MailBackend.SMTP:
            return SmtpMailer(settings)
        case MailBackend.FILE:
            return FileMailer(settings)
        case _:
            return ConsoleMailer(settings)
