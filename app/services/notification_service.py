import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Notification, User


def queue_email(
    db: Session,
    recipient_user_id: int,
    subject: str,
    body: str,
    context_type: str | None = None,
    context_id: int | None = None,
):
    db.add(
        Notification(
            recipient_user_id=recipient_user_id,
            subject=subject,
            body=body,
            status="queued",
            context_type=context_type,
            context_id=context_id,
        )
    )


def get_pending_notifications(db: Session, limit: int = 100) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.channel == "email", Notification.status == "queued")
        .order_by(Notification.created_at.asc())
        .limit(limit)
        .all()
    )


def _smtp_settings() -> dict:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise HTTPException(
            status_code=400,
            detail="SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM.",
        )
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "from_email": os.getenv("SMTP_FROM", "").strip() or "noreply@atomquest.local",
    }


def send_queued_emails(db: Session, limit: int = 100) -> dict:
    settings = _smtp_settings()
    pending = get_pending_notifications(db, limit=limit)
    if not pending:
        return {"attempted": 0, "sent": 0, "failed": 0}

    sent = 0
    failed = 0
    for item in pending:
        recipient = db.query(User).filter(User.id == item.recipient_user_id).first()
        if not recipient:
            item.status = "failed"
            item.error_message = "Recipient user not found"
            failed += 1
            continue
        if not recipient.email:
            item.status = "failed"
            item.error_message = "Recipient email missing"
            failed += 1
            continue

        message = EmailMessage()
        message["Subject"] = item.subject
        message["From"] = settings["from_email"]
        message["To"] = recipient.email
        message.set_content(item.body)

        try:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=15) as server:
                server.starttls()
                if settings["user"]:
                    server.login(settings["user"], settings["password"])
                server.send_message(message)
            item.status = "sent"
            item.sent_at = datetime.utcnow()
            item.error_message = None
            sent += 1
        except (smtplib.SMTPException, OSError) as exc:
            item.status = "failed"
            item.error_message = str(exc)
            failed += 1

    db.commit()
    return {"attempted": len(pending), "sent": sent, "failed": failed}
