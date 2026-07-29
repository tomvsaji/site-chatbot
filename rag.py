from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SOURCE_RE = re.compile(r"^Sources?:\s*(https?://\S+)", re.IGNORECASE)
SUPPORTED_SUFFIXES = {".md", ".txt", ".json"}
QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "can",
    "could",
    "did",
    "do",
    "does",
    "explain",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "please",
    "saji",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "tom",
    "vellavoor",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}
BOILERPLATE_LINES = {
    "home",
    "writing",
    "about",
    "read post",
    "contact",
    "reach out",
}


@dataclass(frozen=True)
class Chunk:
    source: str
    text: str
    tokens: tuple[str, ...]
    title: str = ""
    heading: str = ""
    url: str = ""


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in TOKEN_RE.findall(text))


def _read_document(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return text
    return text


def _is_boilerplate(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", line).strip().lower()
    return (
        normalized in BOILERPLATE_LINES
        or normalized.startswith("© ")
        or normalized.startswith("copyright ")
    )


def _document_parts(text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Extract document metadata and paragraph blocks with heading breadcrumbs."""
    title = ""
    url = ""
    headings: list[str] = []
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        body = "\n".join(paragraph).strip()
        paragraph.clear()
        if body:
            blocks.append((" › ".join(headings), body))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        source_match = SOURCE_RE.match(line)
        if source_match:
            url = source_match.group(1).rstrip(".,)")
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            name = heading_match.group(2).strip()
            if level == 1 and not title:
                title = name
                headings.clear()
            else:
                depth = max(1, level - 1)
                headings[:] = headings[: depth - 1]
                headings.append(name)
            continue

        if not line:
            flush()
        elif not _is_boilerplate(line):
            paragraph.append(line)

    flush()
    return title, url, blocks


def _split_block(text: str, max_tokens: int, overlap: int) -> list[str]:
    if len(tokenize(text)) <= max_tokens:
        return [text]

    sentence_units = [
        unit.strip()
        for unit in re.split(r"(?<=[.!?])\s+|\n(?=\S)", text)
        if unit.strip()
    ]
    if len(sentence_units) == 1:
        words = text.split()
        step = max(1, max_tokens - overlap)
        return [
            " ".join(words[start : start + max_tokens])
            for start in range(0, len(words), step)
        ]

    units: list[str] = []
    for unit in sentence_units:
        if len(tokenize(unit)) <= max_tokens:
            units.append(unit)
            continue
        words = unit.split()
        step = max(1, max_tokens - overlap)
        units.extend(
            " ".join(words[start : start + max_tokens])
            for start in range(0, len(words), step)
        )

    parts: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = " ".join([*current, unit])
        if current and len(tokenize(candidate)) > max_tokens:
            parts.append(" ".join(current))
            overlap_words = " ".join(current).split()[-overlap:]
            current = [*overlap_words, unit]
            if len(tokenize(" ".join(current))) > max_tokens:
                current = [unit]
        else:
            current.append(unit)
    if current:
        parts.append(" ".join(current))
    return parts


def _chunk_text(
    text: str, max_tokens: int = 140, overlap: int = 20
) -> tuple[str, str, list[tuple[str, str]]]:
    """Build short Markdown-aware chunks safe for MiniLM's 256-wordpiece limit."""
    title, url, blocks = _document_parts(text)
    chunks: list[tuple[str, str]] = []
    current_heading = ""
    current_parts: list[str] = []

    def render(heading: str, parts: list[str]) -> str:
        prefix = f"# {title}" if title else ""
        if heading:
            prefix = f"{prefix}\n\n## {heading}".strip()
        return f"{prefix}\n\n{' '.join(parts)}".strip()

    def flush() -> None:
        if current_parts:
            chunks.append((current_heading, render(current_heading, current_parts)))
            current_parts.clear()

    for heading, block in blocks:
        for part in _split_block(
            block, max_tokens=max_tokens - 40, overlap=max(5, overlap // 2)
        ):
            if current_parts and heading != current_heading:
                flush()
            if not current_parts:
                current_heading = heading
            candidate = render(heading, [*current_parts, part])
            if current_parts and len(tokenize(candidate)) > max_tokens:
                previous_words = " ".join(current_parts).split()[-overlap:]
                flush()
                current_heading = heading
                current_parts.extend(previous_words)
                if len(tokenize(render(heading, [*current_parts, part]))) > max_tokens:
                    current_parts.clear()
            current_parts.append(part)
    flush()
    return title, url, chunks


def load_chunks(data_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not data_dir.exists():
        return chunks

    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        relative = str(path.relative_to(data_dir))
        title, url, document_chunks = _chunk_text(_read_document(path))
        for heading, text in document_chunks:
            tokens = tokenize(text)
            if tokens:
                chunks.append(
                    Chunk(
                        source=relative,
                        text=text,
                        tokens=tokens,
                        title=title or path.stem,
                        heading=heading,
                        url=url,
                    )
                )
    return chunks


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.avg_length = (
            sum(len(chunk.tokens) for chunk in chunks) / len(chunks) if chunks else 1.0
        )
        self.document_frequency: Counter[str] = Counter()
        for chunk in chunks:
            self.document_frequency.update(set(chunk.tokens))

    def search(self, query: str, limit: int = 5) -> list[tuple[Chunk, float]]:
        query_tokens = tuple(
            token for token in tokenize(query) if token not in QUERY_STOPWORDS
        )
        if not query_tokens or not self.chunks:
            return []

        scores: list[tuple[Chunk, float]] = []
        total = len(self.chunks)
        k1 = 1.5
        b = 0.75

        for chunk in self.chunks:
            counts = Counter(chunk.tokens)
            score = 0.0
            for token in query_tokens:
                frequency = counts[token]
                if not frequency:
                    continue
                documents_with_token = self.document_frequency[token]
                inverse_frequency = math.log(
                    1 + (total - documents_with_token + 0.5) / (documents_with_token + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(chunk.tokens) / self.avg_length
                )
                score += inverse_frequency * frequency * (k1 + 1) / denominator
            if score > 0:
                scores.append((chunk, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:limit]
