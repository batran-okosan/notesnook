# update_gdoc.py
"""Upload the important.txt and recent.txt reports produced by
list_notesnook_notebook.py to Google Docs.

Two Google Docs are managed:
  - "Notesnook — Important Notes"  (from important.txt)
  - "Notesnook — Recent Notes"     (from recent.txt)

Document IDs are cached in the project .env file as IMPORTANT_GDOC_ID and
RECENT_GDOC_ID. The docs are created automatically on first run using the
find-or-create pattern from the original script.
"""

import io
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",  # narrowest scope that allows create/update
]

IMPORTANT_TITLE = "Notesnook — Important Notes"
RECENT_TITLE = "Notesnook — Recent Notes"

ENV_PATH = Path(__file__).with_name(".env")
TOKEN_PATH = Path(__file__).with_name("token.json")


def _load_env():
    """Load KEY=VALUE settings from the project .env file."""
    load_dotenv(ENV_PATH)


def get_creds():
    """Load credentials from token.json, refreshing if necessary."""
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "No valid credentials found in token.json. Run the original "
                "authorization flow once to create it."
            )
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())
    return creds


def get_drive_service():
    return build("drive", "v3", credentials=get_creds())


def create_doc_once(drive_service, title):
    """Create a Google Doc with the given title if it doesn't already exist in
    the root folder of Google Drive.

    If a document with the same title already exists, return its ID; else
    create and return the new ID.
    """
    # Search for existing document with same title, trashed=false
    q = (
        f"name = '{title}' and "
        "mimeType = 'application/vnd.google-apps.document' and "
        "trashed = false"
    )
    response = (
        drive_service.files()
        .list(q=q, fields="files(id, name)", pageSize=1)
        .execute()
    )
    files = response.get("files", [])
    if files:
        print(f"Found existing document: {title} ({files[0]['id']})")
        return files[0]["id"]

    doc = (
        drive_service.files()
        .create(
            body={
                "name": title,
                "mimeType": "application/vnd.google-apps.document",
            },
            fields="id",
        )
        .execute()
    )
    print(f"Created document: {title} ({doc['id']})")
    return doc["id"]


def _save_env_value(key, value):
    """Add or replace a KEY=VALUE line in the project .env file."""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_key = stripped.split("=", 1)[0].strip()
            if existing_key == key:
                lines[i] = f"{key}={value}"
                replaced = True
                break

    if not replaced:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    print(f"Stored {key}={value} in {ENV_PATH}")


def get_doc_id(drive_service, env_key, title):
    """Return the document ID, creating the doc and caching the ID in .env."""
    _load_env()
    doc_id = os.getenv(env_key)
    if doc_id:
        return doc_id

    doc_id = create_doc_once(drive_service, title)
    _save_env_value(env_key, doc_id)
    return doc_id


def replace_doc_content(drive_service, doc_id, text_path):
    """Replace a Google Doc's content with the given text file's contents."""
    text_path = Path(text_path)
    if not text_path.exists():
        print(f"Warning: {text_path} not found; skipping.", file=sys.stderr)
        return

    with open(text_path, "r", encoding="utf-8") as f:
        content = f.read()

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/plain",
        resumable=False,
    )
    updated = (
        drive_service.files()
        .update(fileId=doc_id, media_body=media, fields="id,name")
        .execute()
    )
    print(
        f"Google Doc '{updated.get('name', '(unknown)')}' ({doc_id}) "
        f"updated from {text_path}."
    )


def upload_reports(important_path=None, recent_path=None, drive_service=None):
    """Create or locate the two Google Docs and replace their content with the
    important.txt and recent.txt reports.

    Parameters
    ----------
    important_path : str or Path, optional
        Path to the important notes text file. Defaults to
        ``important.txt`` next to this script.
    recent_path : str or Path, optional
        Path to the recent notes text file. Defaults to ``recent.txt``
        next to this script.
    drive_service : googleapiclient.discovery.Resource, optional
        An already-built Drive service. If omitted, one is created.
    """
    if drive_service is None:
        drive_service = get_drive_service()

    important_doc_id = get_doc_id(
        drive_service, "IMPORTANT_GDOC_ID", IMPORTANT_TITLE
    )
    recent_doc_id = get_doc_id(
        drive_service, "RECENT_GDOC_ID", RECENT_TITLE
    )

    script_dir = Path(__file__).resolve().parent
    if important_path is None:
        important_path = script_dir / "important.txt"
    if recent_path is None:
        recent_path = script_dir / "recent.txt"

    replace_doc_content(drive_service, important_doc_id, important_path)
    replace_doc_content(drive_service, recent_doc_id, recent_path)


def main():
    """CLI entry point for upload_reports."""
    upload_reports()


if __name__ == "__main__":
    main()
