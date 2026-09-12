#!/usr/bin/env python3
"""Create a chapterized EPUB and PDF from Google Docs links in a Discord JSON export."""

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests
from ebooklib import ITEM_DOCUMENT, epub
from lxml import etree

CALIBRE_DOWNLOAD_URL = "https://calibre-ebook.com/download"

GOOGLE_DOC_PATTERN = re.compile(
    r"https?://docs\.google\.com/document/d/"
    r"(?P<document_id>[A-Za-z0-9_-]+)"
    r"(?:/[^\s\"'<>&,]*)?",
    re.IGNORECASE,
)

LABELED_DOC_PATTERN = re.compile(
    r"(?P<label>[A-Za-z0-9][A-Za-z0-9 _.'()/&-]{0,100}?)"
    r"(?:\s*:\s*|\s+)"
    r"(?P<url>https?://docs\.google\.com/document/d/"
    r"(?P<document_id>[A-Za-z0-9_-]+)"
    r"(?:/[^\s\"'<>&,]*)?)",
    re.IGNORECASE,
)

TAG_PATTERN = re.compile(r"<[^>]*>")
ALLOWED_ELEMENTS = {
    "p",
    "div",
    "span",
    "br",
    "hr",
    "em",
    "strong",
    "b",
    "i",
    "u",
    "blockquote",
    "pre",
    "code",
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "sup",
    "sub",
    "a",
}


def decode_exported_text(value: str) -> str:
    """Decode nested HTML entities and escaped whitespace."""
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    return value.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")


def plain_text(value: str) -> str:
    """Convert exported HTML-like text to normalized plain text."""
    value = TAG_PATTERN.sub(" ", decode_exported_text(value))
    return re.sub(r"[ \t]+", " ", value)


def clean_label(label: str) -> str:
    """Normalize whitespace and punctuation around a label."""
    label = re.sub(r"\s+", " ", html.unescape(label)).strip()
    return label.strip(" ,;\"'")


def make_document(label: str, document_id: str) -> dict[str, str]:
    """Create the normalized record for one Google Doc."""
    return {
        "label": clean_label(label) or document_id,
        "document_id": document_id,
        "url": f"https://docs.google.com/document/d/{document_id}/edit",
        "epub_url": (
            f"https://docs.google.com/document/d/{document_id}/export?format=epub"
        ),
    }


def extract_from_embeds(embeds: list[object]) -> list[dict[str, str]]:
    """Extract Google Docs from embeds, preferring the embed title as label."""
    documents: list[dict[str, str]] = []
    for embed in embeds:
        if not isinstance(embed, dict):
            continue
        title = embed.get("title")
        url_value = embed.get("url")
        if not isinstance(url_value, str):
            continue
        match = GOOGLE_DOC_PATTERN.search(decode_exported_text(url_value))
        if not match:
            continue
        document_id = match.group("document_id")
        label = title if isinstance(title, str) and title.strip() else document_id
        documents.append(make_document(label, document_id))
    return documents


def extract_from_message_content(content: str, message_id: str) -> list[dict[str, str]]:
    """Extract links from message content when the message has no embeds."""
    content = plain_text(content)
    documents: list[dict[str, str]] = []
    matched_ids: set[str] = set()

    for match in LABELED_DOC_PATTERN.finditer(content):
        document_id = match.group("document_id")
        matched_ids.add(document_id)
        documents.append(make_document(match.group("label"), document_id))

    bare_number = 0
    for match in GOOGLE_DOC_PATTERN.finditer(content):
        document_id = match.group("document_id")
        if document_id in matched_ids:
            continue
        bare_number += 1
        suffix = f" #{bare_number}" if bare_number > 1 else ""
        documents.append(make_document(f"Message {message_id}{suffix}", document_id))
        matched_ids.add(document_id)

    return documents


def extract_documents(input_path: Path) -> list[dict[str, str]]:
    """Extract unique documents, preferring embeds on a per-message basis."""
    with input_path.open("r", encoding="utf-8-sig") as input_file:
        data = json.load(input_file)

    messages = data.get("messages")
    if not isinstance(messages, list):
        raise TypeError('The JSON must contain a top-level "messages" array.')

    documents: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        embeds = message.get("embeds")
        if isinstance(embeds, list) and embeds:
            candidates = extract_from_embeds(embeds)
        else:
            content = message.get("content")
            candidates = (
                extract_from_message_content(content, str(message.get("id", "unknown")))
                if isinstance(content, str)
                else []
            )
        for document in candidates:
            if document["document_id"] not in seen_ids:
                seen_ids.add(document["document_id"])
                documents.append(document)
    return documents


def safe_filename(label: str) -> str:
    """Create a safe filename component from a document label."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label)
    label = re.sub(r"\s+", " ", label).strip(" ._")
    return label or "document"


def write_manifest(documents: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(documents, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def is_valid_epub(path: Path) -> bool:
    """Return True when a file has the required EPUB ZIP structure."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" not in names or "META-INF/container.xml" not in names:
                return False
            mime = archive.read("mimetype").decode("ascii").strip()
            if mime != "application/epub+zip":
                return False
        epub.read_epub(str(path))
        return True
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile, KeyError):
        return False


def get_or_download_epub(
    session: requests.Session,
    document: dict[str, str],
    epub_path: Path,
) -> Path:
    """Reuse a valid stored EPUB, otherwise download the native Google Docs EPUB."""
    label = document["label"]
    if is_valid_epub(epub_path):
        print(f'Reusing existing EPUB for "{label}": {epub_path}')
        return epub_path

    if epub_path.exists():
        print(f'Replacing invalid EPUB for "{label}": {epub_path}')
        epub_path.unlink()
    else:
        print(f'Downloading EPUB for "{label}"...')

    response = session.get(document["epub_url"], timeout=120, allow_redirects=True)
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        content_type = response.headers.get("Content-Type", "unknown")
        raise RuntimeError(
            f'Google did not return an EPUB for "{label}" '
            f"(Content-Type: {content_type}). The document may require sign-in."
        )

    epub_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = epub_path.with_suffix(epub_path.suffix + ".part")
    try:
        temporary_path.write_bytes(response.content)
        if not is_valid_epub(temporary_path):
            raise RuntimeError(f'The downloaded EPUB for "{label}" is invalid.')
        temporary_path.replace(epub_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return epub_path


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname.lower()


def sanitize_element(element: etree._Element) -> etree._Element | None:
    """Copy semantic XHTML while removing source-specific styling and assets."""
    name = local_name(element)
    if name not in ALLOWED_ELEMENTS:
        return None

    clean = etree.Element(name)
    clean.text = element.text
    if name == "a":
        href = element.get("href", "")
        if href.startswith(("http://", "https://", "mailto:")):
            clean.set("href", href)

    for child in element:
        copied = sanitize_element(child)
        if copied is not None:
            clean.append(copied)
            copied.tail = child.tail
        elif child.tail:
            if len(clean):
                clean[-1].tail = (clean[-1].tail or "") + child.tail
            else:
                clean.text = (clean.text or "") + child.tail
    return clean


def source_document_items(book: epub.EpubBook):
    """Yield source XHTML items in spine order, then any remaining documents."""
    by_id = {item.get_id(): item for item in book.get_items_of_type(ITEM_DOCUMENT)}
    yielded: set[str] = set()
    for spine_entry in book.spine:
        item_id = spine_entry[0] if isinstance(spine_entry, tuple) else spine_entry
        item = by_id.get(item_id)
        if item is not None:
            yielded.add(item.get_id())
            yield item
    for item in by_id.values():
        if item.get_id() not in yielded:
            yield item


def chapter_content(source_path: Path, label: str) -> bytes:
    """Build one clean XHTML chapter from a native Google Docs EPUB."""
    source_book = epub.read_epub(str(source_path))
    root = etree.Element("div")
    heading = etree.SubElement(root, "h1")
    heading.text = label

    parser = etree.XMLParser(recover=True, resolve_entities=False)
    for item in source_document_items(source_book):
        document = etree.fromstring(item.get_content(), parser=parser)
        bodies = document.xpath('//*[local-name()="body"]')
        if not bodies:
            continue
        for child in bodies[0]:
            copied = sanitize_element(child)
            if copied is not None:
                root.append(copied)

    return etree.tostring(root, encoding="utf-8", method="html")


def create_combined_epub(
    sources: list[tuple[str, Path]],
    output_path: Path,
    title: str,
    language: str,
) -> None:
    """Combine native source EPUBs into one label-chapterized EPUB."""
    book = epub.EpubBook()
    book.set_identifier(f"google-docs-collection-{output_path.stem}")
    book.set_title(title)
    book.set_language(language)

    css = epub.EpubItem(
        uid="book-style",
        file_name="styles/book.css",
        media_type="text/css",
        content=(
            "body { font-family: serif; line-height: 1.5; margin: 5%; }\n"
            "h1 { font-family: sans-serif; break-before: page; "
            "border-bottom: 1px solid #777; padding-bottom: .35em; }\n"
            "p { margin: 0 0 .9em; } blockquote { margin: 1em 2em; }\n"
        ),
    )
    book.add_item(css)
    chapters = []

    for index, (label, source_path) in enumerate(sources, start=1):
        chapter = epub.EpubHtml(
            title=label,
            file_name=f"chapter_{index:03d}.xhtml",
            lang=language,
        )
        chapter.content = chapter_content(source_path, label)
        chapter.add_item(css)
        book.add_item(chapter)
        chapters.append(chapter)

    if not chapters:
        raise RuntimeError("No chapters were available for the EPUB.")

    book.toc = tuple(chapters)
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book, {})

    if not is_valid_epub(output_path):
        raise RuntimeError("The combined EPUB failed validation.")


def find_ebook_convert() -> str:
    """Locate Calibre's ebook-convert command or raise an actionable error."""
    executable = shutil.which("ebook-convert")
    if executable:
        return executable
    raise RuntimeError(
        "Calibre's ebook-convert command was not found in PATH. "
        f"Install Calibre from {CALIBRE_DOWNLOAD_URL}, then ensure ebook-convert "
        "is available in PATH. On macOS it is commonly located at "
        "/Applications/calibre.app/Contents/MacOS/ebook-convert."
    )


def convert_epub_to_pdf(epub_path: Path, pdf_path: Path) -> None:
    """Convert the combined EPUB to a chapterized PDF with Calibre."""
    executable = find_ebook_convert()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        str(epub_path),
        str(pdf_path),
        "--paper-size",
        "a4",
        "--pdf-page-margin-left",
        "50",
        "--pdf-page-margin-right",
        "50",
        "--pdf-page-margin-top",
        "50",
        "--pdf-page-margin-bottom",
        "50",
        "--chapter",
        "//h:h1",
        "--level1-toc",
        "//h:h1",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Calibre failed to create the PDF (exit code {exc.returncode})."
        ) from exc

    if not pdf_path.is_file() or pdf_path.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("Calibre did not produce a valid PDF file.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Google Docs links from Discord JSON, download each document once "
            "as EPUB, assemble a chapterized EPUB, and convert it to PDF with Calibre."
        )
    )
    parser.add_argument("input", type=Path, help="DiscordChatExporter JSON file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("combined_google_docs.pdf"),
        help="Combined PDF path",
    )
    parser.add_argument(
        "--epub-output",
        type=Path,
        default=Path("combined_google_docs.epub"),
        help="Combined EPUB path",
    )
    parser.add_argument(
        "--title",
        default="Google Docs Collection",
        help="Book title used in EPUB and PDF metadata",
    )
    parser.add_argument("--language", default="en", help="EPUB language code")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("google_docs_manifest.json"),
        help="Extracted link manifest path",
    )
    parser.add_argument(
        "--keep-epubs",
        type=Path,
        default=Path("individual_epubs"),
        metavar="DIRECTORY",
        help="Directory used to store and reuse native EPUBs by label",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1

    session = requests.Session()
    session.headers["User-Agent"] = "GoogleDocsBookExporter/2.0"
    try:
        documents = extract_documents(args.input)
        if not documents:
            raise RuntimeError("No Google Docs links were found in the Discord export.")

        print(f"Found {len(documents)} unique document(s):")
        for index, document in enumerate(documents, start=1):
            print(f"  {index}. {document['label']}: {document['url']}")

        write_manifest(documents, args.manifest)
        args.keep_epubs.mkdir(parents=True, exist_ok=True)

        sources: list[tuple[str, Path]] = []
        for document in documents:
            source_path = args.keep_epubs / f"{safe_filename(document['label'])}.epub"
            source_path = get_or_download_epub(session, document, source_path)
            sources.append((document["label"], source_path))

        create_combined_epub(sources, args.epub_output, args.title, args.language)
        print(f"Chapterized EPUB written to: {args.epub_output}")

        convert_epub_to_pdf(args.epub_output, args.output)
        print(f"Chapterized PDF written to: {args.output}")
        print(f"Individual EPUBs stored in: {args.keep_epubs}")
        return 0

    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
        requests.RequestException,
        etree.XMLSyntaxError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
