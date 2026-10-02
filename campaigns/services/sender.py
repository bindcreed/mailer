"""
Background sending. Each SendingSession is walked by a single daemon
thread that sends one plain SMTP message per recipient — no CC, no
BCC, one RCPT TO per transaction. The To/From headers are always
wrapped in angle brackets (some receiving/spam-filtering servers treat
a bare, unwrapped address as a sign of a hand-rolled spam script).
"""
import logging
import smtplib
import threading
import time
from email.utils import formataddr

from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags

# The one error that means the SMTP *connection* died underneath us — as
# opposed to a bad recipient address or a rejected message — and is worth
# reconnecting and retrying immediately for. Many SMTP hosts (shared/
# low-cost ones especially, which is exactly what a self-hosted mail merge
# tool tends to be pointed at) silently close idle connections, or cap how
# many messages a single connection may send before it's dropped. Without
# this, one dropped connection partway through a run would otherwise fail
# every remaining recipient with "please run connect() first", even though
# nothing is actually wrong with those addresses.
#
# Deliberately just this one exception, not a broader OSError catch: every
# exception smtplib raises (SMTPRecipientsRefused, SMTPResponseException,
# SMTPDataError, etc.) is ALSO a subclass of OSError, so a wider tuple here
# would silently swallow real rejections too — including the 4xx "temporary,
# try again" ones _is_temporary_smtp_error()/the backoff-retry loop below
# are specifically meant to catch, retrying them instantly with no backoff
# instead of waiting first.
_CONNECTION_DROPPED_ERRORS = (smtplib.SMTPServerDisconnected,)

# A 4xx SMTP response is a *temporary* rejection — "try again shortly" —
# most commonly a host rate-limiting how fast one account can send (e.g.
# "452 Sender is throttled" on shared/budget hosting). A 5xx response is
# permanent (bad mailbox, blocked sender) and retrying it is pointless.
# When we hit a 4xx, back off for a while and retry the same recipient a
# couple of times before giving up, instead of marking it failed outright.
_MAX_TEMPORARY_RETRIES = 2
_TEMPORARY_RETRY_BACKOFF_SECONDS = 20


def _is_temporary_smtp_error(exc) -> bool:
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return bool(codes) and all(400 <= code < 500 for code in codes)
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    return False

logger = logging.getLogger("campaigns.sender")

# Tracks session ids currently being sent by a thread in *this* process,
# so a double click on "Start Sending" can't spawn two workers for the
# same session. This does NOT survive a process restart — that's what
# SendingSession.last_heartbeat / heartbeat_is_stale is for.
_running_lock = threading.Lock()
_running_session_ids = set()


def format_address(name: str, email: str) -> str:
    """Always returns the address wrapped in <>, e.g. '"Alice" <a@x.com>'
    or '<a@x.com>' if there's no name."""
    name = (name or "").strip()
    if name:
        return formataddr((name, email))
    return f"<{email}>"


def _html_to_plain_text(html: str) -> str:
    """Best-effort plain-text fallback for the HTML body, for mail clients
    that don't render HTML. Turns common block-level breaks into newlines
    before stripping tags, so paragraphs/list items don't run together."""
    import re

    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = strip_tags(text)
    # Collapse runs of 3+ blank lines and trim trailing whitespace per line.
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _reconnect(connection):
    """Force-close and reopen an SMTP connection. Used when the server has
    dropped it out from under us (idle timeout, per-connection message cap,
    etc.) so the run can keep going instead of failing every remaining
    recipient."""
    try:
        connection.close()
    except Exception:  # noqa: BLE001 — the old connection is already dead; ignore close errors
        pass
    connection.open()


def is_running(session_id: int) -> bool:
    with _running_lock:
        return session_id in _running_session_ids


def start_sending(session_id: int):
    """Kick off (or resume) sending for a session, unless it's already
    running in this process. Safe to call repeatedly."""
    with _running_lock:
        if session_id in _running_session_ids:
            return False
        _running_session_ids.add(session_id)

    thread = threading.Thread(target=_run, args=(session_id,), daemon=True)
    thread.start()
    return True


def _run(session_id: int):
    from campaigns.models import SendingSession, Recipient  # local import avoids app-loading order issues

    try:
        session = SendingSession.objects.select_related("smtp_config").get(pk=session_id)
        session.status = SendingSession.STATUS_SENDING
        session.stop_requested = False
        session.last_heartbeat = timezone.now()
        session.save(update_fields=["status", "stop_requested", "last_heartbeat"])

        smtp = session.smtp_config
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=smtp.host,
            port=smtp.port,
            username=smtp.username,
            password=smtp.get_password(),
            use_tls=smtp.use_tls,
            use_ssl=smtp.use_ssl,
            fail_silently=False,
        )
        connection.open()

        from_header = format_address(smtp.from_name, smtp.from_email)
        delay = getattr(settings, "SEND_DELAY_SECONDS", 1.5)

        stopped = False
        while True:
            recipient = (
                session.recipients.filter(status=Recipient.STATUS_PENDING).order_by("id").first()
            )
            if recipient is None:
                break

            session.refresh_from_db(fields=["stop_requested"])
            if session.stop_requested:
                stopped = True
                break

            merge_name = recipient.name or ""
            subject = session.subject.replace("{{name}}", merge_name)
            html_body = session.body.replace("{{name}}", merge_name)
            plain_body = _html_to_plain_text(html_body)
            message = EmailMultiAlternatives(
                subject=subject,
                body=plain_body,
                from_email=from_header,
                to=[format_address(recipient.name, recipient.email)],
                connection=connection,
            )
            message.attach_alternative(html_body, "text/html")
            if session.attachment:
                try:
                    session.attachment.open("rb")
                    message.attach(
                        session.attachment.name.rsplit("/", 1)[-1],
                        session.attachment.read(),
                    )
                finally:
                    session.attachment.close()

            recipient.attempts += 1
            send_error = None
            gave_up_for_stop = False
            for temp_attempt in range(_MAX_TEMPORARY_RETRIES + 1):
                try:
                    try:
                        sent_count = message.send(fail_silently=False)
                    except _CONNECTION_DROPPED_ERRORS as conn_exc:
                        logger.warning(
                            "SMTP connection dropped while sending to %s (%s) — reconnecting and retrying once",
                            recipient.email, conn_exc,
                        )
                        _reconnect(connection)
                        sent_count = message.send(fail_silently=False)
                    if sent_count != 1:
                        # message.send() returns how many messages actually
                        # went out. With fail_silently=False this can only
                        # come back 0 if Django decided there was nothing to
                        # send (e.g. no recipients on the message) rather
                        # than raising — treat that as a failure explicitly
                        # instead of silently taking "no exception" as proof
                        # of success.
                        raise RuntimeError(
                            f"send() reported {sent_count} message(s) sent, expected 1 — not counting this as delivered"
                        )
                    send_error = None
                    break
                except Exception as exc:  # noqa: BLE001 — one bad recipient must not kill the run
                    send_error = exc
                    if _is_temporary_smtp_error(exc) and temp_attempt < _MAX_TEMPORARY_RETRIES:
                        session.refresh_from_db(fields=["stop_requested"])
                        if session.stop_requested:
                            # Don't burn a retry (or mark this recipient failed)
                            # just because the user hit Stop mid-backoff —
                            # leave it pending and let the outer loop stop.
                            gave_up_for_stop = True
                            break
                        logger.warning(
                            "Temporary rejection for %s (%s) — waiting %ss before retry %s/%s",
                            recipient.email, exc, _TEMPORARY_RETRY_BACKOFF_SECONDS,
                            temp_attempt + 1, _MAX_TEMPORARY_RETRIES,
                        )
                        time.sleep(_TEMPORARY_RETRY_BACKOFF_SECONDS)
                        continue
                    break

            if gave_up_for_stop:
                stopped = True
                break

            if send_error is None:
                recipient.status = Recipient.STATUS_SENT
                recipient.last_sent_at = timezone.now()
                recipient.error_message = ""
            else:
                recipient.status = Recipient.STATUS_FAILED
                recipient.error_message = str(send_error)
                logger.warning("Send failed for %s: %s", recipient.email, send_error)

            recipient.save(update_fields=["status", "last_sent_at", "error_message", "attempts"])

            session.last_heartbeat = timezone.now()
            session.save(update_fields=["last_heartbeat"])

            time.sleep(delay)

        connection.close()
        session.status = SendingSession.STATUS_PAUSED if stopped else SendingSession.STATUS_COMPLETED
        session.save(update_fields=["status"])

    except Exception:
        logger.exception("Sending session %s crashed", session_id)
        try:
            SendingSession.objects.filter(pk=session_id).update(status=SendingSession.STATUS_PAUSED)
        except Exception:  # noqa: BLE001
            pass
    finally:
        with _running_lock:
            _running_session_ids.discard(session_id)
