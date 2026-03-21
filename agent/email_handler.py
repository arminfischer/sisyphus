import smtplib
import imaplib
import email
import email.utils
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

from agent.config import settings
from agent.memory.db import get_session, EmailLog

log = logging.getLogger("sisyphus.email")


# ── Send ──────────────────────────────────────────────────────────────────────


def send_email(subject: str, body: str, to: str | None = None) -> None:
    to = to or settings.notify_email

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.gmail_user
    msg["To"] = to
    msg["Reply-To"] = settings.gmail_user
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.gmail_user, settings.gmail_app_password)
        smtp.sendmail(settings.gmail_user, to, msg.as_string())

    with get_session() as session:
        session.add(EmailLog(direction="sent", subject=subject, body=body))
        session.commit()
    log.info(f"Email sent: {subject!r} → {to}")


def send_daily_report(report: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    send_email(f"[Sisyphus] Daily Report — {now}", report)


def send_feedback_request(topic: str, context: str) -> None:
    subject = f"[Sisyphus] Feedback needed: {topic}"
    body = (
        f"Hello,\n\n"
        f"I need your input on the following:\n\n"
        f"{context}\n\n"
        f"Please reply to this email with your feedback or updated instructions.\n\n"
        f"— Sisyphus"
    )
    send_email(subject, body)


def send_test_email() -> None:
    send_email(
        "[Sisyphus] Test — agent is alive",
        "This is a test message confirming that Sisyphus is running and email is configured correctly.",
    )


# ── Receive ───────────────────────────────────────────────────────────────────


def fetch_unread_feedback() -> list[dict]:
    """
    Connect to Gmail via IMAP, fetch unread messages, mark them read,
    and return only those sent FROM NOTIFY_EMAIL as {subject, body} dicts.
    Messages from any other sender are marked read and silently discarded.
    """
    messages: list[dict] = []
    trusted = settings.notify_email.lower().strip()
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(settings.gmail_user, settings.gmail_app_password)
        mail.select("INBOX")

        _, data = mail.search(None, "UNSEEN")
        uids = data[0].split()
        log.info(f"Inbox: {len(uids)} unread message(s)")

        for uid in uids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Always mark as read so it doesn't pile up
            mail.store(uid, "+FLAGS", "\\Seen")

            sender = email.utils.parseaddr(msg.get("From", ""))[1].lower().strip()
            if sender != trusted:
                log.info(f"Ignored message from untrusted sender: {sender!r}")
                continue

            subject = msg.get("Subject", "")
            body = _extract_text_body(msg)

            messages.append({"subject": subject, "body": body})

            with get_session() as session:
                session.add(EmailLog(direction="received", subject=subject, body=body))
                session.commit()

        mail.logout()
    except Exception as e:
        log.warning(f"IMAP fetch error: {e}")

    return messages


def _extract_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode("utf-8", errors="replace") if payload else ""
