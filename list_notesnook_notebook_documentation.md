# `list_notesnook_notebook.py` — Documentation

## Purpose

This script reads a [Notesnook](https://notesnook.com/) backup and lets you explore one notebook's contents — including any nested "meta notebooks" — from the command line. Beyond simple listing, it supports tag- and date-based filtering, and it generates two standing text reports (`important.txt` and `recent.txt`) containing the full readable body text of matching notes.

Those reports are then **uploaded to Google Docs by default** (via `update_gdoc.py`), so the notes are readable in Google Drive without a separate step. If the script cannot find today's backup file, it sends an **email notification** (via `notify.py`) so you know the pipeline failed.

The script is designed to run unattended (e.g. from cron): with no arguments it uses today's backup automatically, generates the reports, and uploads them to Google Drive.

It was built iteratively through a conversation with Codex, starting from a basic recursive notebook reader and growing feature-by-feature into the tool described below.

## Input formats

The script accepts three ways of providing the backup data:

1. **Auto-detection from `backups/`** (default when no `backup` argument is given — see [Backup auto-detection](#backup-auto-detection)).
2. **`unzipped.json`** — a plain JSON export.
3. **A raw `.nnbackupz` file** (e.g. `2026-08-04-23-50-11.nnbackupz`) — Notesnook's native backup archive.

The file type is auto-detected by extension for explicit paths. For `.nnbackupz` files, the script performs the unpacking in memory, replicating what was previously done manually with `jq`/`base64`/`gunzip`:

1. Opens the file as a ZIP archive and locates the single member starting with `0-plain-`.
2. Reads that member's JSON `data` field (a Base64 string).
3. Base64-decodes it, then gzip-decompresses the result.
4. Parses the decompressed bytes as JSON — this is the same data `unzipped.json` contains.

No intermediate file is written; the script works directly from the archive.

## Backup auto-detection

When no `backup` argument is given, the script looks for today's backup file in the `backups/` directory, **relative to the working directory**:

```
backups/YYYY-MM-DD-08-00-0.nnbackupz
```

The date is the day the script runs; the time portion (`08-00-0`) is a constant, matching backups that are downloaded daily at 08:00. For example, running on `2026-08-07` expects `backups/2026-08-07-08-00-0.nnbackupz`.

**If that file is missing, the script sends an email notification and exits.** The email is sent by `notify.py` (see below) and includes the expected path so you know exactly which backup is missing.

**After a successful run, the auto-detected backup file is deleted from disk** — the reports are saved locally and uploaded to Google Docs, so the daily backups do not accumulate. The deletion happens only after the whole run has succeeded (reports written and uploaded); if anything fails, the backup is kept. A backup file you pass explicitly as an argument is never deleted.

Because `backup` is an optional positional argument, a single positional argument is treated as the **notebook**:

```bash
python list_notesnook_notebook.py investing      # notebook, backup auto-detected
python list_notesnook_notebook.py --list-notebooks
```

The two-positional form is unchanged:

```bash
python list_notesnook_notebook.py unzipped.json "trading archive"
python list_notesnook_notebook.py 2026-08-04-23-50-11.nnbackupz investing
```

## Email notification (`notify.py`)

When today's backup file cannot be found, the script calls `send_failure_alert()` from `notify.py`, which sends an email through Gmail SMTP using OAuth 2.0 (XOAUTH2).

Prerequisites (all configured in `.env`):

- `GMAIL_ADDRESS` — the Gmail address used to send the notification.
- `NOTIFY_EMAIL_TO` — the recipient; defaults to `GMAIL_ADDRESS`.
- `NOTIFY_TOKEN_PATH` — OAuth token file (default: `mail_token.json` next to the script). The token must be granted the `https://mail.google.com/` scope. If the token file is missing, expired, or was granted different scopes, `notify.py` launches the OAuth consent flow once using `credentials.json` and then caches the token.
- `credentials.json` — OAuth client secrets for the Google account (the same file used by the other Google scripts in this project).

You can also send a notification manually:

```bash
python notify.py --subject "Test notification" --body "Hello"
python notify.py --subject "Alert" --log /path/to/log.txt
```

## Google Drive upload (`update_gdoc.py`)

After writing the reports, the script uploads them to Google Docs **by default** — you do not need to pass any flag. Use `--no-upload-to-gdrive` to skip the upload (e.g. while experimenting locally).

`update_gdoc.py` manages two Google Docs:

| Local report | Google Doc title | `.env` key (auto-populated) |
|---|---|---|
| `important.txt` | `Notesnook — Important Notes` | `IMPORTANT_GDOC_ID` |
| `recent.txt` | `Notesnook — Recent Notes` | `RECENT_GDOC_ID` |

On the first run the docs are created automatically (find-or-create by title); their IDs are cached in `.env`. On later runs the existing docs' contents are replaced with the freshly generated reports. The upload requires a valid `token.json` carrying the `https://www.googleapis.com/auth/drive.file` scope.

## Data model

Through inspection of a real backup, the export was found to be a **flat list of typed records**, not a nested structure:

| `type` value | Represents | Key fields |
|---|---|---|
| `notebook` | A notebook | `id`, `title` |
| `note` | Note metadata | `id`, `title`, `dateModified`, `contentId` |
| `tiptap` | A note's body content | `noteId`, `data` (Tiptap/HTML) |
| `tag` | A tag definition | `id`, `title` |
| `relation` | A link between two records | `fromType`, `fromId`, `toType`, `toId` |

Hierarchy and membership are both expressed through `relation` records:
- `notebook → notebook`: nested (meta) notebooks.
- `notebook → note`: a note belonging to a notebook.
- `tag → note`: a note carrying a tag.

The script indexes all of this into lookup dictionaries once at startup (`load_backup`), then works entirely from those indexes.

Records marked `"deleted": true` are excluded by default; pass `--include-deleted` to keep them.

## Resolving a notebook

You identify the notebook you want with the `notebook` positional argument, which accepts:
- An exact notebook **ID**.
- An exact notebook **title**.
- A **slash-delimited path**, e.g. `"investing/lessons"`, resolved component-by-component so that each part after the first must be a direct child of the one before it. This avoids ambiguity if the same title (e.g. `lessons`) exists elsewhere in the tree.

Run with `--list-notebooks` to print every notebook's ID and title (handy for finding IDs or exact titles to use).

## Listing notes and notebooks

By default, the script prints an indented tree starting at the resolved notebook: its direct notes, then each child notebook (recursively). Cycles are guarded against — if a notebook is revisited, the script notes that instead of recursing again.

Each listed note shows:
- Title and ID
- Last-modified timestamp, formatted as UTC (`YYYY-MM-DD HH:MM UTC`)
- An `[IMPORTANT]` marker if the note carries the tag literally titled `important` — shown regardless of *why* the note appears in the list (i.e. even if it matched only through the OR date filter)

`--show-content` additionally prints each note's raw Tiptap/HTML body inline beneath its title.

## Filtering notes

Two independent filter dimensions can be combined:

**Tags** (`--tag TAG`, repeatable)
Each `--tag` accepts an exact tag title or tag ID. With multiple `--tag` values, `--tag-match` controls how they combine:
- `any` (default) — note has *at least one* of the given tags
- `all` — note has *every* given tag

**Modification recency** (`--modified-within-days DAYS`)
Keeps notes modified within the last `DAYS` days (UTC, calculated at run time). Defaults to **7**.

**Combining tag and date conditions** (`--filter-mode`)
- `any` (default) — note matches if it satisfies the tag condition **or** the date condition
- `all` — note must satisfy **both**

If no `--tag` is given, only the date condition applies (and vice versa if `--modified-within-days` is explicitly disabled — note that a default of 7 days is always active unless overridden).

This filtering affects which notes are shown in the console tree. The two generated reports (below) use their own logic layered on top of the notebook selection, independent of any `--tag` filter.

## Excluding subfolders

`--exclude-notebook NOTEBOOK` (repeatable, by title or ID) removes **direct child notebooks** of the selected root notebook from both the tree output and the generated reports. Exclusion is not recursive beyond the direct child — it only prevents descending into that specific branch.

Defaults come from the `EXCLUDE_NOTEBOOKS` variable in the `.env` file placed next to the script (see [Configuration](#configuration)).

## Generated reports

On every run, the script writes two plain-text files into `--output-dir` (default: the script's own directory):

- **`important.txt`** — every note tagged `important` within the selected notebook tree (respecting `--exclude-notebook`), regardless of age.
- **`recent.txt`** — non-important notes modified within the active `--modified-within-days` window.

Both files are ordered **newest first** by modification date and are fully rewritten each run. Each entry includes the note's title, last-modified date, and its full body converted from Tiptap/HTML into readable plain text (via a small built-in HTML-to-text extractor, `NoteTextExtractor`).

The reports are then uploaded to Google Docs unless `--no-upload-to-gdrive` is given (see [Google Drive upload](#google-drive-upload-update_gdocpy)).

## Configuration

Settings are read from a `.env` file placed next to the script using **python-dotenv**. Command-line flags take precedence; a variable already present in the shell environment overrides the `.env` value.

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_MODIFIED_WITHIN_DAYS` | `7` | Rolling UTC window used when `--modified-within-days` is not given |
| `DEFAULT_TAG_MATCH` | `any` | How multiple `--tag` values combine (`all`/`any`) |
| `DEFAULT_FILTER_MODE` | `any` | How the tag and date conditions combine (`all`/`any`) |
| `DEFAULT_OUTPUT_DIRECTORY` | `.` | Where `important.txt`/`recent.txt` are written |
| `EXCLUDE_NOTEBOOKS` | *(empty)* | Comma-separated direct child notebooks to exclude |
| `IMPORTANT_GDOC_ID` | *(auto)* | Google Doc ID for `important.txt` (managed by `update_gdoc.py`) |
| `RECENT_GDOC_ID` | *(auto)* | Google Doc ID for `recent.txt` (managed by `update_gdoc.py`) |
| `GMAIL_ADDRESS` | — | Gmail account used by `notify.py` to send alerts |
| `NOTIFY_EMAIL_TO` | `GMAIL_ADDRESS` | Recipient of alert emails |
| `NOTIFY_TOKEN_PATH` | `mail_token.json` | OAuth token file for the mail scope |

Example:

```env
DEFAULT_MODIFIED_WITHIN_DAYS=7
DEFAULT_TAG_MATCH=any
DEFAULT_FILTER_MODE=any
DEFAULT_OUTPUT_DIRECTORY=.
EXCLUDE_NOTEBOOKS=lessons,goals,reflections

IMPORTANT_GDOC_ID=1bN685rURYJgXet6SzO_a_ezk2eLGYr8fh4JRJKSbuD8
RECENT_GDOC_ID=1IYaN3KDrYLqoKof1u_dhqFyp7eiN6zpq9q2SPp2nMHU

GMAIL_ADDRESS=you@gmail.com
NOTIFY_EMAIL_TO=you@gmail.com
NOTIFY_TOKEN_PATH=mail_token.json
```

## Command-line reference

```
usage: list_notesnook_notebook.py [-h] [--list-notebooks] [--show-content]
                                  [--tag TAG] [--tag-match {all,any}]
                                  [--modified-within-days DAYS]
                                  [--filter-mode {all,any}]
                                  [--exclude-notebook NOTEBOOK]
                                  [--output-dir DIRECTORY] [--include-deleted]
                                  [--upload-to-gdrive | --no-upload-to-gdrive]
                                  [backup] [notebook]
```

| Argument | Description |
|---|---|
| `backup` | Path to `unzipped.json` or a `.nnbackupz` archive; if omitted, today's file in `backups/` is used (e.g. `backups/2026-08-07-08-00-0.nnbackupz`) |
| `notebook` | Notebook ID, exact title, or path like `investing/lessons` (omit if using `--list-notebooks`) |
| `--list-notebooks` | List every non-deleted notebook's ID and title, then exit |
| `--show-content` | Print each note's raw body inline in the console tree |
| `--tag TAG` | Filter by tag (title or ID); repeatable |
| `--tag-match {all,any}` | How multiple `--tag` values combine (default: `any`) |
| `--modified-within-days DAYS` | Rolling UTC window for the date filter (default: `7`) |
| `--filter-mode {all,any}` | How the tag and date conditions combine (default: `any`) |
| `--exclude-notebook NOTEBOOK` | Exclude a direct child notebook by title or ID; repeatable |
| `--output-dir DIRECTORY` | Where to write `important.txt`/`recent.txt` (default: script's directory) |
| `--include-deleted` | Include records marked as deleted |
| `--upload-to-gdrive` / `--no-upload-to-gdrive` | Upload reports to Google Docs via `update_gdoc.py` (default: on) |

## Usage examples

```bash
# List all notebooks (uses today's backup automatically)
python list_notesnook_notebook.py --list-notebooks

# List a notebook using today's auto-detected backup, generate reports, and
# upload them to Google Drive (default behaviour)
python list_notesnook_notebook.py investing

# Same, but skip the Google Drive upload
python list_notesnook_notebook.py investing --no-upload-to-gdrive

# Show the tree for a notebook by title (explicit backup)
python list_notesnook_notebook.py unzipped.json "trading archive"

# Same, but reading directly from a raw backup archive
python list_notesnook_notebook.py 2026-08-04-23-50-11.nnbackupz investing

# Nested notebook path, with inline note bodies
python list_notesnook_notebook.py unzipped.json 694441044497c7197d44b3e7 --show-content

# Only notes tagged "important"
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important

# "important" tag OR modified in the last 7 days (defaults)
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important --modified-within-days 7

# Has either of two tags
python list_notesnook_notebook.py unzipped.json "investing/lessons" --tag important --tag lessons --tag-match any

# Has BOTH tags AND was modified within the window
python list_notesnook_notebook.py unzipped.json "investing/lessons" \
  --tag important --tag lessons --tag-match all \
  --modified-within-days 30 --filter-mode all
```

## Development notes (from the build conversation)

- The script went through several iterations: a schema-tolerant first draft (written blind, before the backup could be inspected), then a rewrite once the real flat-record/`relation` schema was confirmed by direct inspection of the file.
- Early attempts to read the file were blocked by sandbox/permission issues (a Windows `EPERM` error on `AppData`, and a non-functional local command runner) before the file was successfully placed in the working directory and inspected.
- A tag filter was initially added but appeared to "not work" for `important` — investigation showed the tag genuinely didn't exist as a Notesnook tag in that particular backup snapshot (only as plain text within note bodies); it worked correctly once a backup containing the real tag was supplied.
- The OR/AND filter semantics, the rolling day-based date window, the `.env`-based defaults, the subfolder exclusion, and the two full-text reports were each added as discrete follow-up requests, in that order.
- Later additions: in-memory `.nnbackupz` decoding, automatic daily-backup detection from the `backups/` directory with an email notification when the file is missing (`notify.py`), default upload of the reports to Google Docs (`update_gdoc.py`), and a move to **python-dotenv** for reading the `.env` configuration.
