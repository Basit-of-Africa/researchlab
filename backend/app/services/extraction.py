"""Text and metadata extraction for PDF, DOCX, TXT and Markdown.

Everything returned here is *extracted* (heuristic) metadata. It is labelled
as such and only marked verified after a registry check (Crossref/OpenAlex).
"""
from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[,;]+)", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
REF_HEADINGS = re.compile(r"^\s*(references|bibliography|works cited|reference list|literature cited)\s*$", re.I)
ABSTRACT_RE = re.compile(r"^\s*abstract\s*[:.\-—]?\s*(.*)$", re.I)
NUMBERED_HEADING = re.compile(r"^\s*((\d+(\.\d+){0,3})\.?|[IVX]{1,5}\.)\s+[A-Z][^.!?]{2,90}$")
COMMON_HEADINGS = re.compile(
    r"^\s*(abstract|introduction|background|literature review|theoretical framework|conceptual framework|"
    r"methodology|methods|research design|results|findings|discussion|conclusions?|implications|limitations|"
    r"acknowledg(e)?ments|references|appendix.*|keywords)\s*$",
    re.I,
)

SUPPORTED_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
}


class ExtractionError(RuntimeError):
    pass


@dataclass
class Block:
    text: str
    page: int | None = None
    is_heading: bool = False
    level: int = 1


@dataclass
class Extracted:
    text: str
    blocks: list[Block]
    page_count: int | None
    metadata: dict = field(default_factory=dict)
    headings: list[dict] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


def detect_format(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK" and name.endswith(".docx"):
        return "docx"
    if name.endswith((".md", ".markdown")):
        return "md"
    if name.endswith(".txt"):
        return "txt"
    if data[:2] == b"PK":
        return "docx"
    # Fallback: treat as text if decodable
    try:
        data[:4096].decode("utf-8")
        return "txt"
    except UnicodeDecodeError:
        raise ExtractionError("Unsupported or unrecognised file type") from None


def _clean(t: str) -> str:
    t = t.replace("­", "")  # soft hyphen
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)  # de-hyphenate line breaks
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def extract_pdf(data: bytes) -> Extracted:
    import pymupdf  # PyMuPDF

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ExtractionError(f"Could not open PDF: {e}") from e
    if doc.needs_pass:
        raise ExtractionError("PDF is password-protected")
    raw_blocks: list[tuple[str, float, int, bool]] = []  # text, size, page, bold
    sizes: list[float] = []
    for pno, page in enumerate(doc, start=1):
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            lines_txt, bsizes, bold = [], [], True
            for line in b.get("lines", []):
                spans = line.get("spans", [])
                lt = "".join(s.get("text", "") for s in spans)
                if lt.strip():
                    lines_txt.append(lt)
                for s in spans:
                    if s.get("text", "").strip():
                        bsizes.append(float(s.get("size", 0)))
                        sizes.extend([float(s.get("size", 0))] * max(1, len(s["text"]) // 20))
                        if not (int(s.get("flags", 0)) & 16):
                            bold = False
            txt = _clean("\n".join(lines_txt))
            if txt:
                raw_blocks.append((txt, max(bsizes) if bsizes else 0.0, pno, bold))
    body = statistics.median(sizes) if sizes else 10.0
    blocks: list[Block] = []
    for txt, size, pno, bold in raw_blocks:
        one_line = " ".join(txt.split())
        short = len(one_line) < 120 and not one_line.endswith((".", ",", ";")) or COMMON_HEADINGS.match(one_line)
        heading = bool(short) and (
            size >= body * 1.15 or (bold and len(one_line) < 90) or bool(COMMON_HEADINGS.match(one_line))
        )
        if re.fullmatch(r"\d{1,4}", one_line):  # page numbers
            continue
        level = 1 if size >= body * 1.4 else 2
        blocks.append(Block(one_line if heading else txt, pno, heading, level))
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    # Title: PDF metadata if plausible, else largest text on page 1
    title = (meta.get("title") or "").strip()
    if not title or len(title) < 8 or title.lower().endswith((".doc", ".docx", ".pdf")):
        p1 = [(s, t) for t, s, p, _ in raw_blocks if p == 1 and 8 < len(t) < 300]
        if p1:
            title = " ".join(max(p1, key=lambda x: x[0])[1].split())
    text = "\n\n".join(b.text for b in blocks)
    ex = Extracted(text=text, blocks=blocks, page_count=doc.page_count)
    ex.metadata = {"title": title, "pdf_author": meta.get("author", ""), "pdf_metadata": meta}
    return ex


def extract_docx(data: bytes) -> Extracted:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(f"Could not open DOCX: {e}") from e
    blocks: list[Block] = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading") or style == "title":
            m = re.search(r"(\d)", style)
            blocks.append(Block(t, None, True, int(m.group(1)) if m else 1))
        else:
            blocks.append(Block(t, None, bool(COMMON_HEADINGS.match(t)) and len(t) < 60, 1))
    for table in d.tables:
        rows = [" | ".join(c.text.strip() for c in r.cells) for r in table.rows]
        if rows:
            blocks.append(Block("\n".join(rows)))
    cp = d.core_properties
    title = (cp.title or "").strip() or next((b.text for b in blocks if b.is_heading), "")
    ex = Extracted(text="\n\n".join(b.text for b in blocks), blocks=blocks, page_count=None)
    ex.metadata = {"title": title, "docx_author": cp.author or ""}
    return ex


def extract_text(data: bytes, markdown: bool) -> Extracted:
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        raw = data.decode("latin-1")
    blocks: list[Block] = []
    for para in re.split(r"\n\s*\n", raw):
        t = para.strip()
        if not t:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", t) if markdown else None
        if m and "\n" not in t:
            blocks.append(Block(m.group(2).strip(), None, True, len(m.group(1))))
        elif "\n" not in t and (COMMON_HEADINGS.match(t) or NUMBERED_HEADING.match(t)) and len(t) < 100:
            blocks.append(Block(t, None, True, 1))
        else:
            blocks.append(Block(t))
    title = next((b.text for b in blocks if b.is_heading), blocks[0].text[:200] if blocks else "")
    ex = Extracted(text="\n\n".join(b.text for b in blocks), blocks=blocks, page_count=None)
    ex.metadata = {"title": title}
    return ex


def split_references(lines: list[str]) -> list[str]:
    """Split a reference-section text into individual entries."""
    joined = "\n".join(lines)
    # Numbered styles: [1] ..., 1. ...
    if re.search(r"^\s*\[\d+\]", joined, re.M):
        parts = re.split(r"\n?\s*\[\d+\]\s*", joined)
    elif len(re.findall(r"^\s*\d{1,3}\.\s+[A-Z]", joined, re.M)) >= 3:
        parts = re.split(r"\n\s*\d{1,3}\.\s+", "\n" + joined)
    else:
        # Author-date: a new entry starts with "Surname, X." and contains a year soon after
        parts = re.split(r"\n(?=[A-Z][A-Za-z'\-]+(?:\s[A-Z][A-Za-z'\-]+)?,\s+(?:[A-Z]\.|[A-Z][a-z]+))", joined)
    refs = [" ".join(p.split()) for p in parts]
    return [r for r in refs if len(r) > 25 and (YEAR_RE.search(r) or DOI_RE.search(r))][:500]


def postprocess(ex: Extracted) -> Extracted:
    """Derive headings, abstract, DOI, year and references from blocks."""
    headings = []
    ref_start = None
    for i, b in enumerate(ex.blocks):
        if b.is_heading:
            headings.append({"text": b.text, "level": b.level, "page": b.page, "block": i})
            if REF_HEADINGS.match(b.text):
                ref_start = i
    ex.headings = headings
    if ref_start is not None:
        ref_lines = []
        for b in ex.blocks[ref_start + 1 :]:
            if b.is_heading and re.match(r"^\s*(appendix|acknowledg)", b.text, re.I):
                break
            ref_lines.append(b.text)
        ex.references = split_references(ref_lines)
    head = ex.text[:6000]
    m = DOI_RE.search(head)
    if m:
        ex.metadata["doi"] = m.group(1).rstrip(".)")
    # Abstract
    abstract = ""
    for i, b in enumerate(ex.blocks[:60]):
        am = ABSTRACT_RE.match(b.text)
        if am:
            abstract = am.group(1).strip()
            if len(abstract) < 100 and i + 1 < len(ex.blocks):
                nxt = ex.blocks[i + 1]
                abstract = (abstract + " " + nxt.text).strip()
            break
    if abstract:
        ex.metadata["abstract"] = " ".join(abstract.split())[:5000]
    years = [int(y) for y in YEAR_RE.findall(head[:3000])]
    if years:
        ex.metadata["year_candidates"] = sorted(set(years))[-5:]
    kw = re.search(r"key\s?words?\s*[:\-—]\s*(.+)", head, re.I)
    if kw:
        ex.metadata["keywords"] = [k.strip() for k in re.split(r"[;,•·]", kw.group(1).split("\n")[0]) if k.strip()][:15]
    return ex


def extract(filename: str, data: bytes) -> tuple[str, Extracted]:
    fmt = detect_format(filename, data)
    if fmt == "pdf":
        ex = extract_pdf(data)
    elif fmt == "docx":
        ex = extract_docx(data)
    else:
        ex = extract_text(data, markdown=(fmt == "md"))
    if not ex.text.strip():
        raise ExtractionError(
            "No extractable text found. The file may be a scanned image; OCR is not enabled in this deployment."
        )
    return fmt, postprocess(ex)
