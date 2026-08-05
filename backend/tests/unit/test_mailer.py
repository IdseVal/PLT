"""The mail backends: what leaves the process, and what cannot.

Two things are being held here. First, that a development checkout cannot send real mail:
the default backend writes to the log and the file backend writes to a directory, and neither
opens a socket. Second, that a value a stranger supplied cannot become a header of its own —
the subscribe endpoint takes an address from an anonymous request, so header injection is the
attack the renderer has to refuse.
"""

from __future__ import annotations

import email
import email.policy
from pathlib import Path

import pytest

from plt.config import AppEnv, MailBackend, Settings
from plt.notifications.mailer import (
    ConsoleMailer,
    FileMailer,
    MailError,
    Message,
    SmtpMailer,
    build_mailer,
    render_message,
)
from tests.conftest import build_settings


@pytest.fixture
def mail_settings(tmp_path: Path) -> Settings:
    """Return settings whose file backend writes into a temporary outbox."""
    return build_settings(
        mail_backend=MailBackend.FILE,
        mail_outbox_dir=tmp_path / "outbox",
        mail_from="Pesticide Litigation Tracker <plt@example.org>",
    )


def test_the_default_backend_sends_nothing(tmp_path: Path) -> None:
    # The whole point of the default: `pip install`, run the flow, mail nobody.
    settings = build_settings(mail_outbox_dir=tmp_path / "outbox")

    assert settings.mail_backend is MailBackend.CONSOLE
    assert isinstance(build_mailer(settings), ConsoleMailer)


def test_the_backend_is_configuration(mail_settings: Settings) -> None:
    assert isinstance(build_mailer(mail_settings), FileMailer)
    assert isinstance(
        build_mailer(build_settings(mail_backend=MailBackend.SMTP, smtp_host="smtp.example.org")),
        SmtpMailer,
    )


def test_production_refuses_the_console_backend() -> None:
    # A confirmation written to the log in production is a subscription that never completes.
    with pytest.raises(ValueError, match="console"):
        build_settings(
            app_env=AppEnv.PRODUCTION,
            secret_key="a-generated-production-secret",
            mail_backend=MailBackend.CONSOLE,
        )


def test_the_smtp_backend_needs_a_host() -> None:
    with pytest.raises(ValueError, match="PLT_SMTP_HOST"):
        build_settings(mail_backend=MailBackend.SMTP)


def test_the_two_tls_modes_are_alternatives() -> None:
    with pytest.raises(ValueError, match="alternatives"):
        build_settings(
            mail_backend=MailBackend.SMTP,
            smtp_host="smtp.example.org",
            smtp_ssl=True,
            smtp_starttls=True,
        )


def test_a_rendered_message_is_plain_text_with_no_html_part(mail_settings: Settings) -> None:
    # No HTML part means no tracking pixel: the tracker cannot report who read a digest
    # because there is nothing in the message that would tell it.
    message = Message(to="r@example.org", subject="Hello", body="Body")
    rendered = render_message(message, mail_settings)

    assert rendered.get_content_type() == "text/plain"
    assert not rendered.is_multipart()
    assert rendered["To"] == "r@example.org"
    assert rendered["From"] == "Pesticide Litigation Tracker <plt@example.org>"
    assert rendered["Auto-Submitted"] == "auto-generated"
    assert rendered["Message-ID"]


@pytest.mark.parametrize(
    "recipient",
    [
        "victim@example.org\nBcc: everyone@example.org",
        "victim@example.org\rBcc: everyone@example.org",
        "victim@example.org\r\nSubject: Forged",
        "   ",
    ],
)
def test_a_header_that_could_split_is_refused(mail_settings: Settings, recipient: str) -> None:
    with pytest.raises(MailError):
        render_message(Message(to=recipient, subject="Hello", body="Body"), mail_settings)


def test_an_injected_extra_header_is_refused(mail_settings: Settings) -> None:
    message = Message(
        to="r@example.org",
        subject="Hello",
        body="Body",
        headers={"List-Unsubscribe": "<https://x.test/u>\nBcc: victim@example.org"},
    )

    with pytest.raises(MailError):
        render_message(message, mail_settings)


def test_the_file_backend_writes_one_readable_message(mail_settings: Settings) -> None:
    with FileMailer(mail_settings) as mailer:
        mailer.send(Message(to="reader@example.org", subject="Digest", body="One new case."))

    written = list(mail_settings.mail_outbox_dir.glob("*.eml"))
    assert len(written) == 1
    parsed = email.message_from_string(
        written[0].read_text(encoding="utf-8"), policy=email.policy.default
    )
    assert parsed["To"] == "reader@example.org"
    assert "One new case." in parsed.get_content()


def test_the_filename_carries_no_address_and_no_path(mail_settings: Settings) -> None:
    # Two properties at once: the path is logged, so it must not name a subscriber, and a
    # recipient is client input, so it must not be able to walk out of the outbox.
    with FileMailer(mail_settings) as mailer:
        mailer.send(Message(to="a/../../reader@example.org", subject="s", body="b"))

    written = list(mail_settings.mail_outbox_dir.glob("*.eml"))
    assert len(written) == 1
    assert written[0].parent == mail_settings.mail_outbox_dir
    assert "reader" not in written[0].name
    assert "@" not in written[0].name


def test_the_console_backend_touches_no_file(tmp_path: Path) -> None:
    settings = build_settings(mail_outbox_dir=tmp_path / "outbox")

    with ConsoleMailer(settings) as mailer:
        mailer.send(Message(to="reader@example.org", subject="Digest", body="Body"))

    assert not (tmp_path / "outbox").exists()
