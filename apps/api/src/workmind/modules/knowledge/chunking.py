"""Structure-aware chunking (ARCHITECTURE section 10.1).

Chunks never cross a section, so every chunk has one heading path and one page for its
citation. Paragraphs are packed up to a token budget; oversized paragraphs split at sentences.
"""

import re
from dataclasses import dataclass

from workmind.modules.knowledge.parsing import ParsedDocument

MAX_TOKENS = 320
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    text: str
    heading_path: tuple[str, ...]
    page: int | None
    token_count: int


def estimate_tokens(text: str) -> int:
    """A conservative budget estimate, never used for billing: ~1.3 tokens per English word,
    or one per 3.2 characters for tables, numbers and code, whichever is larger."""
    return max(1, round(len(text.split()) * 1.3), round(len(text) / 3.2))


def _pieces(paragraph: str, max_tokens: int) -> list[str]:
    if estimate_tokens(paragraph) <= max_tokens:
        return [paragraph]
    pieces, current = list[str](), list[str]()
    for sentence in _SENTENCE.split(paragraph):
        words = sentence.split()
        while estimate_tokens(" ".join(words)) > max_tokens:  # a single huge "sentence"
            cut = max(1, min(int(max_tokens / 1.3), len(words) // 2 or 1))
            pieces.append(" ".join(words[:cut]))
            words = words[cut:]
        sentence = " ".join(words)
        if current and estimate_tokens(" ".join([*current, sentence])) > max_tokens:
            pieces.append(" ".join(current))
            current = []
        if sentence:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _table_pieces(table: str, max_tokens: int) -> list[str]:
    """Rows grouped within the budget; every piece repeats the header, so it reads on its own."""
    lines = table.strip().split("\n")
    header, rows = lines[:2], lines[2:]
    pieces: list[str] = []
    current: list[str] = []
    for row in rows:
        if current and estimate_tokens("\n".join([*header, *current, row])) > max_tokens:
            pieces.append("\n".join([*header, *current]))
            current = []
        current.append(row)
    pieces.append("\n".join([*header, *current]))
    return pieces


def chunk_document(document: ParsedDocument, max_tokens: int = MAX_TOKENS) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for section in document.sections:
        is_table = section.text.lstrip().startswith("|")
        if is_table:
            pieces = _table_pieces(section.text, max_tokens)
        else:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section.text) if p.strip()]
            pieces = [piece for p in paragraphs for piece in _pieces(p, max_tokens)]

        groups: list[list[str]] = [[]]
        for piece in pieces:
            current = groups[-1]
            # Table pieces are already sized and each carries its header: one chunk each.
            if current and (
                is_table or estimate_tokens("\n\n".join([*current, piece])) > max_tokens
            ):
                groups.append([piece])
            else:
                current.append(piece)
        for group in groups:
            if group:
                text = "\n\n".join(group)
                drafts.append(
                    ChunkDraft(
                        len(drafts), text, section.heading_path, section.page, estimate_tokens(text)
                    )
                )
    return drafts


def embedding_text(heading_path: tuple[str, ...] | list[str], text: str) -> str:
    """What gets embedded: the heading path gives the chunk its context ("contextual chunk
    header"); the stored and displayed text stays clean."""
    return f"{' > '.join(heading_path)}\n{text}" if heading_path else text
