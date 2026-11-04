# python-pptx and JSON payloads are untyped; strict "unknown type" checks add noise here only.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
"""Document parsing (ARCHITECTURE section 10.1): bytes in, titled sections out.

Each section keeps its heading path and page, which citations show later. These parsers are
pure Python (pypdf, python-docx, openpyxl, python-pptx); Docling can replace them behind the same
`parse()` signature where layout-heavy PDFs need it (ADR 0007).
"""

import csv
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePath
from typing import Any

MIME_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
ROWS_PER_SECTION = 25


class UnsupportedFormatError(ValueError):
    pass


@dataclass
class Section:
    heading_path: tuple[str, ...]
    text: str
    page: int | None = None


@dataclass
class ParsedDocument:
    title: str
    sections: list[Section] = field(default_factory=list[Section])

    @property
    def text_length(self) -> int:
        return sum(len(s.text) for s in self.sections)


def detect_mime(filename: str, declared: str | None = None) -> str:
    by_extension = MIME_TYPES.get(PurePath(filename).suffix.lower())
    if by_extension:
        return by_extension
    if declared and declared in MIME_TYPES.values():
        return declared
    raise UnsupportedFormatError(f"unsupported file type: {filename}")


def parse(data: bytes, filename: str, mime: str) -> ParsedDocument:
    fallback_title = PurePath(filename).stem.replace("_", " ").replace("-", " ").strip()
    parser = {
        "text/markdown": _markdown,
        "text/plain": _plain,
        "text/html": _html,
        "text/csv": _csv,
        "application/json": _slack_export,
        "application/pdf": _pdf,
        MIME_TYPES[".docx"]: _docx,
        MIME_TYPES[".xlsx"]: _xlsx,
        MIME_TYPES[".pptx"]: _pptx,
    }.get(mime)
    if parser is None:
        raise UnsupportedFormatError(f"unsupported type {mime}")
    document = parser(data, fallback_title)
    document.sections = [s for s in document.sections if s.text.strip()]
    return document


def _text(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _markdown(data: bytes, fallback_title: str) -> ParsedDocument:
    title, stack, lines = "", list[str](), list[str]()
    sections: list[Section] = []

    def flush() -> None:
        if "".join(lines).strip():
            sections.append(Section(tuple(stack), "\n".join(lines).strip()))
        lines.clear()

    for line in _text(data).split("\n"):
        heading = _HEADING.match(line)
        if heading:
            flush()
            level, name = len(heading.group(1)), heading.group(2).strip("# ").strip()
            if level == 1 and not title:
                title = name
                continue
            del stack[max(0, level - 2) :]
            stack.append(name)
        else:
            lines.append(line)
    flush()
    return ParsedDocument(title or fallback_title, sections)


def _plain(data: bytes, fallback_title: str) -> ParsedDocument:
    return ParsedDocument(fallback_title, [Section((), _text(data).strip())])


_HTML_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
_HTML_BREAKS = frozenset({"p", "div", "li", "br", "tr", "section", "article"})


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in _HTML_HEADINGS:
            self.parts.append("\n" + "#" * _HTML_HEADINGS[tag] + " ")
        elif tag in _HTML_BREAKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip -= 1
        elif tag in _HTML_HEADINGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html(data: bytes, fallback_title: str) -> ParsedDocument:
    extractor = _HtmlText()
    extractor.feed(_text(data))
    return _markdown("".join(extractor.parts).encode(), fallback_title)


def _table_sections(name: str, rows: list[list[str]]) -> list[Section]:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:] or [[]]
    sections: list[Section] = []
    for start in range(0, len(body), ROWS_PER_SECTION):
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(row) + " |" for row in body[start : start + ROWS_PER_SECTION]]
        sections.append(Section((name,), "\n".join(lines)))  # header repeated in every part
    return sections


def _csv(data: bytes, fallback_title: str) -> ParsedDocument:
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(_text(data)))]
    return ParsedDocument(fallback_title, _table_sections(fallback_title, rows))


def _slack_export(data: bytes, fallback_title: str) -> ParsedDocument:
    """A Slack channel export: {"channel": ..., "messages": [{user, text, ts, thread_ts}]} or a
    bare message list. One section per thread, so a conversation stays together."""
    raw: Any = json.loads(_text(data))
    channel = str(raw.get("channel", fallback_title)) if isinstance(raw, dict) else fallback_title
    channel = channel.lstrip("#")
    entries: Any = raw.get("messages", []) if isinstance(raw, dict) else raw
    messages: list[dict[str, Any]] = (
        [m for m in entries if isinstance(m, dict)] if isinstance(entries, list) else []
    )
    threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for message in messages:
        threads[str(message.get("thread_ts") or message.get("ts"))].append(message)
    sections = []
    for thread in threads.values():
        thread.sort(key=lambda m: float(m.get("ts", 0)))
        first = thread[0]
        lines = [
            f"{m.get('user_name') or m.get('user', 'someone')}: {m.get('text', '')}" for m in thread
        ]
        sections.append(
            Section(
                (f"#{channel}", f"thread started {first.get('date', first.get('ts'))}"),
                "\n".join(lines),
            )
        )
    return ParsedDocument(f"Slack #{channel}", sections)


def _pdf(data: bytes, fallback_title: str) -> ParsedDocument:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    title = (
        reader.metadata.title if reader.metadata and reader.metadata.title else ""
    ) or fallback_title
    sections = [
        Section((), (page.extract_text() or "").strip(), page=number)
        for number, page in enumerate(reader.pages, start=1)
    ]
    return ParsedDocument(title, sections)


def _docx(data: bytes, fallback_title: str) -> ParsedDocument:
    import docx

    document = docx.Document(io.BytesIO(data))
    title = document.core_properties.title or ""
    stack: list[str] = []
    sections: list[Section] = []
    lines: list[str] = []

    def flush() -> None:
        if "".join(lines).strip():
            sections.append(Section(tuple(stack), "\n\n".join(lines).strip()))
        lines.clear()

    for paragraph in document.paragraphs:
        style = (paragraph.style.name or "") if paragraph.style is not None else ""
        text = paragraph.text.strip()
        if not text:
            continue
        if style == "Title":
            title = title or text
        elif style.startswith("Heading"):
            flush()
            level = int(style.removeprefix("Heading").strip() or 1)
            del stack[max(0, level - 1) :]
            stack.append(text)
        else:
            lines.append(f"- {text}" if "List" in style else text)
    flush()
    for index, table in enumerate(document.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        sections += _table_sections(f"Table {index}", rows)
    return ParsedDocument(title or fallback_title, sections)


def _xlsx(data: bytes, fallback_title: str) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sections: list[Section] = []
    for sheet in workbook.worksheets:
        rows = [
            ["" if v is None else str(v) for v in row] for row in sheet.iter_rows(values_only=True)
        ]
        sections += _table_sections(sheet.title, rows)
    title = workbook.properties.title or fallback_title
    workbook.close()
    return ParsedDocument(title, sections)


def _pptx(data: bytes, fallback_title: str) -> ParsedDocument:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(data))
    sections: list[Section] = []
    for number, slide in enumerate(presentation.slides, start=1):
        heading = (
            slide.shapes.title.text_frame.text.strip() if slide.shapes.title is not None else ""
        )
        texts = [
            shape.text_frame.text.strip()
            for shape in slide.shapes
            if shape.has_text_frame
            and shape != slide.shapes.title
            and shape.text_frame.text.strip()
        ]
        sections.append(
            Section(
                (f"Slide {number}: {heading}" if heading else f"Slide {number}",),
                "\n".join(texts),
                page=number,
            )
        )
    return ParsedDocument(presentation.core_properties.title or fallback_title, sections)
