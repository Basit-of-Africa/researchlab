"""Section-aware chunking that preserves headings and page locations."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.services.extraction import REF_HEADINGS, Block

TARGET_CHARS = 1400
MAX_CHARS = 2200
OVERLAP_SENTENCES = 1


@dataclass
class Chunk:
    index: int
    text: str
    section: str
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256((self.section + "\n" + self.text).encode()).hexdigest()

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.text) // 4)


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    sents = _SENT.split(text)
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) > TARGET_CHARS and cur:
            out.append(cur.strip())
            cur = ""
        while len(s) > MAX_CHARS:  # pathological sentence
            out.append(s[:MAX_CHARS])
            s = s[MAX_CHARS:]
        cur += s + " "
    if cur.strip():
        out.append(cur.strip())
    return out


def chunk_blocks(blocks: list[Block], include_references: bool = False) -> list[Chunk]:
    chunks: list[Chunk] = []
    section_path: list[str] = []
    buf: list[str] = []
    buf_pages: list[int] = []
    offset = 0
    buf_start = 0
    in_refs = False

    def flush() -> None:
        nonlocal buf, buf_pages, buf_start
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        if text:
            pages = [p for p in buf_pages if p is not None]
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=text,
                    section=" > ".join(section_path),
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    char_start=buf_start,
                    char_end=buf_start + len(text),
                )
            )
            # carry a sentence of overlap for context continuity
            tail = _SENT.split(text)[-OVERLAP_SENTENCES:]
            overlap = " ".join(tail)
            buf = [overlap] if len(overlap) < 400 else []
            buf_pages = buf_pages[-1:] if buf else []
        else:
            buf, buf_pages = [], []
        buf_start = offset

    for b in blocks:
        if b.is_heading:
            flush()
            buf, buf_pages = [], []
            in_refs = bool(REF_HEADINGS.match(b.text))
            lvl = max(1, min(b.level, 4))
            section_path = section_path[: lvl - 1] + [b.text]
            offset += len(b.text) + 2
            buf_start = offset
            continue
        if in_refs and not include_references:
            offset += len(b.text) + 2
            continue
        for piece in _split_long(b.text):
            if sum(len(x) for x in buf) + len(piece) > TARGET_CHARS and buf:
                flush()
            buf.append(piece)
            buf_pages.append(b.page)  # type: ignore[arg-type]
            offset += len(piece) + 2
    flush()
    return chunks
