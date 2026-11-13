"""BM25 sparse vectors for keyword search inside Qdrant (ARCHITECTURE section 11).

Documents get BM25 term-frequency weights; Qdrant's IDF modifier supplies the inverse document
frequency at query time, so the combination is classic BM25. Computed locally, so keyword
search works with any embedding provider. Exact identifiers (INC-2026-031, project names) are
what dense embeddings miss and this catches.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

_TOKEN = re.compile(r"[\w][\w\-./]*[\w]|\w", re.UNICODE)
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "about",
        "any",
        "all",
        "not",
        "no",
    ]
)
K1, B, AVERAGE_LENGTH = 1.2, 0.75, 220.0


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]

    def as_qdrant(self) -> dict[str, list[int] | list[float]]:
        return {"indices": self.indices, "values": self.values}


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token in STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        tokens.append(token)
        if any(c in token for c in "-./"):  # also index the parts of "inc-2026-031"
            tokens += [part for part in re.split(r"[-./]", token) if len(part) > 1]
    return tokens


def _term_index(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")


def _vector(weights: dict[int, float]) -> SparseVector:
    indices = sorted(weights)
    return SparseVector(indices=indices, values=[weights[i] for i in indices])


def encode_document(text: str) -> SparseVector:
    tokens = tokenize(text)
    length_norm = K1 * (1 - B + B * len(tokens) / AVERAGE_LENGTH)
    weights: dict[int, float] = {}
    for token, tf in Counter(tokens).items():
        index = _term_index(token)  # rare hash collisions merge two terms; harmless at this size
        weights[index] = weights.get(index, 0.0) + tf * (K1 + 1) / (tf + length_norm)
    return _vector(weights)


def encode_query(text: str) -> SparseVector:
    return _vector({_term_index(token): 1.0 for token in set(tokenize(text))})
