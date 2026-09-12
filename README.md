# Google Docs PDF Exporter

Extract labeled Google Docs links from a Discord channel export, download each document as a PDF, and combine the PDFs into a single file.

The tool supports Discord messages that contain Google Docs links in embeds, messages containing multiple labeled links, and messages where a label is separated from its URL by a colon, a space, or a newline. Previously downloaded PDFs are stored by label and reused on later runs.

## Features

- Reads valid JSON exports produced by DiscordChatExporter.
- Extracts Google Docs links from Discord embed URLs.
- Uses the embed title as the document label when available.
- Falls back to the Google document ID when an embed has no title.
- Analyzes message content only when the message has no embeds.
- Supports multiple labeled links in a single message.
- Supports labels separated from URLs by a colon or whitespace, including newlines.
- Removes duplicate Google documents while preserving their first occurrence.
- Downloads Google Docs through the PDF export endpoint.
- Reuses valid PDFs already present in the individual-PDF directory.
- Replaces invalid or incomplete existing PDF files.
- Combines the PDFs in Discord message order.
- Adds a PDF bookmark for each extracted document label.
- Adds the document label as a visible chapter header on every page.
- Writes a JSON manifest containing the extracted labels and URLs.

## Requirements

- Python 3.9 or newer
- `requests`
- `pypdf`
- `reportlab`
- DiscordChatExporter CLI for creating the input JSON
- Read access to the Discord channel being exported
- Access to the linked Google Docs

## 1. Export the Discord channel

Use DiscordChatExporter CLI to export the channel and its threads as JSON:

```powershell
DiscordChatExporter.Cli export --include-threads -f Json -c <CHANNEL-ID> -t <ACCESS-TOKEN>
```

Replace:

- `<CHANNEL-ID>` with the ID of the Discord channel to export.
- `<ACCESS-TOKEN>` with the access token used by DiscordChatExporter.

The exporter writes a JSON file that can then be passed to this tool.

> [!CAUTION]
> Treat the access token as a secret. Do not commit it to source control, include it in screenshots, paste it into issue reports, or store it in the README. If a token is exposed, revoke or rotate it immediately.

## 2. Install the Python dependencies

It is recommended to use a virtual environment.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install requests pypdf reportlab
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests pypdf reportlab
```

## 3. Run the tool

Basic usage:

```bash
python google_docs_to_pdf.py export.json
```

This creates the following default outputs:

```text
combined_google_docs.pdf
google_docs_manifest.json
individual_pdfs/
```

Use custom paths when needed:

```bash
python google_docs_to_pdf.py export.json \
  --output combined.pdf \
  --manifest extracted_links.json \
  --keep-pdfs individual_pdfs
```

In Windows PowerShell, the same command can be written as:

```powershell
python .\google_docs_to_pdf.py .\export.json `
  --output .\combined.pdf `
  --manifest .\extracted_links.json `
  --keep-pdfs .\individual_pdfs
```

## Command-line options

```text
usage: google_docs_to_pdf.py [-h] [-o OUTPUT] [--manifest MANIFEST]
                             [--keep-pdfs DIRECTORY]
                             input
```

### `input`

Path to a valid DiscordChatExporter JSON file.

### `-o`, `--output`

Path for the combined PDF.

Default:

```text
combined_google_docs.pdf
```

### `--manifest`

Path for the JSON manifest containing the extracted document information.

Default:

```text
google_docs_manifest.json
```

### `--keep-pdfs`

Directory used to store and reuse the individual PDFs. Each PDF is named using its sanitized document label.

Default:

```text
individual_pdfs
```

For example, a document labeled `TOH 37` is stored as:

```text
individual_pdfs/TOH 37.pdf
```

## Extraction behavior

### Messages with embeds

If a message has one or more embeds, the embeds are preferred and the message content is not analyzed.

For every Google Docs embed:

- The Google Docs URL is extracted from `embed.url`.
- A non-empty `embed.title` is used as the label.
- If the title is absent or empty, the Google document ID is used as the fallback label.
- Non-Google-Docs embeds are ignored.

Example:

```json
{
  "content": "https://docs.google.com/document/d/EXAMPLE/edit",
  "embeds": [
    {
      "title": "TOH 31",
      "url": "https://docs.google.com/document/d/EXAMPLE/edit?usp=sharing"
    }
  ]
}
```

The extracted label is `TOH 31`.

### Messages without embeds

When `embeds` is empty or absent, the tool analyzes `message.content`.

The following formats are supported:

```text
TOH 37: https://docs.google.com/document/d/EXAMPLE/edit
```

```text
TOH 37 https://docs.google.com/document/d/EXAMPLE/edit
```

```text
TOH 37
https://docs.google.com/document/d/EXAMPLE/edit
```

A single message may also contain multiple entries:

```text
TOH 01: https://docs.google.com/document/d/DOCUMENT_ONE/edit
TOH 02: https://docs.google.com/document/d/DOCUMENT_TWO/edit
```

If a URL has no detectable label, the message ID is used as a stable fallback label.

## PDF storage and reuse

The `--keep-pdfs` directory is both the individual-document output directory and the reuse location.

Before downloading a document, the tool checks for a PDF named after the sanitized label. For example:

```text
individual_pdfs/TOH 37.pdf
```

If the file exists, begins with a PDF header, can be opened by `pypdf`, and contains at least one page, it is reused without another request to Google.

If the existing file is missing, empty, corrupt, or not a readable PDF, the tool downloads the document again and replaces the invalid file.

Downloads are first written to a temporary `.part` file. The temporary file is moved to its final name only after it has passed validation. This prevents interrupted downloads from being reused as complete PDFs.

> [!NOTE]
> Labels are assumed to be unique. If two different Google Docs use the same label, they resolve to the same individual PDF filename. Rename the labels in the Discord export or use separate output directories if this occurs.

## Manifest format

The manifest records each unique extracted document. A typical entry looks like this:

```json
{
  "label": "TOH 37",
  "document_id": "1DRDQlDTT9zyV60IGRL24f5rqAcUYWDpDLfsURljlFZU",
  "url": "https://docs.google.com/document/d/1DRDQlDTT9zyV60IGRL24f5rqAcUYWDpDLfsURljlFZU/edit",
  "pdf_url": "https://docs.google.com/document/d/1DRDQlDTT9zyV60IGRL24f5rqAcUYWDpDLfsURljlFZU/export?format=pdf"
}
```

Documents are deduplicated by Google document ID. Their first occurrence in the Discord export determines their position in the manifest and combined PDF.

## Chapter headers and navigation

Each source document becomes a chapter in the combined PDF. The tool adds the extracted label as a centered header on every page belonging to that document and adds the same label as a top-level PDF bookmark.

The header is placed in an added top margin, so it does not cover or replace content from the exported Google Docs PDF.

## Google Docs permissions

The PDF export endpoint must be accessible to the HTTP request made by the script. In the simplest setup, each Google Doc should be shared so that anyone with the link can view it.

If Google returns a sign-in page, access-denied page, or another non-PDF response, the tool stops and reports the affected label. The current script does not perform an interactive Google login or use OAuth credentials.

## Troubleshooting

### No labeled Google Docs links were found

Check that:

- The input is a valid JSON export.
- The JSON contains a top-level `messages` array.
- Embed URLs are stored in `embeds[].url`.
- Content URLs use the `docs.google.com/document/d/...` format.
- Messages with embeds contain the intended link in the embed, because message content is intentionally ignored when embeds are present.

### Google did not return a PDF

The document may require authentication or may not be shared with the necessary permissions. Open the document URL in a private browser window to test whether it is accessible without signing in.

### An old version of a document is reused

Delete the corresponding label-based PDF from the `--keep-pdfs` directory and run the tool again:

```powershell
Remove-Item ".\individual_pdfs\TOH 37.pdf"
python .\google_docs_to_pdf.py .\export.json
```

On Linux or macOS:

```bash
rm "individual_pdfs/TOH 37.pdf"
python google_docs_to_pdf.py export.json
```

### Two documents have the same label

Because labels are used as filenames, duplicate labels share the same path. Make the labels unique or process the documents using different `--keep-pdfs` directories.

### A partial `.part` file remains

A `.part` file can remain if the process is forcibly terminated. It is not treated as a completed PDF and may safely be deleted before the next run.

## Example workflow

```powershell
# 1. Export the Discord channel and its threads.
DiscordChatExporter.Cli export --include-threads -f Json -c <CHANNEL-ID> -t <ACCESS-TOKEN>

# 2. Create and activate a Python virtual environment.
python -m venv .venv
.\.venv\Scripts\activate

# 3. Install dependencies.
python -m pip install requests pypdf reportlab

# 4. Extract, download, reuse existing PDFs, and merge.
python .\google_docs_to_pdf.py .\export.json `
  --output ".\story.pdf" `
  --manifest ".\google_docs_manifest.json" `
  --keep-pdfs ".\individual_pdfs"
```

## Exit status

The tool returns:

- `0` when extraction, PDF retrieval or reuse, and merging complete successfully.
- `1` when the input cannot be read, no documents are found, a PDF cannot be retrieved, or merging fails.

## Security notes

- Never publish or commit your Discord access token.
- Review exported Discord JSON before sharing it because it can contain message history, user IDs, profile information, and private URLs.
- Review the generated manifest before sharing it because it contains direct Google Docs URLs.
- Store exports and downloaded documents according to the privacy and retention requirements applicable to their contents.
