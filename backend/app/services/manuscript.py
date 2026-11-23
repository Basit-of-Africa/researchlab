"""Manuscript planning, sectional generation, checking and assembly.

Design principles
* Never one giant model response: outline → per-section (and per-subsection)
  generation, each persisted as its own job step, so interrupted jobs resume
  without regenerating finished sections.
* The writer may cite only the project's curated references, by citekey
  (``[@key]``). Anything else is flagged by the audit.
* With no linked analysis outputs, results sections describe what will be
  reported and list ``[DATA REQUIRED: ...]`` items — never invented findings.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisOutput,
    Citation,
    Manuscript,
    ManuscriptSection,
    ManuscriptVersion,
    Publication,
    Reference,
    ResearchAnalysis,
    ResearcherProfile,
    WritingProfile,
)
from app.services import citations as cit
from app.services import embeddings
from app.services.agents.core import ContextItem, project_foundation, run_agent
from app.services.search import SearchFilters, hybrid_search
from app.services.style import effective_profile, style_prompt_block

# (key, heading, share of body words)
STRUCTURES: dict[str, list[tuple[str, str, float]]] = {
    "empirical_article": [
        ("abstract", "Abstract", 250 / 7000), ("introduction", "Introduction", 1000 / 7000),
        ("literature_review", "Literature Review and Theoretical Framework", 2000 / 7000),
        ("methodology", "Methodology", 1000 / 7000), ("results", "Results", 1000 / 7000),
        ("discussion", "Discussion", 1250 / 7000), ("conclusion", "Conclusion and Implications", 500 / 7000),
    ],
    "qualitative_article": [
        ("abstract", "Abstract", 0.036), ("introduction", "Introduction", 0.14), ("literature_review", "Literature Review", 0.26),
        ("methodology", "Methodology", 0.14), ("findings", "Findings", 0.19), ("discussion", "Discussion", 0.17),
        ("conclusion", "Conclusion and Implications", 0.064),
    ],
    "conceptual_article": [
        ("abstract", "Abstract", 0.036), ("introduction", "Introduction", 0.15), ("literature_review", "Literature Review", 0.25),
        ("theory_development", "Theory Development and Propositions", 0.30), ("discussion", "Discussion", 0.18),
        ("conclusion", "Conclusion and Implications", 0.084),
    ],
    "systematic_review": [
        ("abstract", "Abstract", 0.036), ("introduction", "Introduction", 0.13), ("methodology", "Review Methodology", 0.17),
        ("results", "Results of the Review", 0.30), ("discussion", "Discussion", 0.24), ("conclusion", "Conclusion and Future Research", 0.124),
    ],
}

DATA_SECTIONS = {"results", "findings"}
SUBSECTION_THRESHOLD = 1100  # sections longer than this are written subsection by subsection


def allocate(target_words: int, structure: list[tuple[str, str, float]], abstract_words: int | None = None) -> list[dict]:
    total_share = sum(s for _, _, s in structure)
    out = []
    for key, heading, share in structure:
        w = target_words * share / total_share
        if key == "abstract":
            w = abstract_words or min(300, max(150, round(w)))
        out.append({"key": key, "heading": heading, "target_words": int(round(w / 10) * 10)})
    # make body sections sum to the target exactly
    diff = target_words - sum(x["target_words"] for x in out)
    body = [x for x in out if x["key"] != "abstract"]
    if body:
        max(body, key=lambda x: x["target_words"])["target_words"] += diff
    return out


def count_words(text: str) -> int:
    t = re.sub(r"\[@[^\]]+\]", " ", text or "")
    t = re.sub(r"\[(DATA REQUIRED|CITATION NEEDED|EVIDENCE REQUIRED)[^\]]*\]", " ", t)
    t = re.sub(r"^#+\s.*$", " ", t, flags=re.M)  # headings excluded
    t = re.sub(r"[*_`>|#-]", " ", t)
    return len(re.findall(r"\b[\w'’-]+\b", t))


def create_manuscript(db: Session, owner_id: uuid.UUID, project_id: uuid.UUID, *, title: str, manuscript_type: str,
                      target_word_count: int, citation_style: str, mode: str, sections: list[dict] | None = None,
                      **fields) -> Manuscript:
    if manuscript_type not in STRUCTURES and not sections:
        raise ValueError(f"Unknown manuscript type '{manuscript_type}'; supply a custom section structure")
    prof = db.scalar(select(ResearcherProfile).where(ResearcherProfile.user_id == owner_id))
    wp = db.scalar(select(WritingProfile).where(WritingProfile.owner_id == owner_id, WritingProfile.is_default.is_(True)))
    m = Manuscript(owner_id=owner_id, project_id=project_id, title=title, manuscript_type=manuscript_type,
                   target_word_count=target_word_count, citation_style=citation_style, mode=mode,
                   language_variant=fields.pop("language_variant", None) or (prof.language_variant if prof else "en-GB"),
                   writing_profile_id=wp.id if wp else None, **fields)
    db.add(m)
    db.flush()
    plan = sections or allocate(target_word_count, STRUCTURES[manuscript_type])
    for i, s in enumerate(plan):
        key = s.get("key") or re.sub(r"[^a-z0-9]+", "_", s["heading"].lower()).strip("_")
        db.add(ManuscriptSection(manuscript_id=m.id, key=key[:60], heading=s["heading"], position=i,
                                 target_words=int(s.get("target_words") or 0), objective=s.get("objective", "")))
    db.flush()
    return m


def snapshot(db: Session, m: Manuscript, label: str, created_by: str = "researcher") -> ManuscriptVersion:
    n = (db.scalar(select(func.max(ManuscriptVersion.version_no)).where(ManuscriptVersion.manuscript_id == m.id)) or 0) + 1
    secs = sorted(m.sections, key=lambda s: s.position)
    snap = {"title": m.title, "sections": [{"key": s.key, "heading": s.heading, "content": s.content, "word_count": s.word_count,
                                            "status": s.status, "target_words": s.target_words} for s in secs]}
    v = ManuscriptVersion(manuscript_id=m.id, version_no=n, label=label, snapshot=snap,
                          total_words=sum(s.word_count for s in secs if s.key != "abstract"), created_by=created_by)
    db.add(v)
    db.flush()
    return v


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def project_references(db: Session, m: Manuscript) -> list[Reference]:
    return list(db.scalars(select(Reference).where(Reference.owner_id == m.owner_id, Reference.project_id == m.project_id)
                           .order_by(Reference.citekey)).all())


def _cos(a: list[float], b: list[float]) -> float:
    return float(np.dot(np.array(a), np.array(b)))


def reference_context(db: Session, m: Manuscript, query: str, limit: int = 14) -> list[ContextItem]:
    refs = [r for r in project_references(db, m) if r.verification_status not in ("not_found",)]
    if not refs:
        return []
    items: list[ContextItem] = []
    doc_refs = {r.document_id: r for r in refs if r.document_id}
    scored: list[tuple[float, ContextItem]] = []
    if doc_refs:
        hits = hybrid_search(db, m.owner_id, query, SearchFilters(document_ids=list(doc_refs)), k=limit * 2)
        per_doc: dict[str, list[dict]] = {}
        for h in hits:
            per_doc.setdefault(h["document_id"], []).append(h)
        for did, hs in per_doc.items():
            r = doc_refs[uuid.UUID(did)]
            excerpt = "\n…\n".join(f"(p. {h['page_start']}) {h['text']}" if h.get("page_start") else h["text"] for h in hs[:2])
            scored.append((hs[0]["scores"]["score"] * 100, _ref_item(r, excerpt, hs[0].get("visibility") == "private_unpublished",
                                                                   "full text excerpt")))
    qv = embeddings.embed_query(query)
    pub_ids = [r.publication_id for r in refs if r.publication_id and r.document_id not in doc_refs]
    pubs = {p.id: p for p in db.scalars(select(Publication).where(Publication.id.in_(pub_ids))).all()} if pub_ids else {}
    covered_docs = set(doc_refs)
    for r in refs:
        if r.document_id in covered_docs:
            continue
        p = pubs.get(r.publication_id) if r.publication_id else None
        text = (p.abstract if p and p.abstract else "") or r.raw_text
        basis = "abstract only" if p and p.abstract else "bibliographic record only"
        sim = _cos(qv, embeddings.embed_texts([((r.csl or {}).get("title") or "") + " " + text])[0])
        scored.append((sim, _ref_item(r, text or "(no abstract — cite only for what its title establishes, if at all)", False, basis)))
    scored.sort(key=lambda x: x[0], reverse=True)
    items = [it for _, it in scored[:limit]]
    return items


def _ref_item(r: Reference, text: str, private: bool, basis: str) -> ContextItem:
    csl = r.csl or {}
    a = csl.get("authors") or []
    who = (a[0].get("family") if a else "Anon.") + (" et al." if len(a) > 2 else (f" & {a[1].get('family')}" if len(a) == 2 else ""))
    label = (f"CITABLE REFERENCE — cite as [@{r.citekey}] — {who} ({csl.get('year') or 'n.d.'}) \"{(csl.get('title') or '')[:160]}\" "
             f"[{basis}; verification: {r.verification_status}]")
    return ContextItem("reference", label, text, private=private, ref={"type": "reference", "citekey": r.citekey, "reference_id": str(r.id)})


def analysis_context(db: Session, m: Manuscript) -> list[ContextItem]:
    ids = (m.requirements or {}).get("analysis_ids") or []
    if not ids:
        return []
    items = []
    for a in db.scalars(select(ResearchAnalysis).where(ResearchAnalysis.id.in_([uuid.UUID(i) for i in ids]),
                                                       ResearchAnalysis.owner_id == m.owner_id)).all():
        outs = db.scalars(select(AnalysisOutput).where(AnalysisOutput.analysis_id == a.id)).all()
        body = f"Procedure: {a.procedure}\nParameters: {a.params}\nInterpretation (computed): {a.interpretation}\n"
        for o in outs:
            if o.kind in ("table", "json", "text"):
                body += f"\n{o.title}:\n{str(o.content)[:2500]}\n"
        items.append(ContextItem("analysis", f"COMPUTED ANALYSIS OUTPUT (researcher's data) — {a.procedure}", body, private=True,
                                 ref={"type": "analysis", "analysis_id": str(a.id)}))
    return items


def style_block(db: Session, m: Manuscript) -> str:
    wp = db.get(WritingProfile, m.writing_profile_id) if m.writing_profile_id else None
    return style_prompt_block(effective_profile(wp.metrics, wp.qualitative, wp.researcher_overrides) if wp else None)


def requirements_text(m: Manuscript) -> str:
    r = m.requirements or {}
    lines = [f"Title: {m.title}", f"Type: {m.manuscript_type.replace('_', ' ')}", f"Discipline: {m.discipline or 'unspecified'}",
             f"Target journal: {m.target_journal or 'unspecified'}", f"Target length: {m.target_word_count} words",
             f"Language: {m.language_variant} spelling and conventions", f"Citation style: {cit.STYLES.get(m.citation_style, m.citation_style)}"]
    for k in ("topic", "objectives", "research_questions", "methodological_requirements", "special_instructions", "required_structure"):
        if r.get(k):
            lines.append(f"{k.replace('_', ' ').capitalize()}: {r[k]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generation steps
# ---------------------------------------------------------------------------
def generate_outline(db: Session, m: Manuscript, job_id=None) -> dict:
    secs = sorted(m.sections, key=lambda s: s.position)
    items = project_foundation(db, m.owner_id, m.project_id) + reference_context(db, m, m.title + " " + str((m.requirements or {}).get("topic", "")), 18)
    plan = "\n".join(f"- key={s.key}; heading={s.heading}; target_words={s.target_words}" for s in secs)
    has_data = bool(analysis_context(db, m))
    out = run_agent(db, m.owner_id, "writer", "manuscript_outline",
        f"MANUSCRIPT REQUIREMENTS\n{requirements_text(m)}\n\nSECTION PLAN (keep keys and word targets)\n{plan}\n\n"
        f"Empirical analysis outputs available: {'yes' if has_data else 'NO — results/findings must not report findings'}\n\n"
        "Develop the manuscript outline. Return JSON: {central_argument (string), key_terms ([{term, definition}]), "
        "sections: [{key, objective, subsections: [{heading, points: [...], target_words}], evidence_needed: [what evidence/citekeys support it], "
        "outstanding_requirements: [...]}]}. Subsection word targets must sum to the section target. Do not add subsections to the abstract.",
        context=items, project_id=m.project_id, job_id=job_id, json_mode=True, max_tokens=8000)
    data = out.data if isinstance(out.data, dict) else {}
    by_key = {s.get("key"): s for s in data.get("sections", []) if isinstance(s, dict)}
    for s in secs:
        o = by_key.get(s.key) or {}
        if o.get("objective"):
            s.objective = o["objective"]
        if s.key != "abstract":
            s.outline = o.get("subsections") or []
        s.evidence_plan = o.get("evidence_needed") or []
        s.outstanding_requirements = o.get("outstanding_requirements") or []
    m.quality_report = {**(m.quality_report or {}), "central_argument": data.get("central_argument", ""), "key_terms": data.get("key_terms", [])}
    m.status = "outlined"
    db.commit()
    return {"central_argument": data.get("central_argument", ""), "sections": len(by_key), "agent_run": str(out.run_id)}


def _previous_summary(m: Manuscript, upto: int) -> str:
    out = []
    for s in sorted(m.sections, key=lambda x: x.position):
        if s.position >= upto or not s.content:
            continue
        paras = [p for p in s.content.split("\n\n") if p.strip() and not p.startswith("#")]
        gist = " ".join(paras[0].split()[:70]) if paras else ""
        subs = [ln.lstrip("# ").strip() for ln in s.content.splitlines() if ln.startswith("#")]
        out.append(f"{s.heading}: {gist}…" + (f" (subsections: {'; '.join(subs[:6])})" if subs else ""))
    return "\n".join(out) or "(this is the first section written)"


def _clean_output(text: str, heading: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", t)
    lines = t.splitlines()
    if lines and lines[0].lstrip("# ").strip().lower() == heading.lower():  # drop duplicated section heading
        lines = lines[1:]
    return "\n".join(lines).strip()


def write_section(db: Session, m: Manuscript, s: ManuscriptSection, job_id=None, instructions: str = "") -> dict:
    s.status = "generating"
    db.commit()
    meta = m.quality_report or {}
    terms = "; ".join(f"{t.get('term')}: {t.get('definition')}" for t in meta.get("key_terms", []) if isinstance(t, dict))
    data_items = analysis_context(db, m)
    no_data_rule = ""
    if s.key in DATA_SECTIONS and not data_items:
        no_data_rule = ("\nNO EMPIRICAL DATA OR ANALYSIS OUTPUTS ARE AVAILABLE. Do NOT report findings, statistics, quotations from "
                        "participants or themes. Instead: state what will be reported, the analyses planned and table/figure shells, and mark "
                        "every outstanding item as [DATA REQUIRED: description].")
    subs = s.outline if (s.target_words > SUBSECTION_THRESHOLD and s.outline) else [{"heading": None, "points": [], "target_words": s.target_words}]
    written: list[str] = []
    used_sources: list[dict] = []
    runs = []
    for i, sub in enumerate(subs):
        query = f"{m.title}. {s.heading}. {sub.get('heading') or ''} {s.objective} {' '.join(map(str, sub.get('points') or []))}"[:900]
        items = project_foundation(db, m.owner_id, m.project_id) + data_items + reference_context(db, m, query)
        so_far = "\n\n".join(written)[-3000:]
        target = int(sub.get("target_words") or s.target_words / max(1, len(subs)))
        instr = (
            f"MANUSCRIPT REQUIREMENTS\n{requirements_text(m)}\n\nCENTRAL ARGUMENT: {meta.get('central_argument') or 'not yet defined'}\n"
            f"KEY TERMS (use consistently): {terms or 'none defined'}\n\nPREVIOUS SECTIONS (summary):\n{_previous_summary(m, s.position)}\n\n"
            f"NOW WRITE: section '{s.heading}'" + (f", subsection '{sub['heading']}'" if sub.get("heading") else "") + "\n"
            f"Section objective: {s.objective or 'see requirements'}\n"
            + (f"Points to cover: {sub.get('points')}\n" if sub.get("points") else "")
            + (f"Text already written in this section:\n{so_far}\n" if so_far else "")
            + f"Length: approximately {target} words (stay within ±10%).\n"
            f"{no_data_rule}\n"
            "CITATION RULES: cite only CITABLE REFERENCE sources, using exactly their marker, e.g. [@key] or [@key, p. 4] "
            "(locators only when the excerpt shows the page). Combine as [@a; @b]. Never write author-year citations by hand. "
            "If a claim needs support that no citable reference provides, write [CITATION NEEDED: what is needed] instead of inventing a source.\n"
            "Distinguish evidence from your interpretation. Avoid repetition, padding and unsupported generalisations.\n"
            + (f"RESEARCHER INSTRUCTIONS: {instructions}\n" if instructions else "")
            + ("Output Markdown prose only. Begin with the subsection heading as '## " + sub["heading"] + "'." if sub.get("heading")
               else "Output Markdown prose only, without the section heading." + (" Write a single-paragraph structured abstract without citations." if s.key == "abstract" else ""))
        )
        out = run_agent(db, m.owner_id, "writer", f"write_section:{s.key}", instr, context=items, project_id=m.project_id, job_id=job_id,
                        extra_system=style_block(db, m), temperature=0.5, max_tokens=max(2048, int(target * 2.6)))
        runs.append(str(out.run_id))
        written.append(_clean_output(out.text, s.heading))
        used_sources.extend(x for x in out.sources if x.get("type") == "reference")
    content = "\n\n".join(w for w in written if w)
    s.content = content
    s.word_count = count_words(content)
    s.status, s.origin = "draft", "ai"
    s.content_version = (s.content_version or 0) + 1
    s.outstanding_requirements = sorted(set((s.outstanding_requirements or []) + re.findall(r"\[DATA REQUIRED: ([^\]]+)\]", content)))
    s.evidence_used = list({x["citekey"]: x for x in used_sources}.values())
    sync_citations(db, m, s)
    db.commit()
    return {"section": s.key, "words": s.word_count, "target": s.target_words, "agent_runs": runs}


def sync_citations(db: Session, m: Manuscript, s: ManuscriptSection) -> None:
    refs = {r.citekey: r for r in project_references(db, m)}
    for c in db.scalars(select(Citation).where(Citation.section_id == s.id)).all():
        db.delete(c)
    for mk in cit.parse_markers(s.content or ""):
        start = max(s.content.rfind(".", 0, mk["start"]) + 1, s.content.rfind("\n", 0, mk["start"]) + 1)
        claim = s.content[start : mk["end"]].strip()
        for it in mk["items"]:
            r = refs.get(it["key"])
            db.add(Citation(section_id=s.id, reference_id=r.id if r else None, citekey=it["key"], claim_text=claim[:2000],
                            char_offset=mk["start"], locator=it["locator"][:100],
                            is_quotation=bool(cit.QUOTE.search(claim)), alignment_status="unchecked" if r else "unresolved"))
    db.flush()


def check_consistency(db: Session, m: Manuscript, job_id=None) -> dict:
    secs = sorted(m.sections, key=lambda x: x.position)
    body = "\n\n".join(f"# {s.heading}\n{s.content[:5000]}" for s in secs if s.content)
    out = run_agent(db, m.owner_id, "reviewer", "consistency_check",
        f"Check this manuscript for cross-section consistency.\nKey terms: {(m.quality_report or {}).get('key_terms')}\n\n{body[:60000]}\n\n"
        "Return JSON: {terminology_inconsistencies: [...], argument_progression: string, contradictions: [{between, issue}], "
        "repetition: [...], transition_problems: [...], research_question_alignment: string}.",
        context=[], project_id=m.project_id, job_id=job_id, json_mode=True, max_tokens=4000)
    return out.data if isinstance(out.data, dict) else {"raw": out.text}


def check_word_allocation(m: Manuscript, tolerance: float = 0.15) -> dict:
    rows, total = [], 0
    for s in sorted(m.sections, key=lambda x: x.position):
        dev = (s.word_count - s.target_words) / s.target_words if s.target_words else 0
        if s.key != "abstract":
            total += s.word_count
        rows.append({"key": s.key, "heading": s.heading, "target": s.target_words, "actual": s.word_count,
                     "deviation": round(dev, 3), "within_tolerance": abs(dev) <= tolerance})
    body_target = sum(s.target_words for s in m.sections if s.key != "abstract")
    return {"sections": rows, "total_words_excluding_abstract": total, "target_body_words": body_target,
            "total_with_abstract": total + sum(s.word_count for s in m.sections if s.key == "abstract"),
            "references_counted": m.count_references_in_words, "tolerance": tolerance}


def audit_manuscript(db: Session, m: Manuscript, *, alignment: bool = True, max_alignment_checks: int = 25, job_id=None) -> dict:
    refs = project_references(db, m)
    secs = sorted(m.sections, key=lambda x: x.position)
    report = cit.audit_text([{"id": str(s.id), "key": s.key, "heading": s.heading, "content": s.content} for s in secs], refs)
    # hand-written citations that bypass the verified reference list
    for s in secs:
        stripped = cit.CITE_GROUP.sub(" ", s.content or "")
        for mm in re.finditer(r"\(([A-Z][A-Za-z\-]+(?: et al\.)?(?:,? (?:and|&) [A-Z][A-Za-z\-]+)?),? (\d{4}[a-z]?)\)", stripped):
            report["findings"].append({"severity": "high", "type": "unmarked_citation", "section": s.key, "context": mm.group(0),
                                       "message": "Author-year citation not linked to a project reference."})
    checked = []
    if alignment:
        refs_by_id = {r.id: r for r in refs}
        cites = db.scalars(select(Citation).where(Citation.section_id.in_([s.id for s in secs]), Citation.reference_id.is_not(None))).all()
        for c in cites[:max_alignment_checks]:
            r = refs_by_id.get(c.reference_id)
            if not r:
                continue
            items = reference_context_for_claim(db, m, r, c.claim_text)
            try:
                out = run_agent(db, m.owner_id, "auditor", "claim_alignment",
                    f"Claim in the manuscript: \"{c.claim_text}\"\nIt cites [@{r.citekey}].\n"
                    "Based ONLY on the supplied source text, does the source support the claim? Return JSON: "
                    "{verdict: 'supported'|'partially_supported'|'not_supported'|'cannot_determine', explanation, supporting_quote (verbatim or null)}. "
                    "Use cannot_determine when only metadata or an abstract is available and it does not address the claim.",
                    context=items, project_id=m.project_id, job_id=job_id, json_mode=True, temperature=0.0, max_tokens=800)
                d = out.data if isinstance(out.data, dict) else {}
                c.alignment_status = d.get("verdict", "cannot_determine")
                c.alignment_note = d.get("explanation", "")[:2000]
                c.supporting_evidence = {"quote": d.get("supporting_quote"), "basis": items[0].label if items else ""}
            except Exception as e:  # noqa: BLE001
                c.alignment_status, c.alignment_note = "error", str(e)[:500]
            checked.append({"citekey": r.citekey, "claim": c.claim_text[:300], "verdict": c.alignment_status, "note": c.alignment_note})
            if c.alignment_status in ("not_supported", "partially_supported"):
                report["findings"].append({"severity": "high" if c.alignment_status == "not_supported" else "medium",
                                           "type": "citation_mismatch", "citekey": r.citekey, "context": c.claim_text[:200],
                                           "message": f"Claim–source alignment: {c.alignment_status}. {c.alignment_note[:200]}"})
        db.flush()
    report["alignment_checks"] = checked
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    report["note"] = "This audit checks citation integrity. It is not a formal plagiarism check."
    return report


def reference_context_for_claim(db: Session, m: Manuscript, r: Reference, claim: str) -> list[ContextItem]:
    if r.document_id:
        hits = hybrid_search(db, m.owner_id, claim, SearchFilters(document_ids=[r.document_id]), k=3)
        if hits:
            return [_ref_item(r, "\n…\n".join(h["text"] for h in hits), hits[0]["visibility"] == "private_unpublished", "full text excerpts")]
    p = db.get(Publication, r.publication_id) if r.publication_id else None
    if p and p.abstract:
        return [_ref_item(r, p.abstract, False, "abstract only")]
    return [_ref_item(r, "(no source text available — metadata only)", False, "metadata only")]


def critical_review(db: Session, m: Manuscript, job_id=None) -> dict:
    secs = sorted(m.sections, key=lambda x: x.position)
    body = "\n\n".join(f"# {s.heading}\n{s.content}" for s in secs if s.content)
    out = run_agent(db, m.owner_id, "reviewer", "manuscript_review",
        f"Act as a rigorous peer reviewer for {m.target_journal or 'a reputable journal'}.\nRequirements:\n{requirements_text(m)}\n\n{body[:90000]}\n\n"
        "Return JSON: {overall_assessment, contribution_assessment, strengths:[...], major_concerns:[{issue, section, suggestion}], "
        "minor_concerns:[...], methodological_concerns:[...], unsupported_claims:[...], questions_for_author:[...], "
        "readiness: 'not_ready'|'major_revision'|'minor_revision'|'near_ready'}.",
        context=[], project_id=m.project_id, job_id=job_id, json_mode=True, max_tokens=5000)
    return out.data if isinstance(out.data, dict) else {"raw": out.text}


# ---------------------------------------------------------------------------
# Rendering / export
# ---------------------------------------------------------------------------
def rendered(db: Session, m: Manuscript) -> dict:
    refs = project_references(db, m)
    by_key = {r.citekey: r.csl for r in refs}
    secs = sorted(m.sections, key=lambda x: x.position)
    order = cit.cited_keys_in_order([s.content for s in secs])
    numbering = {k: i + 1 for i, k in enumerate([k for k in order if k in by_key])}
    out_secs = [{"key": s.key, "heading": s.heading, "content": cit.render_intext(s.content or "", by_key, m.citation_style, numbering)} for s in secs]
    return {"title": m.title, "sections": out_secs, "bibliography": cit.build_bibliography(order, by_key, m.citation_style),
            "unresolved": [k for k in order if k not in by_key]}
