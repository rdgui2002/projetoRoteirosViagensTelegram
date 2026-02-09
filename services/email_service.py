from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_email_with_pdf(to_email: str, subject: str, body: str, pdf_bytes: bytes) -> str:
    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int((os.getenv("SMTP_PORT") or "587").strip())
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASS") or "").strip()
    from_email = (os.getenv("SMTP_FROM") or user).strip()

    if not (host and user and password and from_email and to_email):
        return "EMAIL_STATUS: SKIPPED (configure SMTP_* no .env)"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename="roteiro.pdf")

    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        return "EMAIL_STATUS: SENT ✅"
    except Exception as e:
        return f"EMAIL_STATUS: ERROR ({e})"
