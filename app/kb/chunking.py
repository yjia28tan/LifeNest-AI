"""Split knowledge/*.md into retrievable chunks: one per `##` section, split further above ~800 tokens."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.kb.store import KBChunk

MAX_TOKENS = 800
_TOKENS_PER_WORD = 1.3  # rough English average; good enough to decide when to split


def parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path.name}: missing YAML front matter")
    return yaml.safe_load(match.group(1)), match.group(2)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * _TOKENS_PER_WORD) + 1


def _split_paragraphs(body: str, max_tokens: int) -> list[str]:
    parts, current = [], ""
    for para in [p.strip() for p in body.split("\n\n") if p.strip()]:
        candidate = f"{current}\n\n{para}" if current else para
        if current and _approx_tokens(candidate) > max_tokens:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def chunk_document(meta: dict[str, Any], body: str, max_tokens: int = MAX_TOKENS) -> list[KBChunk]:
    sections = re.split(r"^## +(.+)$", body, flags=re.MULTILINE)
    chunks: list[KBChunk] = []
    # re.split yields [preamble, title1, body1, title2, body2, ...]
    for title, text in zip(sections[1::2], sections[2::2], strict=True):
        section = title.strip()
        parts = _split_paragraphs(text.strip(), max_tokens)
        for i, content in enumerate(parts, start=1):
            suffix = f"-{i}" if len(parts) > 1 else ""
            chunks.append(KBChunk(
                chunk_id=f"{meta['doc_id']}--{_slug(section)}{suffix}",
                doc_id=meta["doc_id"],
                title=meta["title"],
                section=section,
                category=meta["category"],
                content=content,
            ))
    return chunks


def load_knowledge(directory: Path) -> list[KBChunk]:
    chunks: list[KBChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_document(*parse_markdown(path)))
    return chunks
