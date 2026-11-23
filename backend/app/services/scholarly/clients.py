"""Clients for legitimate scholarly APIs, normalised to a common record.

Only metadata the source actually returns is populated — missing fields stay
empty and are never inferred. ``abstract_source`` records where an abstract
came from; full-text availability is reported separately from OA status.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("researchlab.scholarly")


class ScholarlyError(RuntimeError):
    pass


class ScholarlyRateLimited(ScholarlyError):
    pass


class ScholarlyNotConfigured(ScholarlyError):
    pass


@dataclass
class AuthorRec:
    name: str
    given: str = ""
    family: str = ""
    orcid: str = ""
    openalex_author_id: str = ""
    institution: str = ""


@dataclass
class PubRecord:
    title: str
    source_db: str
    authors: list[AuthorRec] = field(default_factory=list)
    year: int | None = None
    publication_date: str = ""
    venue: str = ""
    publisher: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    abstract: str = ""
    abstract_source: str = ""
    doi: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    core_id: str | None = None
    url: str = ""
    pub_type: str = ""
    is_oa: bool | None = None
    oa_url: str = ""
    full_text_available: bool = False
    cited_by_count: int | None = None
    concepts: list[str] = field(default_factory=list)
    referenced_works: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    d = d.rstrip(".,;)")
    return d.lower() if d.startswith("10.") else None


def _ua() -> dict[str, str]:
    s = get_settings()
    ua = "ResearchLab/1.0 (academic research assistant)"
    if s.scholarly_contact_email:
        ua += f" mailto:{s.scholarly_contact_email}"
    return {"User-Agent": ua}


def _get(url: str, params: dict | None = None, headers: dict | None = None, source: str = "") -> dict:
    s = get_settings()
    delay = 1.5
    for attempt in range(3):
        try:
            r = httpx.get(url, params=params, headers={**_ua(), **(headers or {})}, timeout=s.scholarly_timeout_seconds)
        except httpx.HTTPError as e:
            if attempt == 2:
                raise ScholarlyError(f"{source} unreachable: {e}") from e
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 404:
            raise ScholarlyError(f"{source}: not found")
        if r.status_code == 429:
            if attempt == 2:
                raise ScholarlyRateLimited(f"{source} rate limit reached; try again later or configure an API key")
            time.sleep(delay * 2)
            delay *= 2
            continue
        if r.status_code >= 500:
            if attempt == 2:
                raise ScholarlyError(f"{source} server error {r.status_code}")
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code >= 400:
            raise ScholarlyError(f"{source} request failed ({r.status_code}): {r.text[:200]}")
        return r.json()
    raise ScholarlyError(f"{source} request failed")


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------
OPENALEX = "https://api.openalex.org"


def _oa_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _oa_short(id_url: str | None) -> str | None:
    return id_url.rsplit("/", 1)[-1] if id_url else None


def openalex_to_record(w: dict) -> PubRecord:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = w.get("open_access") or {}
    biblio = w.get("biblio") or {}
    authors = []
    for a in w.get("authorships") or []:
        au = a.get("author") or {}
        insts = a.get("institutions") or []
        authors.append(
            AuthorRec(
                name=au.get("display_name") or a.get("raw_author_name") or "",
                orcid=(au.get("orcid") or "").replace("https://orcid.org/", ""),
                openalex_author_id=_oa_short(au.get("id")) or "",
                institution=(insts[0].get("display_name") if insts else "") or "",
            )
        )
    pages = ""
    if biblio.get("first_page"):
        pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
    abstract = _oa_abstract(w.get("abstract_inverted_index"))
    oa_url = oa.get("oa_url") or ""
    best = w.get("best_oa_location") or {}
    return PubRecord(
        title=(w.get("title") or w.get("display_name") or "").strip(),
        source_db="openalex",
        authors=authors,
        year=w.get("publication_year"),
        publication_date=w.get("publication_date") or "",
        venue=src.get("display_name") or "",
        publisher=src.get("host_organization_name") or "",
        volume=biblio.get("volume") or "",
        issue=biblio.get("issue") or "",
        pages=pages,
        abstract=abstract,
        abstract_source="openalex (reconstructed)" if abstract else "",
        doi=normalize_doi(w.get("doi")),
        openalex_id=_oa_short(w.get("id")),
        url=loc.get("landing_page_url") or (w.get("doi") or ""),
        pub_type=w.get("type") or "",
        is_oa=oa.get("is_oa"),
        oa_url=oa_url,
        full_text_available=bool(best.get("pdf_url")),
        cited_by_count=w.get("cited_by_count"),
        concepts=[t.get("display_name", "") for t in (w.get("topics") or w.get("concepts") or [])][:8],
        referenced_works=[_oa_short(x) or "" for x in (w.get("referenced_works") or [])][:200],
    )


def _oa_params(extra: dict) -> dict:
    s = get_settings()
    p = dict(extra)
    if s.scholarly_contact_email:
        p["mailto"] = s.scholarly_contact_email
    if s.openalex_api_key:
        p["api_key"] = s.openalex_api_key
    return p


def openalex_search(
    query: str = "",
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    pub_type: str | None = None,
    open_access: bool | None = None,
    author_id: str | None = None,
    source_id: str | None = None,
    min_citations: int | None = None,
    sort: str | None = None,
    page: int = 1,
    per_page: int = 25,
    extra_filters: list[str] | None = None,
) -> tuple[list[PubRecord], int]:
    filters = list(extra_filters or [])
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if pub_type:
        filters.append(f"type:{pub_type}")
    if open_access is not None:
        filters.append(f"is_oa:{str(open_access).lower()}")
    if author_id:
        filters.append(f"authorships.author.id:{author_id}")
    if source_id:
        filters.append(f"primary_location.source.id:{source_id}")
    if min_citations:
        filters.append(f"cited_by_count:>{min_citations - 1}")
    params: dict[str, Any] = {"per-page": min(per_page, 50), "page": page}
    if query:
        params["search"] = query
    if filters:
        params["filter"] = ",".join(filters)
    if sort:
        params["sort"] = sort
    data = _get(f"{OPENALEX}/works", _oa_params(params), source="OpenAlex")
    return [openalex_to_record(w) for w in data.get("results", [])], int((data.get("meta") or {}).get("count") or 0)


def openalex_work(id_or_doi: str) -> PubRecord:
    key = id_or_doi
    d = normalize_doi(id_or_doi)
    if d:
        key = f"https://doi.org/{d}"
    data = _get(f"{OPENALEX}/works/{key}", _oa_params({}), source="OpenAlex")
    return openalex_to_record(data)


def openalex_related(openalex_id: str, relation: str = "cites", per_page: int = 25) -> list[PubRecord]:
    """relation: 'cites' (works citing this one), 'references' (works it cites), 'related'."""
    if relation == "cites":
        recs, _ = openalex_search(extra_filters=[f"cites:{openalex_id}"], per_page=per_page, sort="cited_by_count:desc")
        return recs
    work = _get(f"{OPENALEX}/works/{openalex_id}", _oa_params({}), source="OpenAlex")
    ids = work.get("referenced_works" if relation == "references" else "related_works") or []
    ids = [_oa_short(i) for i in ids][:per_page]
    if not ids:
        return []
    recs, _ = openalex_search(extra_filters=["openalex_id:" + "|".join(i for i in ids if i)], per_page=per_page)
    return recs


def openalex_authors(query: str, per_page: int = 10) -> list[dict]:
    data = _get(f"{OPENALEX}/authors", _oa_params({"search": query, "per-page": per_page}), source="OpenAlex")
    out = []
    for a in data.get("results", []):
        inst = (a.get("last_known_institutions") or [{}])
        out.append(
            {
                "id": _oa_short(a.get("id")),
                "name": a.get("display_name"),
                "orcid": (a.get("orcid") or "").replace("https://orcid.org/", ""),
                "works_count": a.get("works_count"),
                "cited_by_count": a.get("cited_by_count"),
                "institution": (inst[0] or {}).get("display_name", "") if inst else "",
            }
        )
    return out


def openalex_institutions(query: str, per_page: int = 10) -> list[dict]:
    data = _get(f"{OPENALEX}/institutions", _oa_params({"search": query, "per-page": per_page}), source="OpenAlex")
    return [
        {
            "id": _oa_short(i.get("id")),
            "name": i.get("display_name"),
            "country": i.get("country_code"),
            "works_count": i.get("works_count"),
            "type": i.get("type"),
        }
        for i in data.get("results", [])
    ]


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------
CROSSREF = "https://api.crossref.org"


def _strip_jats(t: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", t or "").split())


def crossref_to_record(m: dict) -> PubRecord:
    authors = []
    for a in m.get("author") or []:
        given, family = a.get("given", ""), a.get("family", "")
        authors.append(
            AuthorRec(
                name=(f"{given} {family}".strip() or a.get("name", "")),
                given=given,
                family=family,
                orcid=(a.get("ORCID") or "").replace("http://orcid.org/", "").replace("https://orcid.org/", ""),
                institution=((a.get("affiliation") or [{}])[0] or {}).get("name", "") if a.get("affiliation") else "",
            )
        )
    year = None
    date = ""
    for k in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((m.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = int(parts[0])
            date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p)
            break
    abstract = _strip_jats(m.get("abstract", ""))
    links = m.get("link") or []
    lic = m.get("license") or []
    return PubRecord(
        title=_strip_jats(((m.get("title") or [""])[0])),
        source_db="crossref",
        authors=authors,
        year=year,
        publication_date=date,
        venue=((m.get("container-title") or [""])[0]),
        publisher=m.get("publisher", ""),
        volume=m.get("volume", ""),
        issue=m.get("issue", ""),
        pages=m.get("page", ""),
        abstract=abstract,
        abstract_source="crossref" if abstract else "",
        doi=normalize_doi(m.get("DOI")),
        url=m.get("URL", ""),
        pub_type=m.get("type", ""),
        is_oa=True if any("creativecommons" in (x.get("URL") or "") for x in lic) else None,
        full_text_available=any(x.get("content-type") == "application/pdf" for x in links) and bool(lic),
        cited_by_count=m.get("is-referenced-by-count"),
    )


def crossref_work(doi: str) -> PubRecord:
    d = normalize_doi(doi)
    if not d:
        raise ScholarlyError("Invalid DOI")
    data = _get(f"{CROSSREF}/works/{d}", source="Crossref")
    return crossref_to_record(data["message"])


def crossref_search(
    query: str, *, rows: int = 20, year_from: int | None = None, year_to: int | None = None, bibliographic: bool = False
) -> list[PubRecord]:
    params: dict[str, Any] = {"rows": rows}
    params["query.bibliographic" if bibliographic else "query"] = query
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    if filters:
        params["filter"] = ",".join(filters)
    s = get_settings()
    if s.scholarly_contact_email:
        params["mailto"] = s.scholarly_contact_email
    data = _get(f"{CROSSREF}/works", params, source="Crossref")
    return [crossref_to_record(m) for m in (data.get("message") or {}).get("items", [])]


# ---------------------------------------------------------------------------
# Semantic Scholar (works without a key at a low shared rate limit)
# ---------------------------------------------------------------------------
S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,abstract,year,venue,authors,externalIds,url,citationCount,isOpenAccess,openAccessPdf,publicationTypes,publicationDate,journal"


def _s2_headers() -> dict:
    k = get_settings().semantic_scholar_api_key
    return {"x-api-key": k} if k else {}


def s2_to_record(p: dict) -> PubRecord:
    ext = p.get("externalIds") or {}
    j = p.get("journal") or {}
    oapdf = p.get("openAccessPdf") or {}
    abstract = p.get("abstract") or ""
    return PubRecord(
        title=p.get("title") or "",
        source_db="semantic_scholar",
        authors=[AuthorRec(name=a.get("name", "")) for a in p.get("authors") or []],
        year=p.get("year"),
        publication_date=p.get("publicationDate") or "",
        venue=p.get("venue") or j.get("name") or "",
        volume=j.get("volume") or "",
        pages=(j.get("pages") or "").strip(),
        abstract=abstract,
        abstract_source="semantic_scholar" if abstract else "",
        doi=normalize_doi(ext.get("DOI")),
        s2_id=p.get("paperId"),
        url=p.get("url") or "",
        pub_type=",".join(p.get("publicationTypes") or []),
        is_oa=p.get("isOpenAccess"),
        oa_url=oapdf.get("url") or "",
        full_text_available=bool(oapdf.get("url")),
        cited_by_count=p.get("citationCount"),
    )


def s2_search(query: str, limit: int = 20, year: str | None = None) -> list[PubRecord]:
    params: dict[str, Any] = {"query": query, "limit": limit, "fields": S2_FIELDS}
    if year:
        params["year"] = year
    data = _get(f"{S2}/paper/search", params, _s2_headers(), source="Semantic Scholar")
    return [s2_to_record(p) for p in data.get("data") or []]


def s2_related(paper_id: str, relation: str = "citations", limit: int = 20) -> list[PubRecord]:
    """relation: citations | references | recommendations"""
    if relation == "recommendations":
        data = _get(
            f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{paper_id}",
            {"fields": S2_FIELDS, "limit": limit},
            _s2_headers(),
            source="Semantic Scholar",
        )
        return [s2_to_record(p) for p in data.get("recommendedPapers") or []]
    key = "citingPaper" if relation == "citations" else "citedPaper"
    data = _get(
        f"{S2}/paper/{paper_id}/{relation}", {"fields": S2_FIELDS, "limit": limit}, _s2_headers(), source="Semantic Scholar"
    )
    return [s2_to_record(x[key]) for x in data.get("data") or [] if x.get(key)]


# ---------------------------------------------------------------------------
# CORE (open-access full texts) — requires a free API key
# ---------------------------------------------------------------------------
CORE = "https://api.core.ac.uk/v3"


def core_search(query: str, limit: int = 20) -> list[PubRecord]:
    key = get_settings().core_api_key
    if not key:
        raise ScholarlyNotConfigured("CORE requires a free API key (CORE_API_KEY). See docs/INTEGRATIONS.md")
    data = _get(
        f"{CORE}/search/works", {"q": query, "limit": limit}, {"Authorization": f"Bearer {key}"}, source="CORE"
    )
    out = []
    for w in data.get("results") or []:
        out.append(
            PubRecord(
                title=w.get("title") or "",
                source_db="core",
                authors=[AuthorRec(name=a.get("name", "")) for a in w.get("authors") or []],
                year=w.get("yearPublished"),
                venue=((w.get("journals") or [{}])[0] or {}).get("title", "") if w.get("journals") else "",
                publisher=w.get("publisher") or "",
                abstract=w.get("abstract") or "",
                abstract_source="core" if w.get("abstract") else "",
                doi=normalize_doi(w.get("doi")),
                core_id=str(w.get("id")) if w.get("id") else None,
                url=(w.get("links") or [{}])[0].get("url", "") if w.get("links") else "",
                is_oa=True,
                oa_url=w.get("downloadUrl") or "",
                full_text_available=bool(w.get("downloadUrl")),
                cited_by_count=w.get("citationCount"),
            )
        )
    return out
