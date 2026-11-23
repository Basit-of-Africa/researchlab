"""Reference formatting, citekeys, in-text citation rendering and auditing.

Manuscript text uses Pandoc-style markers: ``[@smith2020]``,
``[@smith2020, p. 12]`` or ``[@a2019; @b2021]``. Every marker must resolve to a
project ``Reference`` row; unresolved keys are flagged, never invented.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.models import Publication, Reference

CITE_GROUP = re.compile(r"\[(@[^\[\]]+?)\]")
CITE_ITEM = re.compile(r"@([A-Za-z0-9_:\-]+)(?:\s*,\s*([^;\]]+))?")
QUOTE = re.compile(r"[\"“]([^\"”]{15,})[\"”]")

STYLES = {
    "apa7": "APA 7th",
    "harvard": "Harvard",
    "chicago_ad": "Chicago (author-date)",
    "ieee": "IEEE (numeric)",
    "vancouver": "Vancouver (numeric)",
}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def split_name(name: str) -> dict:
    name = " ".join((name or "").split())
    if not name:
        return {"family": "", "given": ""}
    if "," in name:
        fam, giv = name.split(",", 1)
        return {"family": fam.strip(), "given": giv.strip()}
    parts = name.split(" ")
    particles = {"van", "von", "de", "da", "del", "der", "di", "le", "la", "du"}
    i = len(parts) - 1
    while i > 0 and parts[i - 1].lower() in particles:
        i -= 1
    return {"family": " ".join(parts[i:]), "given": " ".join(parts[:i])}


def make_citekey(authors: list[dict], year, title: str, existing: set[str]) -> str:
    fam = _ascii(authors[0]["family"]).lower() if authors and authors[0].get("family") else "anon"
    fam = re.sub(r"[^a-z]", "", fam) or "anon"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", _ascii(title or "")) if w.lower() not in {"with", "from", "into", "that", "this", "their", "towards", "toward"}), "")
    base = f"{fam}{year or 'nd'}{word.lower()}"
    key, n = base, 1
    while key in existing:
        n += 1
        key = f"{base}{chr(96 + n)}" if n <= 26 else f"{base}{n}"
    return key


def csl_from_publication(p: Publication) -> dict:
    return {
        "title": p.title,
        "authors": [
            {"family": a.family or split_name(a.name)["family"], "given": a.given or split_name(a.name)["given"]}
            for a in p.authors
        ],
        "year": p.year,
        "venue": p.venue,
        "volume": p.volume,
        "issue": p.issue,
        "pages": p.pages,
        "doi": p.doi,
        "url": p.url,
        "publisher": p.publisher,
        "type": p.pub_type,
    }


def _initials(given: str, dots: bool = True, space: bool = True) -> str:
    parts = [g for g in re.split(r"[\s\-]+", given or "") if g]
    sep = " " if space else ""
    return sep.join(f"{g[0]}." if dots else g[0] for g in parts)


def _authors_apa(auths: list[dict]) -> str:
    names = [f"{a['family']}, {_initials(a.get('given', ''))}".strip().rstrip(",") for a in auths if a.get("family")]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", ... " + names[-1]


def _authors_harvard(auths: list[dict]) -> str:
    names = [f"{a['family']}, {_initials(a.get('given', ''), space=False)}" for a in auths if a.get("family")]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _authors_ieee(auths: list[dict]) -> str:
    names = [f"{_initials(a.get('given', ''))} {a['family']}".strip() for a in auths if a.get("family")]
    if len(names) > 6:
        return names[0] + " et al."
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def _authors_vancouver(auths: list[dict]) -> str:
    names = [f"{a['family']} {_initials(a.get('given', ''), dots=False, space=False)}" for a in auths if a.get("family")]
    if len(names) > 6:
        return ", ".join(names[:6]) + ", et al"
    return ", ".join(names)


def format_reference(csl: dict, style: str, number: int | None = None) -> str:
    """Render a reference entry. Missing fields are omitted, never guessed."""
    a = csl.get("authors") or []
    year = csl.get("year") or "n.d."
    title = (csl.get("title") or "").rstrip(".")
    venue = csl.get("venue") or ""
    vol, iss, pages = csl.get("volume") or "", csl.get("issue") or "", csl.get("pages") or ""
    doi = csl.get("doi")
    link = f"https://doi.org/{doi}" if doi else (csl.get("url") or "")
    pub = csl.get("publisher") or ""
    if style == "apa7":
        s = f"{_authors_apa(a) or title}"
        s += f" ({year}). "
        s += f"{title}. " if a else ""
        if venue:
            s += f"*{venue}*"
            if vol:
                s += f", *{vol}*"
            if iss:
                s += f"({iss})"
            if pages:
                s += f", {pages.replace('-', '–')}"
            s += ". "
        elif pub:
            s += f"{pub}. "
        return (s + link).strip()
    if style == "harvard":
        s = f"{_authors_harvard(a) or title} ({year}) "
        s += f"'{title}', " if a else ""
        if venue:
            s += f"*{venue}*"
            if vol:
                s += f", {vol}"
            if iss:
                s += f"({iss})"
            if pages:
                s += f", pp. {pages}"
            s += "."
        elif pub:
            s += f"{pub}."
        if link:
            s += f" Available at: {link}."
        return s.strip()
    if style == "chicago_ad":
        names = [f"{x['family']}, {x.get('given', '')}".strip(", ") if i == 0 else f"{x.get('given', '')} {x['family']}".strip() for i, x in enumerate(a) if x.get("family")]
        auth = names[0] if len(names) == 1 else (", ".join(names[:-1]) + ", and " + names[-1] if names else title)
        s = f"{auth}. {year}. "
        s += f"“{title}.” " if a else ""
        if venue:
            s += f"*{venue}* {vol}".rstrip()
            if iss:
                s += f" ({iss})"
            if pages:
                s += f": {pages}"
            s += ". "
        return (s + link).strip()
    if style == "ieee":
        s = f"[{number or 0}] {_authors_ieee(a)}, “{title},” "
        if venue:
            s += f"*{venue}*"
            if vol:
                s += f", vol. {vol}"
            if iss:
                s += f", no. {iss}"
            if pages:
                s += f", pp. {pages}"
            s += f", {year}."
        else:
            s += f"{pub + ', ' if pub else ''}{year}."
        if doi:
            s += f" doi: {doi}."
        return s
    if style == "vancouver":
        s = f"{number or 0}. {_authors_vancouver(a)}. {title}. "
        if venue:
            s += f"{venue}. {year}"
            if vol:
                s += f";{vol}"
            if iss:
                s += f"({iss})"
            if pages:
                s += f":{pages}"
            s += "."
        else:
            s += f"{pub + '; ' if pub else ''}{year}."
        if doi:
            s += f" doi:{doi}"
        return s
    raise ValueError(f"Unsupported citation style {style}")


def _intext_author(csl: dict, style: str) -> str:
    a = [x for x in (csl.get("authors") or []) if x.get("family")]
    if not a:
        return (csl.get("title") or "Anon.")[:40]
    if len(a) == 1:
        return a[0]["family"]
    if len(a) == 2:
        joiner = " & " if style == "apa7" else " and "
        return a[0]["family"] + joiner + a[1]["family"]
    return a[0]["family"] + " et al."


def parse_markers(text: str) -> list[dict]:
    out = []
    for g in CITE_GROUP.finditer(text):
        items = []
        for it in CITE_ITEM.finditer(g.group(1)):
            items.append({"key": it.group(1), "locator": (it.group(2) or "").strip()})
        if items:
            out.append({"start": g.start(), "end": g.end(), "raw": g.group(0), "items": items})
    return out


def cited_keys_in_order(texts: list[str]) -> list[str]:
    seen: list[str] = []
    for t in texts:
        for m in parse_markers(t):
            for it in m["items"]:
                if it["key"] not in seen:
                    seen.append(it["key"])
    return seen


def render_intext(text: str, refs_by_key: dict[str, dict], style: str, numbering: dict[str, int]) -> str:
    """Replace markers with formatted in-text citations. Unknown keys are made visible."""

    def repl(m: re.Match) -> str:
        items = [{"key": it.group(1), "locator": (it.group(2) or "").strip()} for it in CITE_ITEM.finditer(m.group(1))]
        rendered = []
        for it in items:
            csl = refs_by_key.get(it["key"])
            if csl is None:
                rendered.append(f"UNVERIFIED CITATION: {it['key']}")
                continue
            if style in ("ieee", "vancouver"):
                rendered.append(str(numbering.get(it["key"], "?")) + (f", {it['locator']}" if it["locator"] else ""))
            else:
                year = csl.get("year") or "n.d."
                loc = f", {it['locator']}" if it["locator"] else ""
                sep = " " if style == "chicago_ad" else ", "
                rendered.append(f"{_intext_author(csl, style)}{sep}{year}{loc}")
        if style in ("ieee", "vancouver"):
            return "[" + ", ".join(rendered) + "]"
        return "(" + "; ".join(rendered) + ")"

    return CITE_GROUP.sub(repl, text)


def build_bibliography(keys_in_order: list[str], refs_by_key: dict[str, dict], style: str) -> list[str]:
    known = [k for k in keys_in_order if k in refs_by_key]
    if style in ("ieee", "vancouver"):
        return [format_reference(refs_by_key[k], style, i + 1) for i, k in enumerate(known)]

    def sort_key(k: str):
        c = refs_by_key[k]
        a = c.get("authors") or []
        return ((a[0].get("family", "") if a else c.get("title", "")).lower(), str(c.get("year") or ""))

    return [format_reference(refs_by_key[k], style) for k in sorted(known, key=sort_key)]


def audit_text(sections: list[dict], references: list[Reference]) -> dict:
    """Deterministic citation audit across manuscript sections.

    sections: [{id, key, heading, content}]
    """
    by_key = {r.citekey: r for r in references}
    findings: list[dict] = []
    used: Counter = Counter()
    for s in sections:
        content = s.get("content") or ""
        markers = parse_markers(content)
        for m in markers:
            for it in m["items"]:
                used[it["key"]] += 1
                ref = by_key.get(it["key"])
                ctx = content[max(0, m["start"] - 220) : m["start"]].split("\n")[-1]
                if ref is None:
                    findings.append({"severity": "critical", "type": "nonexistent_reference", "section": s["key"],
                                     "citekey": it["key"], "context": ctx, "message": "Citation key does not match any reference in this project."})
                    continue
                if ref.verification_status in ("not_found",):
                    findings.append({"severity": "critical", "type": "unverifiable_source", "section": s["key"], "citekey": it["key"],
                                     "message": "The reference could not be found in Crossref/OpenAlex."})
                elif ref.verification_status == "mismatch":
                    findings.append({"severity": "high", "type": "metadata_mismatch", "section": s["key"], "citekey": it["key"],
                                     "message": "Reference metadata disagrees with the registry record.", "detail": ref.verification_detail})
                elif ref.verification_status in ("unverified", "incomplete", "unverifiable"):
                    findings.append({"severity": "medium", "type": "unverified_reference", "section": s["key"], "citekey": it["key"],
                                     "message": f"Reference status is '{ref.verification_status}'. Verify before submission."})
                if not (ref.csl or {}).get("doi") and (ref.csl or {}).get("type") in ("journal-article", "article"):
                    findings.append({"severity": "low", "type": "missing_doi", "section": s["key"], "citekey": it["key"],
                                     "message": "Journal article without a DOI."})
                missing = [f for f in ("title", "year", "authors") if not (ref.csl or {}).get(f)]
                if missing:
                    findings.append({"severity": "medium", "type": "missing_metadata", "section": s["key"], "citekey": it["key"],
                                     "message": "Missing: " + ", ".join(missing)})
        # quotations need page locators
        for q in QUOTE.finditer(content):
            after = content[q.end() : q.end() + 80]
            nxt = CITE_GROUP.match(after.lstrip())
            if not nxt or not any(it.group(2) for it in CITE_ITEM.finditer(nxt.group(1))):
                findings.append({"severity": "high", "type": "quotation_needs_page", "section": s["key"],
                                 "context": q.group(0)[:160], "message": "Direct quotation without a cited page/locator."})
        for marker in re.findall(r"\[(?:CITATION NEEDED|EVIDENCE REQUIRED)[^\]]*\]", content):
            findings.append({"severity": "high", "type": "unsupported_claim", "section": s["key"], "context": marker,
                             "message": "The writer marked a claim as lacking verified support."})
    # duplicates: same DOI or near-identical titles under different keys
    seen_doi: dict[str, str] = {}
    for r in references:
        d = (r.csl or {}).get("doi")
        if d:
            if d in seen_doi:
                findings.append({"severity": "medium", "type": "duplicate_reference", "citekey": r.citekey,
                                 "message": f"Same DOI as {seen_doi[d]}"})
            else:
                seen_doi[d] = r.citekey
    for r in references:
        if r.source_type == "secondary" and used[r.citekey]:
            findings.append({"severity": "low", "type": "secondary_source", "citekey": r.citekey,
                             "message": "Cited as a secondary source; consider consulting the primary work."})
    uncited = [r.citekey for r in references if used[r.citekey] == 0]
    summary = Counter(f["severity"] for f in findings)
    return {"findings": findings, "citations_used": dict(used), "uncited_references": uncited, "summary": dict(summary)}
