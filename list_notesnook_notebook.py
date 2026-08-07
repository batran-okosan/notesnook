#!/usr/bin/env python3
"""Recursively list a notebook from this Notesnook JSON export.

When no backup file is given, the script uses today's backup from the
``backups/`` directory (``backups/YYYY-MM-DD-08-00-0.nnbackupz``). If that
file is missing, an email notification is sent via ``notify.py``. After a
successful run, the auto-detected backup file is deleted from disk.

Examples
--------
python list_notesnook_notebook.py --list-notebooks
python list_notesnook_notebook.py investing
python list_notesnook_notebook.py unzipped.json --list-notebooks
python list_notesnook_notebook.py 2026-08-04-23-50-11.nnbackupz investing
python list_notesnook_notebook.py unzipped.json "trading archive"
python list_notesnook_notebook.py unzipped.json 694441044497c7197d44b3e7 --show-content
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important --modified-within-days 7
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important --tag lessons --tag-match any
python list_notesnook_notebook.py investing --no-upload-to-gdrive
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import json
import os
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).with_name(".env")


def load_env_file() -> None:
    """Load KEY=VALUE settings from the project .env file, if present.

    python-dotenv does not override variables already present in the process
    environment, so a shell variable takes precedence over the .env value.
    """
    if ENV_PATH.is_file():
        load_dotenv(ENV_PATH)


def setting(name, fallback):
    """Environment variables override .env values, which override fallbacks."""
    return os.environ.get(name) or fallback


def comma_separated(value):
    return [part.strip() for part in value.split(",") if part.strip()]


class NoteTextExtractor(HTMLParser):
    """Turn Notesnook's Tiptap HTML into readable plain text."""
    block_tags = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "br" or tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        output = []
        for line in lines:
            if line or (output and output[-1]):
                output.append(line)
        return "\n".join(output).strip()


def note_text(html):
    extractor = NoteTextExtractor()
    extractor.feed(html or "")
    extractor.close()
    return extractor.text()


def modified_timestamp(note):
    return note.get("dateModified", 0)


def modified_label(note):
    return datetime.fromtimestamp(
        modified_timestamp(note) / 1000, timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def read_backup_items(path: Path):
    """Read an exported JSON file or decode a Notesnook .nnbackupz archive."""
    if path.suffix.casefold() != ".nnbackupz":
        with path.open(encoding="utf-8") as file:
            return json.load(file)

    try:
        with zipfile.ZipFile(path) as archive:
            plain_members = [member for member in archive.infolist()
                             if not member.is_dir()
                             and Path(member.filename).name.startswith("0-plain-")]
            if len(plain_members) != 1:
                raise ValueError(
                    f"Expected exactly one 0-plain-* member; found {len(plain_members)}."
                )
            wrapper = json.loads(archive.read(plain_members[0]))
        encoded_data = wrapper["data"]
        if not isinstance(encoded_data, str):
            raise ValueError("The 0-plain-* member's data field is not a string.")
        return json.loads(gzip.decompress(base64.b64decode(encoded_data)))
    except (KeyError, TypeError, binascii.Error, gzip.BadGzipFile, zipfile.BadZipFile) as error:
        raise ValueError(f"Cannot decode {path.name}: {error}") from error


def load_backup(path: Path, include_deleted: bool):
    """Index the flat, typed-record Notesnook export."""
    items = read_backup_items(path)
    if not isinstance(items, list):
        raise ValueError("Expected a top-level JSON list.")

    def keep(item):
        return include_deleted or not item.get("deleted", False)

    notebooks = {item["id"]: item for item in items
                 if item.get("type") == "notebook" and keep(item)}
    notes = {item["id"]: item for item in items
             if item.get("type") == "note" and keep(item)}
    contents = {item.get("noteId"): item.get("data", "") for item in items
                if item.get("type") == "tiptap" and keep(item)}
    tags = {item["id"]: item for item in items
            if item.get("type") == "tag" and keep(item)}

    # In this export, a relation points from the containing notebook to its child.
    child_notebooks = defaultdict(list)
    notebook_notes = defaultdict(list)
    tag_notes = defaultdict(set)
    for item in items:
        if item.get("type") != "relation" or not keep(item):
            continue
        if item.get("fromType") != "notebook":
            if (item.get("fromType") == "tag" and item.get("toType") == "note"
                    and item.get("fromId") in tags and item.get("toId") in notes):
                tag_notes[item["fromId"]].add(item["toId"])
            continue
        if item.get("toType") == "notebook" and item.get("toId") in notebooks:
            child_notebooks[item["fromId"]].append(item["toId"])
        elif item.get("toType") == "note" and item.get("toId") in notes:
            notebook_notes[item["fromId"]].append(item["toId"])

    return notebooks, notes, contents, tags, child_notebooks, notebook_notes, tag_notes


def print_tree(notebook_id, notebooks, notes, contents, child_notebooks,
               notebook_notes, depth, show_content, visited, allowed_notes=None,
               important_notes=frozenset(), excluded_children=frozenset()):
    prefix = "  " * depth

    note_ids = (note_id for note_id in notebook_notes.get(notebook_id, ())
                if allowed_notes is None or note_id in allowed_notes)
    for note_id in sorted(note_ids,
                          key=lambda n: notes[n].get("title", "").casefold()):
        note = notes[note_id]
        modified = modified_label(note)
        marker = " [IMPORTANT]" if note_id in important_notes else ""
        print(f"{prefix}- NOTE: {note.get('title', '(untitled)')} [{note_id}]"
              f" — modified {modified}{marker}")
        if show_content:
            data = contents.get(note_id, "")
            if data:
                for line in data.splitlines() or [""]:
                    print(f"{prefix}    {line}")

    for child_id in sorted(child_notebooks.get(notebook_id, ()),
                           key=lambda n: notebooks[n].get("title", "").casefold()):
        if child_id in excluded_children:
            continue
        child = notebooks[child_id]
        print(f"{prefix}- NOTEBOOK: {child.get('title', '(untitled)')} [{child_id}]")
        if child_id in visited:
            print(f"{prefix}    (already visited; not descending again)")
            continue
        visited.add(child_id)
        print_tree(child_id, notebooks, notes, contents, child_notebooks,
                   notebook_notes, depth + 1, show_content, visited, allowed_notes,
                   important_notes)


def resolve_notebook(query, notebooks, child_notebooks):
    """Resolve an ID, an exact title, or a slash-delimited notebook path."""
    if query in notebooks:
        return notebooks[query]

    parts = [part.strip() for part in query.split("/") if part.strip()]
    if not parts:
        raise ValueError("Notebook path cannot be empty.")

    # The first component may be any notebook; following components may only
    # match children of the preceding component.
    candidates = [notebook for notebook in notebooks.values()
                  if notebook.get("title") == parts[0]]
    for part in parts[1:]:
        candidates = [notebooks[child_id]
                      for parent in candidates
                      for child_id in child_notebooks.get(parent["id"], ())
                      if notebooks[child_id].get("title") == part]

    if not candidates:
        raise ValueError(f"No notebook matches {query!r}. Use --list-notebooks.")
    if len(candidates) > 1:
        raise ValueError(
            f"{query!r} is ambiguous. Supply more of the path, or use an ID from --list-notebooks."
        )
    return candidates[0]


def resolve_tag(query, tags):
    """Resolve a tag by its ID or exact title."""
    if query in tags:
        return tags[query]
    matches = [tag for tag in tags.values() if tag.get("title") == query]
    if not matches:
        raise ValueError(f"No tag matches {query!r}.")
    if len(matches) > 1:
        raise ValueError(f"Tag title {query!r} is ambiguous; use its ID.")
    return matches[0]


def parse_days(value):
    """Validate a whole-number rolling date window."""
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("days must be a positive whole number, e.g. 7")
    if days < 1:
        raise argparse.ArgumentTypeError("days must be at least 1")
    return days


def matching_notes(notes, selected_tags, tag_notes, modified_after,
                   tag_match, filter_mode):
    """Return note IDs satisfying the requested tag/date predicates.

    tag_match combines multiple tags. filter_mode then combines that single
    tag predicate with the optional modification-date predicate.
    """
    if not selected_tags and modified_after is None:
        return None

    result = set()
    for note_id, note in notes.items():
        predicates = []
        if selected_tags:
            memberships = [note_id in tag_notes[tag_id] for tag_id in selected_tags]
            predicates.append(all(memberships) if tag_match == "all" else any(memberships))
        if modified_after is not None:
            predicates.append(note.get("dateModified", 0) > modified_after)
        if (all(predicates) if filter_mode == "all" else any(predicates)):
            result.add(note_id)
    return result


def collect_notes(notebook_id, child_notebooks, notebook_notes, excluded_children=frozenset(), seen=None):
    """Collect notes below a notebook, excluding selected direct children."""
    if seen is None:
        seen = set()
    if notebook_id in seen:
        return set()
    seen.add(notebook_id)
    result = set(notebook_notes.get(notebook_id, ()))
    for child_id in child_notebooks.get(notebook_id, ()):
        if child_id not in excluded_children:
            result.update(collect_notes(child_id, child_notebooks, notebook_notes, (), seen))
    return result


def write_report(path, heading, note_ids, notes, contents):
    """Write full note bodies in descending modification-date order."""
    ordered = sorted((notes[note_id] for note_id in note_ids),
                     key=modified_timestamp, reverse=True)
    sections = [f"{heading}\nNotes: {len(ordered)}"]
    for note in ordered:
        body = note_text(contents.get(note["id"], "")) or "(No note content found.)"
        sections.append(
            "\n".join((
                "=" * 80,
                f"TITLE: {note.get('title', '(untitled)')}",
                f"LAST MODIFIED: {modified_label(note)}",
                "",
                body,
            ))
        )
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Backup auto-detection
# ---------------------------------------------------------------------------

# Backups are downloaded daily at 08:00 into the backups/ directory (relative
# to the working directory), named e.g. backups/2026-08-07-08-00-0.nnbackupz.
BACKUP_DIR = Path("backups")
BACKUP_TIME_CONSTANT = "08-00-0"


def default_backup_path() -> Path:
    """Return today's expected backup path: backups/YYYY-MM-DD-08-00-0.nnbackupz."""
    today = datetime.now().strftime("%Y-%m-%d")
    return BACKUP_DIR / f"{today}-{BACKUP_TIME_CONSTANT}.nnbackupz"


def notify_backup_missing(expected: Path) -> None:
    """Email the user that today's backup file is missing."""
    from notify import send_failure_alert

    send_failure_alert(
        subject="Notesnook backup file missing",
        message_body=(
            "The expected Notesnook backup file was not found:\n\n"
            f"    {expected}\n\n"
            "Please check that today's backup was downloaded and placed "
            "in the backups/ directory."
        ),
    )


def resolve_backup_path(backup_arg) -> Path:
    """Return the backup file to read.

    If ``backup_arg`` is given it must already exist. Otherwise the script
    uses today's automatically named backup file; if that file is missing, an
    email notification is sent and the script exits.
    """
    if backup_arg is not None:
        if not backup_arg.is_file():
            raise SystemExit(f"Backup file not found: {backup_arg}")
        return backup_arg

    expected = default_backup_path()
    if not expected.is_file():
        notify_backup_missing(expected)
        raise SystemExit(
            f"Backup file not found: {expected}\n"
            "(a notification email has been sent)"
        )

    print(f"Using today's backup: {expected}")
    return expected


def delete_processed_backup(backup_path: Path) -> None:
    """Delete the backup file after it has been processed successfully.

    Only auto-detected daily backups are removed; a backup file the user
    explicitly passed as an argument is always left untouched.
    """
    try:
        backup_path.unlink()
        print(f"Deleted processed backup: {backup_path}")
    except OSError as error:
        print(f"Warning: could not delete backup {backup_path}: {error}",
              file=sys.stderr)


def main():
    load_env_file()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, nargs="?", default=None,
                        help="Path to unzipped.json or a Notesnook .nnbackupz archive; "
                             "defaults to today's file in backups/ "
                             "(e.g. backups/2026-08-07-08-00-0.nnbackupz)")
    parser.add_argument("notebook", nargs="?",
                        help="Notebook ID, exact title, or path such as 'investing/lessons'")
    parser.add_argument("--list-notebooks", action="store_true",
                        help="List every non-deleted notebook ID and title")
    parser.add_argument("--show-content", action="store_true",
                        help="Print each note's raw Tiptap/HTML body")
    parser.add_argument("--tag", metavar="TAG", action="append",
                        help="Filter by exact tag title or ID; repeat to provide multiple tags")
    parser.add_argument("--tag-match", choices=("all", "any"),
                        default=setting("DEFAULT_TAG_MATCH", "any"),
                        help="How repeated --tag values combine (default: any)")
    parser.add_argument("--modified-within-days", type=parse_days, metavar="DAYS",
                        default=parse_days(setting("DEFAULT_MODIFIED_WITHIN_DAYS", "7")),
                        help="Filter to notes modified during the preceding number of UTC days (default: 7)")
    parser.add_argument("--filter-mode", choices=("all", "any"),
                        default=setting("DEFAULT_FILTER_MODE", "any"),
                        help="How the tag and date conditions combine (default: any)")
    parser.add_argument("--exclude-notebook", metavar="NOTEBOOK", action="append",
                        help="Exclude a direct child notebook by title or ID; repeat as needed")
    parser.add_argument("--output-dir", type=Path, metavar="DIRECTORY",
                        default=Path(setting("DEFAULT_OUTPUT_DIRECTORY", ".")),
                        help="Directory for important.txt and recent.txt (default: script directory)")
    parser.add_argument("--include-deleted", action="store_true")
    parser.add_argument("--upload-to-gdrive", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Upload important.txt and recent.txt to Google Docs "
                             "via update_gdoc.py (default: on; use "
                             "--no-upload-to-gdrive to skip)")
    args = parser.parse_args()

    if (args.backup is not None and args.notebook is None
            and not args.backup.is_file()):
        # Backward-compatible single positional: `script.py NOTEBOOK`
        # auto-detects today's backup instead of treating NOTEBOOK as a path.
        args.notebook = str(args.backup)
        args.backup = None

    backup_path = resolve_backup_path(args.backup)
    try:
        notebooks, notes, contents, tags, children, notebook_notes, tag_notes = load_backup(
            backup_path, args.include_deleted
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.exit(f"Cannot read backup: {error}")

    if args.list_notebooks:
        for notebook in sorted(notebooks.values(), key=lambda n: n.get("title", "").casefold()):
            print(f"{notebook['id']}\t{notebook.get('title', '(untitled)')}")
        return
    if not args.notebook:
        parser.error("notebook is required unless --list-notebooks is used")

    try:
        root = resolve_notebook(args.notebook, notebooks, children)
    except ValueError as error:
        sys.exit(error)

    selected_tags = []
    for query in args.tag or ():
        try:
            selected_tags.append(resolve_tag(query, tags))
        except ValueError as error:
            sys.exit(error)
    modified_after = None
    if args.modified_within_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.modified_within_days)
        modified_after = int(cutoff.timestamp() * 1000)
    allowed_notes = matching_notes(
        notes,
        [tag["id"] for tag in selected_tags],
        tag_notes,
        modified_after,
        args.tag_match,
        args.filter_mode,
    )
    important_notes = set().union(*(
        tag_notes[tag["id"]]
        for tag in tags.values()
        if tag.get("title", "").casefold() == "important"
    ))
    excluded_names = (args.exclude_notebook if args.exclude_notebook is not None
                      else comma_separated(setting("EXCLUDE_NOTEBOOKS", "")))
    excluded_children = {
        child_id for child_id in children.get(root["id"], ())
        if child_id in excluded_names or notebooks[child_id].get("title") in excluded_names
    }
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reports use the requested notebook tree, not the optional tag filter.
    # Important notes are retained regardless of age; recent.txt excludes them.
    report_notes = collect_notes(root["id"], children, notebook_notes, excluded_children)
    important_report_notes = report_notes & important_notes
    recent_report_notes = {
        note_id for note_id in report_notes - important_notes
        if modified_timestamp(notes[note_id]) > modified_after
    }
    important_path = output_dir / "important.txt"
    recent_path = output_dir / "recent.txt"
    write_report(important_path, "IMPORTANT NOTES", important_report_notes, notes, contents)
    write_report(recent_path, "RECENT NOTES (NOT IMPORTANT)", recent_report_notes, notes, contents)
    if args.upload_to_gdrive:
        from update_gdoc import upload_reports
        upload_reports(important_path=important_path, recent_path=recent_path)
    print(f"NOTEBOOK: {root.get('title', '(untitled)')} [{root['id']}]")
    if selected_tags:
        labels = ", ".join(f"{tag.get('title', '(untitled)')} [{tag['id']}]"
                           for tag in selected_tags)
        print(f"TAG FILTER ({args.tag_match}): {labels}")
    if args.modified_within_days is not None:
        print(f"MODIFIED WITHIN LAST: {args.modified_within_days} days (UTC)")
    if selected_tags and args.modified_within_days is not None:
        print(f"FILTER MODE: {args.filter_mode}")
    if excluded_children:
        names = ", ".join(notebooks[child_id].get("title", "(untitled)")
                          for child_id in sorted(excluded_children))
        print(f"EXCLUDING DIRECT CHILD NOTEBOOKS: {names}")
    print(f"WROTE: {important_path}")
    print(f"WROTE: {recent_path}")
    print_tree(root["id"], notebooks, notes, contents, children, notebook_notes,
               depth=1, show_content=args.show_content, visited={root["id"]},
               allowed_notes=allowed_notes, important_notes=important_notes,
               excluded_children=excluded_children)

    # Free disk space: remove the auto-detected daily backup after a fully
    # successful run (reports written and uploaded). Explicitly provided
    # backups are kept. This is the last step, so any earlier failure leaves
    # the backup in place.
    if args.backup is None:
        delete_processed_backup(backup_path)


if __name__ == "__main__":
    main()
