# `list_notesnook_notebook.py` — Documentation

## Purpose

This script reads a [Notesnook](https://notesnook.com/) backup and lets you explore one notebook's contents — including any nested "meta notebooks" — from the command line. Beyond simple listing, it supports tag- and date-based filtering, and it generates two standing text reports (`important.txt` and `recent.txt`) containing the full readable body text of matching notes.

It was built iteratively through a conversation with Codex, starting from a basic recursive notebook reader and growing feature-by-feature into the tool described below.

## Input formats

The script accepts two kinds of backup input, auto-detected by file extension:

1. **`unzipped.json`** — a plain JSON export.
2. **A raw `.nnbackupz` file** (e.g. `2026-08-04-23-50-11.nnbackupz`) — Notesnook's native backup archive.

For `.nnbackupz` files, the script performs the unpacking in memory, replicating what was previously done manually with `jq`/`base64`/`gunzip`:
1. Opens the file as a ZIP archive and locates the single member starting with `0-plain-`.
2. Reads that member's JSON `data` field (a Base64 string).
3. Base64-decodes it, then gzip-decompresses the result.
4. Parses the decompressed bytes as JSON — this is the same data `unzipped.json` contains.

No intermediate file is written; the script works directly from the archive.

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

Defaults come from an `.env` file placed next to the script:

```ini
DEFAULT_MODIFIED_WITHIN_DAYS=7
DEFAULT_TAG_MATCH=any
DEFAULT_FILTER_MODE=any
EXCLUDE_NOTEBOOKS=lessons,goals,reflections
```

Precedence: command-line flags > environment variables > `.env` values > built-in fallback.

## Generated reports

On every run, the script writes two plain-text files into `--output-dir` (default: the script's own directory):

- **`important.txt`** — every note tagged `important` within the selected notebook tree (respecting `--exclude-notebook`), regardless of age.
- **`recent.txt`** — non-important notes modified within the active `--modified-within-days` window.

Both files are ordered **newest first** by modification date and are fully rewritten each run. Each entry includes the note's title, last-modified date, and its full body converted from Tiptap/HTML into readable plain text (via a small built-in HTML-to-text extractor, `NoteTextExtractor`).

## Command-line reference

```
usage: list_notesnook_notebook.py [-h] [--list-notebooks] [--show-content]
                                   [--tag TAG] [--tag-match {all,any}]
                                   [--modified-within-days DAYS]
                                   [--filter-mode {all,any}]
                                   [--exclude-notebook NOTEBOOK]
                                   [--output-dir DIRECTORY] [--include-deleted]
                                   backup [notebook]
```

| Argument | Description |
|---|---|
| `backup` | Path to `unzipped.json` or a `.nnbackupz` archive |
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

## Usage examples

```powershell
# List all notebooks and their IDs
python list_notesnook_notebook.py unzipped.json --list-notebooks

# Show the tree for a notebook by title
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
python list_notesnook_notebook.py unzipped.json "investing/lessons" `
  --tag important --tag lessons --tag-match all `
  --modified-within-days 30 --filter-mode all
```

## Development notes (from the build conversation)

- The script went through several iterations: a schema-tolerant first draft (written blind, before the backup could be inspected), then a rewrite once the real flat-record/`relation` schema was confirmed by direct inspection of the file.
- Early attempts to read the file were blocked by sandbox/permission issues (a Windows `EPERM` error on `AppData`, and a non-functional local command runner) before the file was successfully placed in the working directory and inspected.
- A tag filter was initially added but appeared to "not work" for `important` — investigation showed the tag genuinely didn't exist as a Notesnook tag in that particular backup snapshot (only as plain text within note bodies); it worked correctly once a backup containing the real tag was supplied.
- The OR/AND filter semantics, the rolling day-based date window, the `.env`-based defaults, the subfolder exclusion, and the two full-text reports were each added as discrete follow-up requests, in that order.
