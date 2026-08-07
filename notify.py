#!/usr/bin/env python3
"""Send pipeline failure alerts via Gmail SMTP XOAUTH2."""

import argparse
import base64
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://mail.google.com/",
]


def _repo_root() -> Path:
    """Return the directory containing this script (and the project .env)."""
    return Path(__file__).resolve().parent


def _load_env() -> None:
    load_dotenv(_repo_root() / ".env")


def _read_message_body(log_path: str | None, body: str | None) -> str:
    if body:
        return body
    if not log_path:
        raise RuntimeError("either --log or --body must be provided")

    path = Path(log_path)
    if not path.exists():
        raise RuntimeError(f"log file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _credentials_path() -> Path:
    """OAuth client secrets downloaded from Google Cloud Console."""
    return _repo_root() / "credentials.json"


def _save_credentials(creds: Credentials, token_path: str) -> None:
    """Persist credentials as an authorized-user file."""
    Path(token_path).write_text(creds.to_json(), encoding="utf-8")


def _run_consent_flow() -> Credentials:
    """Run the OAuth consent flow to obtain a mail-scoped token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = _credentials_path()
    if not creds_path.is_file():
        raise RuntimeError(
            f"OAuth client secrets not found: {creds_path}. "
            "Download them from the Google Cloud Console as credentials.json."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    return flow.run_local_server(port=0)


def _get_credentials(token_path: str) -> Credentials:
    """Load mail OAuth credentials, running the consent flow if needed.

    The token file must contain a token granted for the mail scope; if it is
    missing or was granted for different scopes, the consent flow is run.
    """
    creds = None

    if os.path.exists(token_path):
        try:
            data = json.loads(Path(token_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if SCOPES[0] in set(data.get("scopes", [])):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds is not None and creds.valid:
        return creds

    if creds is not None and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds, token_path)
            return creds
        except Exception:
            print("Mail token refresh failed; re-authenticating...", file=sys.stderr)

    print("Requesting Gmail authorization...", file=sys.stderr)
    creds = _run_consent_flow()
    _save_credentials(creds, token_path)
    return creds


def send_failure_alert(subject: str, message_body: str) -> None:
    _load_env()

    sender = os.getenv("GMAIL_ADDRESS")
    recipient = os.getenv("NOTIFY_EMAIL_TO") or sender
    token_path = os.getenv("NOTIFY_TOKEN_PATH", str(_repo_root() / "mail_token.json"))

    if not sender:
        raise RuntimeError("GMAIL_ADDRESS not set in .env")
    if not recipient:
        raise RuntimeError("NOTIFY_EMAIL_TO not set and GMAIL_ADDRESS missing")

    creds = _get_credentials(token_path)

    auth_string = f"user={sender}\x01auth=Bearer {creds.token}\x01\x01"
    encoded_auth = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"[notesnook] {subject}"
    msg.set_content(message_body)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        code, response = smtp.docmd("AUTH", "XOAUTH2 " + encoded_auth)
        if code != 235:
            raise RuntimeError(f"SMTP AUTH failed ({code}): {response!r}")
        smtp.send_message(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a failure notification email")
    parser.add_argument("--subject", required=True, help="Email subject line")
    parser.add_argument("--log", help="Path to log file used as email body")
    parser.add_argument("--body", help="Optional direct body text")
    args = parser.parse_args()

    try:
        message_body = _read_message_body(args.log, args.body)
        send_failure_alert(args.subject, message_body)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should surface all failures
        print(f"notify.py error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
