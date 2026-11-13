# Development tooling over untyped libraries (python-pptx, reportlab).
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""python -m needwise.seed.corpus [--plan PATH] [--out DIR] [--only TEXT] [--force]

Writes the demo corpus (seed/acme/corpus_plan.yaml) with the configured LLM's "generator" role,
then converts each document to its planned format (PDF, DOCX, XLSX, PPTX, Markdown, Slack JSON).
Every number, ID and e-mail address from a document's facts is checked in the output; a document
missing one is regenerated once with that feedback, and reported if it still fails.

Generated files are committed to the repository, so seeding never needs an API key.
"""

import argparse
import asyncio
import io
import json
import re
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from needwise.core.config import get_settings
from needwise.providers.factory import create_llm
from needwise.providers.llm import LLMMessage, LLMProvider, LLMRequest

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_PLAN = REPO_ROOT / "seed" / "acme" / "corpus_plan.yaml"
DEFAULT_OUT = REPO_ROOT / "seed" / "acme" / "corpus"

SYSTEM = """You write realistic internal documents for a fictional company, for a software demo.

Rules:
- Output only the document, in GitHub-flavoured Markdown, starting with "# <title>".
- Use "##" and "###" headings, lists and tables where a real document would.
- State every listed fact. You may rephrase sentences, but copy every number, date, name, ID and
  e-mail address exactly as given, and never invent a value that contradicts a fact.
- Keep it believable and specific: 250 to 650 words. No placeholder text, no "Lorem ipsum".
- Everything is fictional; do not mention that it is fictional inside the document."""

FORMAT_GUIDANCE = {
    "xlsx": (
        "This becomes a spreadsheet: put the data in Markdown tables, each under a "
        "'## <Sheet name>' heading of at most 31 characters (Excel's limit). Notes go in a "
        "short paragraph under the table."
    ),
    "pptx": (
        "This becomes a slide deck: one '## <Slide title>' section per slide "
        "with 2 to 5 short bullets."
    ),
}


class SlackMessage(BaseModel):
    user_name: str
    text: str
    ts: str  # seconds since epoch with decimals, as Slack exports them
    thread_ts: str | None
    date: str  # ISO date and time


class SlackExport(BaseModel):
    channel: str
    messages: list[SlackMessage]


@dataclass
class PlannedDocument:
    file: str
    title: str
    brief: str
    facts: list[str]
    format: str = ""
    trust: str | None = None
    sensitivity: str | None = None
    # The document's own access list (docs/permission-sync.md), company.yaml syntax.
    viewers: list[str] | None = None

    def __post_init__(self) -> None:
        self.format = self.format or PurePath(self.file).suffix.lstrip(".")


@dataclass
class Outcome:
    document: PlannedDocument
    missing: list[str] = field(default_factory=list[str])
    error: str | None = None


# --- Fact checking ------------------------------------------------------------------------

_VALUE = re.compile(r"CANARY-[A-Z0-9-]+|[\w.+-]+@[\w-]+\.[\w.]+|\d+(?:[.,]\d+)*")


def required_values(facts: list[str]) -> set[str]:
    return {m.group(0).rstrip(".,") for fact in facts for m in _VALUE.finditer(fact)}


def _normalize(text: str) -> str:
    return re.sub(r"(?<=\d)[ ,](?=\d{3}\b)", "", text)  # 182,400 == 182 400 == 182400


def missing_values(markdown: str, facts: list[str]) -> list[str]:
    haystack = _normalize(markdown)
    return sorted(v for v in required_values(facts) if _normalize(v) not in haystack)


# --- Writing ------------------------------------------------------------------------------


def _prompt(context: str, doc: PlannedDocument, feedback: str = "") -> LLMRequest:
    facts = "\n".join(f"- {fact}" for fact in doc.facts)
    text = (
        f"Company context:\n{context}\n\nDocument title: {doc.title}\nWhat it is: {doc.brief}\n\n"
        f"Facts it must state:\n{facts}\n\n{FORMAT_GUIDANCE.get(doc.format, '')}{feedback}"
    )
    return LLMRequest(system=SYSTEM, messages=[LLMMessage("user", text)], max_tokens=8000)


async def write_document(
    llm: LLMProvider, context: str, doc: PlannedDocument
) -> tuple[str | SlackExport, list[str]]:
    if doc.format == "slack":
        export, _ = await llm.structured("generator", _prompt(context, doc), SlackExport)
        return export, missing_values(json.dumps(export.model_dump()), doc.facts)
    markdown, _ = await llm.complete("generator", _prompt(context, doc))
    missing = missing_values(markdown, doc.facts)
    if missing:  # one retry with precise feedback
        feedback = (
            "\n\nYour previous version left out these exact values; include all of them: "
            + ", ".join(missing)
        )
        markdown, _ = await llm.complete("generator", _prompt(context, doc, feedback))
        missing = missing_values(markdown, doc.facts)
    return markdown, missing


# --- Markdown to file formats -------------------------------------------------------------


@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | number | table
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list[list[str]])


def markdown_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []
    table: list[list[str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", " ".join(paragraph)))
            paragraph.clear()
        if table:
            rows = [r for r in table if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
            blocks.append(Block("table", rows=rows))
            table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            if paragraph:
                flush()
            table.append([c.strip() for c in stripped.strip("|").split("|")])
            continue
        if table:
            flush()
        if not stripped:
            flush()
        elif heading := re.match(r"^(#{1,6})\s+(.*)", stripped):
            flush()
            blocks.append(Block("heading", _plain(heading.group(2)), level=len(heading.group(1))))
        elif bullet := re.match(r"^[-*]\s+(.*)", stripped):
            flush()
            blocks.append(Block("bullet", _plain(bullet.group(1))))
        elif number := re.match(r"^\d+[.)]\s+(.*)", stripped):
            flush()
            blocks.append(Block("number", _plain(number.group(1))))
        else:
            paragraph.append(_plain(stripped))
    flush()
    return blocks


def _plain(text: str) -> str:
    return re.sub(
        r"\*\*(.+?)\*\*|__(.+?)__|`(.+?)`", lambda m: next(g for g in m.groups() if g), text
    )


def to_docx(markdown: str, title: str) -> bytes:
    import docx

    document = docx.Document()
    document.core_properties.title = title
    for block in markdown_blocks(markdown):
        if block.kind == "heading":
            document.add_heading(
                block.text, level=0 if block.level == 1 else min(block.level - 1, 3)
            )
        elif block.kind == "bullet":
            document.add_paragraph(block.text, style="List Bullet")
        elif block.kind == "number":
            document.add_paragraph(block.text, style="List Number")
        elif block.kind == "table" and block.rows:
            width = max(len(r) for r in block.rows)
            table = document.add_table(rows=len(block.rows), cols=width)
            table.style = "Light Grid Accent 1"
            for i, row in enumerate(block.rows):
                for j, cell in enumerate(row):
                    table.cell(i, j).text = _plain(cell)
        elif block.kind == "paragraph":
            document.add_paragraph(block.text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def to_pdf(markdown: str, title: str) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story: list[Any] = []
    for block in markdown_blocks(markdown):
        if block.kind == "heading":
            style = (
                styles["Title"]
                if block.level == 1
                else styles["Heading2" if block.level == 2 else "Heading3"]
            )
            story.append(Paragraph(escape(block.text), style))
        elif block.kind in ("bullet", "number"):
            story.append(Paragraph("&bull; " + escape(block.text), styles["BodyText"]))
        elif block.kind == "table" and block.rows:
            width = max(len(r) for r in block.rows)
            rows = [
                [
                    Paragraph(escape(_plain(c)), styles["BodyText"])
                    for c in r + [""] * (width - len(r))
                ]
                for r in block.rows
            ]
            table = Table(rows, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ]
                )
            )
            story += [table, Spacer(1, 8)]
        elif block.kind == "paragraph":
            story.append(Paragraph(escape(block.text), styles["BodyText"]))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, title=title, author="Acme Robotics").build(story)
    return buffer.getvalue()


SHEET_NAME_MAX = 31  # Excel's limit for a worksheet name
# Written into every generated workbook instead of the time it was made, so the same Markdown
# always gives the same bytes (and re-seeding an unchanged file is a no-op, not a new version).
REPRODUCIBLE_TIME = datetime(2026, 1, 1)


def sheet_name(heading: str, taken: Iterable[str] = ()) -> str:
    """A worksheet name from a '## heading'. Excel forbids []:*?/\\ and allows 31 characters:
    a longer heading is shortened at a word boundary (never mid-word), and a name already in
    use (Excel compares them ignoring case) gets a number."""
    name = " ".join(re.sub(r"[\[\]:*?/\\]", " ", heading).split()) or "Sheet"
    if len(name) > SHEET_NAME_MAX:
        words = name[: SHEET_NAME_MAX + 1].rsplit(" ", 1)[0].rstrip(",;-")
        name = words if 0 < len(words) <= SHEET_NAME_MAX else name[:SHEET_NAME_MAX]
    used = {t.lower() for t in taken}
    unique, number = name, 2
    while unique.lower() in used:
        suffix = f" {number}"
        unique = name[: SHEET_NAME_MAX - len(suffix)].rstrip() + suffix
        number += 1
    return unique


def _fixed_zip_times(data: bytes) -> bytes:
    """Zip entries record when they were written: the same fixed time for every entry."""
    out = io.BytesIO()
    stamp = REPRODUCIBLE_TIME.timetuple()[:6]
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            fixed = zipfile.ZipInfo(entry.filename, date_time=stamp)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(fixed, source.read(entry))
    return out.getvalue()


def to_xlsx(markdown: str, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.writer.excel import ExcelWriter

    workbook = Workbook()
    workbook.properties.title = title
    workbook.properties.created = workbook.properties.modified = REPRODUCIBLE_TIME
    default = workbook.active
    sheet = None
    heading = "Sheet1"
    for block in markdown_blocks(markdown):
        if block.kind == "heading" and block.level >= 2:
            heading = block.text
        elif block.kind == "table":
            sheet = workbook.create_sheet(sheet_name(heading, workbook.sheetnames))
            for row in block.rows:
                sheet.append([_cell(c) for c in row])
        elif block.kind in ("paragraph", "bullet", "number") and sheet is not None:
            sheet.append([])
            sheet.append([block.text])
    if default is not None and len(workbook.sheetnames) > 1:
        workbook.remove(default)
    buffer = io.BytesIO()
    # Workbook.save() stamps the current time as "modified"; its writer keeps ours.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        ExcelWriter(workbook, archive).save()
    return _fixed_zip_times(buffer.getvalue())


def _cell(value: str) -> str | int | float:
    text = _plain(value).strip()
    plain = text.replace(",", "")
    if re.fullmatch(r"-?\d+", plain):
        return int(plain)
    if re.fullmatch(r"-?\d+\.\d+", plain):
        return float(plain)
    return text


def to_pptx(markdown: str, title: str) -> bytes:
    from pptx import Presentation

    presentation = Presentation()
    presentation.core_properties.title = title
    cover = presentation.slides.add_slide(presentation.slide_layouts[0])
    cover.shapes.title.text = title  # pyright: ignore[reportOptionalMemberAccess]
    cover.placeholders[1].text = "Acme Robotics"  # pyright: ignore[reportAttributeAccessIssue]
    slide = None
    for block in markdown_blocks(markdown):
        if block.kind == "heading" and block.level == 2:
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = block.text  # pyright: ignore[reportOptionalMemberAccess]
            slide.placeholders[1].text_frame.clear()  # pyright: ignore[reportAttributeAccessIssue]
        elif slide is not None and block.kind in ("bullet", "number", "paragraph"):
            frame = slide.placeholders[1].text_frame  # pyright: ignore[reportAttributeAccessIssue]
            paragraph = (
                frame.paragraphs[0] if not frame.paragraphs[0].text else frame.add_paragraph()
            )
            paragraph.text = block.text
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def render(doc: PlannedDocument, content: str | SlackExport) -> bytes:
    if isinstance(content, SlackExport):
        return json.dumps(content.model_dump(), indent=2).encode()
    converters = {"docx": to_docx, "pdf": to_pdf, "xlsx": to_xlsx, "pptx": to_pptx}
    converter = converters.get(doc.format)
    return converter(content, doc.title) if converter else content.encode()


# --- Orchestration ------------------------------------------------------------------------


def load_plan(path: Path) -> tuple[str, list[PlannedDocument]]:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    return str(raw["company"]["context"]), [PlannedDocument(**d) for d in raw["documents"]]


async def generate(
    plan: Path, out: Path, only: str | None, force: bool, concurrency: int = 6
) -> list[Outcome]:
    context, documents = load_plan(plan)
    selected = [
        d
        for d in documents
        if (only is None or only in d.file) and (force or not (out / d.file).exists())
    ]
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as http:
        llm = create_llm(get_settings(), http)

        async def one(doc: PlannedDocument) -> Outcome:
            async with semaphore:
                try:
                    content, missing = await write_document(llm, context, doc)
                    target = out / doc.file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(render(doc, content))
                    print(
                        f"  wrote {doc.file}"
                        + (f"  (missing: {', '.join(missing)})" if missing else "")
                    )
                    return Outcome(doc, missing)
                except Exception as exc:
                    print(f"  FAILED {doc.file}: {exc}", file=sys.stderr)
                    return Outcome(doc, error=str(exc))

        outcomes = await asyncio.gather(*(one(d) for d in selected))
    manifest = [
        {
            "file": d.file,
            "title": d.title,
            **({"trust": d.trust} if d.trust else {}),
            **({"sensitivity": d.sensitivity} if d.sensitivity else {}),
            **({"viewers": d.viewers} if d.viewers is not None else {}),
        }
        for d in documents
        if (out / d.file).exists()
    ]
    (out / "manifest.yaml").write_text(
        "# Generated by `python -m needwise.seed.corpus`.\n"
        "# Seeding loads these files into their collections.\n"
        + yaml.safe_dump({"documents": manifest}, sort_keys=False, allow_unicode=True)
    )
    return list(outcomes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m needwise.seed.corpus", description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", help="only documents whose path contains this text")
    parser.add_argument("--force", action="store_true", help="regenerate existing files")
    args = parser.parse_args(argv)
    outcomes = asyncio.run(generate(args.plan, args.out, args.only, args.force))
    failed = [o for o in outcomes if o.error or o.missing]
    print(f"Generated {len(outcomes) - len(failed)} of {len(outcomes)} documents cleanly.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
