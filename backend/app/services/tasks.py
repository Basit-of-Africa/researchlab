"""Background job handlers (registered with the durable job queue)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DiscoveryQueueItem,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceExtraction,
    LiteratureWatch,
    Manuscript,
    Publication,
    Reference,
    ResearchIdea,
    TheoreticalFramework,
    ResearchMethod,
    WritingProfile,
)
from app.services import knowledge, manuscript as ms
from app.services.agents import workflows
from app.services.agents.core import ContextItem, run_agent
from app.services.extraction import ExtractionError
from app.services.jobs import JobAwaitingApproval, JobContext, PermanentJobError, enqueue, handler
from app.services.scholarly import clients
from app.services.scholarly.clients import ScholarlyError
from app.services.scholarly.service import upsert_publication, verify_bibliographic, verify_publication
from app.services.style import compute_metrics


def _uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
@handler("ingest_document")
def ingest_document(ctx: JobContext) -> dict:
    db = ctx.db
    v = db.get(DocumentVersion, _uuid(ctx.params["version_id"]))
    if v is None:
        raise PermanentJobError("Document version no longer exists")
    try:
        stats = ctx.step("process", "Extract, chunk, embed and index", lambda: knowledge.process_version(
            db, v, verify_metadata=ctx.params.get("verify_metadata", True)))
    except ExtractionError as e:
        doc = db.get(Document, v.document_id)
        doc.status, doc.status_detail, v.processing_status = "failed", str(e), "failed"
        db.commit()
        raise PermanentJobError(str(e)) from e
    return stats


@handler("reindex_document")
def reindex_document(ctx: JobContext) -> dict:
    db = ctx.db
    doc = db.get(Document, _uuid(ctx.params["document_id"]))
    if not doc or not doc.current_version_id:
        raise PermanentJobError("Document has no indexed version")
    if ctx.params.get("embeddings_only"):
        return ctx.step("embed", "Regenerate embeddings", lambda: knowledge.regenerate_embeddings(db, doc))
    v = db.get(DocumentVersion, doc.current_version_id)
    if not v.storage_key:  # abstract-only record
        return ctx.step("embed", "Regenerate embeddings", lambda: knowledge.regenerate_embeddings(db, doc))

    def _do():
        r = knowledge.reindex_version(db, doc, v)
        db.commit()
        return r

    return ctx.step("reindex", "Re-chunk and re-index", _do)


@handler("writing_style_analysis")
def writing_style_analysis(ctx: JobContext) -> dict:
    db = ctx.db
    wp = db.get(WritingProfile, _uuid(ctx.params["profile_id"]))
    ids = [_uuid(i) for i in wp.sample_document_ids or []]
    texts = []
    for d in db.scalars(select(Document).where(Document.id.in_(ids), Document.owner_id == wp.owner_id)).all():
        v = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
        if v and v.extracted_text:
            texts.append(v.extracted_text)
    if not texts:
        raise PermanentJobError("No processed writing samples; upload and process your own documents first")
    metrics = ctx.step("metrics", "Compute stylometrics", lambda: compute_metrics(texts))
    wp.metrics = metrics
    db.commit()
    if ctx.params.get("with_ai", True):
        excerpt = "\n\n".join(t[:6000] for t in texts[:4])

        def _ai():
            out = run_agent(db, wp.owner_id, "writer", "style_profile",
                "Describe the WRITING STYLE of these samples by the researcher (not their opinions or arguments). Return JSON: "
                "{summary, traits: [specific, observable traits], sentence_construction, paragraph_organisation, vocabulary, "
                "argumentation_patterns, literature_integration, transitions, tone_formality, discussion_patterns}.",
                context=[ContextItem("kb_chunk", "RESEARCHER WRITING SAMPLE", excerpt, private=True)], json_mode=True, job_id=ctx.job.id)
            return {**(out.data or {}), "_generated_by": f"{out.provider}:{out.model}", "_label": "AI interpretation"}

        try:
            wp.qualitative = ctx.step("ai_description", "AI qualitative description", _ai)
        except Exception as e:  # noqa: BLE001 - deterministic metrics remain valid
            wp.qualitative = {"error": f"AI description unavailable: {e}"}
    wp.version = (wp.version or 0) + 1
    db.commit()
    return {"metrics": True, "samples": len(texts)}


# ---------------------------------------------------------------------------
# Scholarly intelligence
# ---------------------------------------------------------------------------
def _run_watch(db, w: LiteratureWatch) -> int:
    since = (w.last_run_at or (datetime.now(timezone.utc) - timedelta(days=365))).date().isoformat()
    added = 0
    recs = []
    if "openalex" in (w.sources or []):
        f = [f"from_created_date:{since}"] if w.last_run_at else []
        y = (w.filters or {}).get("year_from")
        for aid in w.authors or []:
            try:
                r, _ = clients.openalex_search(w.query or "", author_id=aid, per_page=25, extra_filters=f, year_from=y)
                recs.extend(r)
            except ScholarlyError:
                pass
        if w.query:
            r, _ = clients.openalex_search(w.query, per_page=25, extra_filters=f, year_from=y,
                                           open_access=(w.filters or {}).get("open_access"), sort="publication_date:desc")
            recs.extend(r)
    if "crossref" in (w.sources or []) and w.query:
        try:
            recs.extend(clients.crossref_search(w.query, rows=20, year_from=int(since[:4])))
        except ScholarlyError:
            pass
    for rec in recs:
        if not rec.title:
            continue
        p = upsert_publication(db, w.owner_id, rec)
        if not db.scalar(select(DiscoveryQueueItem).where(DiscoveryQueueItem.owner_id == w.owner_id, DiscoveryQueueItem.publication_id == p.id)):
            db.add(DiscoveryQueueItem(owner_id=w.owner_id, watch_id=w.id, publication_id=p.id, status="pending"))
            added += 1
    w.last_run_at, w.last_result_count = datetime.now(timezone.utc), added
    db.commit()
    return added


@handler("literature_watch")
def literature_watch(ctx: JobContext) -> dict:
    w = ctx.db.get(LiteratureWatch, _uuid(ctx.params["watch_id"]))
    if not w:
        raise PermanentJobError("Watch deleted")
    return {"added_to_review_queue": ctx.step("search", "Search sources", lambda: _run_watch(ctx.db, w))}


def schedule_due_watches() -> int:
    db = SessionLocal()
    n = 0
    try:
        now = datetime.now(timezone.utc)
        for w in db.scalars(select(LiteratureWatch).where(LiteratureWatch.is_active.is_(True))).all():
            if w.last_run_at is None or w.last_run_at + timedelta(days=w.frequency_days) <= now:
                from app.models import GenerationJob

                pending = db.scalar(select(GenerationJob).where(GenerationJob.job_type == "literature_watch",
                                                                GenerationJob.status.in_(["queued", "running"]),
                                                                GenerationJob.params["watch_id"].astext == str(w.id)))
                if not pending:
                    enqueue(db, w.owner_id, "literature_watch", {"watch_id": str(w.id)}, title=f"Literature watch: {w.name}")
                    n += 1
        db.commit()
    finally:
        db.close()
    return n


@handler("verify_references")
def verify_references(ctx: JobContext) -> dict:
    db = ctx.db
    q = select(Reference).where(Reference.owner_id == ctx.job.owner_id)
    if ctx.params.get("project_id"):
        q = q.where(Reference.project_id == _uuid(ctx.params["project_id"]))
    if ctx.params.get("reference_ids"):
        q = q.where(Reference.id.in_([_uuid(i) for i in ctx.params["reference_ids"]]))
    refs = db.scalars(q).all()
    counts: dict[str, int] = {}
    for i, r in enumerate(refs):
        def _v(r=r):
            res = verify_bibliographic(db, r.owner_id, r.csl or {}, reference_id=r.id)
            r.verification_status = res["status"]
            r.verification_detail = {"comparison": res["comparison"], "checks": res["checks"][-3:]}
            r.verified_at = datetime.now(timezone.utc)
            db.commit()
            return res["status"]
        st = ctx.step(f"ref:{r.id}", f"Verify {r.citekey}", _v)
        counts[st] = counts.get(st, 0) + 1
        ctx.progress((i + 1) / max(1, len(refs)))
    return {"checked": len(refs), "by_status": counts}


@handler("verify_publications")
def verify_publications(ctx: JobContext) -> dict:
    db = ctx.db
    ids = [_uuid(i) for i in ctx.params["publication_ids"]]
    out = {}
    for p in db.scalars(select(Publication).where(Publication.id.in_(ids), Publication.owner_id == ctx.job.owner_id)).all():
        out[str(p.id)] = ctx.step(f"pub:{p.id}", f"Verify {p.title[:60]}", lambda p=p: (verify_publication(db, p), db.commit())[0]["status"])
    return {"results": out}


@handler("evidence_extraction")
def evidence_extraction(ctx: JobContext) -> dict:
    db = ctx.db
    owner = ctx.job.owner_id
    items: list[ContextItem] = []
    pub = db.get(Publication, _uuid(ctx.params["publication_id"])) if ctx.params.get("publication_id") else None
    doc = db.get(Document, _uuid(ctx.params["document_id"])) if ctx.params.get("document_id") else None
    basis = "abstract"
    if doc and doc.owner_id == owner:
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.chunk_index).limit(40)).all()
        for c in chunks:
            loc = f"p. {c.page_start}" if c.page_start else ""
            items.append(ContextItem("kb_chunk", f"{doc.title[:80]} — section: {c.section[:60] or 'n/a'} {loc}", c.text,
                                     private=doc.visibility == "private_unpublished",
                                     ref={"type": "chunk", "chunk_id": str(c.id), "page": c.page_start, "section": c.section}))
        basis = "full_text" if doc.source_kind != "doi" else "abstract"
    elif pub and pub.owner_id == owner:
        if not pub.abstract:
            raise PermanentJobError("This publication has no abstract or full text in the knowledge base; evidence cannot be extracted.")
        items.append(ContextItem("publication", f"{pub.title} — ABSTRACT ONLY", pub.abstract, ref={"type": "publication", "publication_id": str(pub.id)}))
    else:
        raise PermanentJobError("Source not found")
    res = ctx.step("extract", "Extract evidence", lambda: workflows.run_evidence_extraction(db, owner, items, basis, job_id=ctx.job.id))
    by_sid = {s["sid"]: s for s in res["sources"]}
    fields = {}
    for k, v in (res["fields"] or {}).items():
        if not isinstance(v, dict):
            v = {"value": v}
        v["locations"] = [by_sid[s] for s in (v.get("sources") or []) if s in by_sid]
        fields[k] = v
    ee = EvidenceExtraction(owner_id=owner, publication_id=pub.id if pub else (doc.publication_id if doc else None),
                            document_id=doc.id if doc else None, basis=basis, fields=fields, provider=res["provider"], model=res["model"])
    db.add(ee)
    db.commit()
    return {"evidence_extraction_id": str(ee.id), "basis": basis}


# ---------------------------------------------------------------------------
# Research intelligence
# ---------------------------------------------------------------------------
@handler("idea_assessment")
def idea_assessment(ctx: JobContext) -> dict:
    db = ctx.db
    idea = db.get(ResearchIdea, _uuid(ctx.params["idea_id"]))
    res = ctx.step("assess", "Interrogate idea (strategist → literature → strategist → reviewer)", lambda: workflows.run_idea_assessment(
        db, idea.owner_id, idea.statement, idea.researcher_context, project_id=idea.project_id, job_id=ctx.job.id,
        use_openalex=ctx.params.get("use_openalex", True)))
    idea.assessment = res
    idea.status = "assessed"
    db.commit()
    return {"idea_id": str(idea.id)}


@handler("challenge")
def challenge(ctx: JobContext) -> dict:
    p = ctx.params
    res = ctx.step("challenge", "Research Challenge Mode", lambda: workflows.run_challenge(
        ctx.db, ctx.job.owner_id, p["argument"], project_id=ctx.job.project_id, job_id=ctx.job.id, use_openalex=p.get("use_openalex", True)))
    return res


@handler("theory_analysis")
def theory_analysis(ctx: JobContext) -> dict:
    db, p = ctx.db, ctx.params
    res = ctx.step("analyse", "Theoretical analysis", lambda: workflows.run_theory_analysis(
        db, ctx.job.owner_id, p["problem"], project_id=ctx.job.project_id, job_id=ctx.job.id,
        candidate_theories=p.get("candidate_theories"), use_openalex=p.get("use_openalex", True)))
    created = []
    by_sid = {s["sid"]: s for s in res.get("sources", [])}
    a = res.get("analysis") or {}
    model = a.get("conceptual_model") or {}
    for t in a.get("theories", []) or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        fw = TheoreticalFramework(owner_id=ctx.job.owner_id, project_id=ctx.job.project_id, name=t["name"][:300],
                                  origins=t.get("origins", ""), assumptions=t.get("central_assumptions") or [], constructs=t.get("constructs") or [],
                                  strengths=t.get("strengths") or [], limitations=t.get("limitations") or [], suitability=t.get("suitability", ""),
                                  source_refs=[by_sid[s] for s in t.get("sources") or [] if s in by_sid], status="proposed", origin="ai")
        db.add(fw)
        db.flush()
        created.append(str(fw.id))
    if model.get("nodes"):
        fw = TheoreticalFramework(owner_id=ctx.job.owner_id, project_id=ctx.job.project_id, name="Proposed conceptual model",
                                  model=model, hypotheses=a.get("hypotheses") or [], suitability=a.get("comparison", ""), status="proposed", origin="ai")
        db.add(fw)
        db.flush()
        created.append(str(fw.id))
    db.commit()
    return {**res, "framework_ids": created}


@handler("method_advice")
def method_advice(ctx: JobContext) -> dict:
    db, p = ctx.db, ctx.params
    res = ctx.step("advise", "Methodology advice", lambda: workflows.run_method_advice(
        db, ctx.job.owner_id, p["problem"], project_id=ctx.job.project_id, job_id=ctx.job.id, preferences=p.get("preferences", "")))
    rec = (res.get("advice") or {}).get("recommended") or {}
    m = None
    if ctx.job.project_id and rec:
        m = ResearchMethod(owner_id=ctx.job.owner_id, project_id=ctx.job.project_id, status="proposed", origin="ai",
                           **{k: (rec.get(k) if isinstance(rec.get(k), (str, list)) else str(rec.get(k) or ""))
                              for k in ("approach", "design", "population", "sampling", "sample_size", "variables", "instruments",
                                        "data_collection", "reliability_validity", "ethics", "analysis_plan", "rationale")})
        if not isinstance(m.variables, list):
            m.variables = []
        db.add(m)
        db.commit()
    return {**res, "method_id": str(m.id) if m else None}


@handler("qual_code_suggestions")
def qual_code_suggestions(ctx: JobContext) -> dict:
    from app.models import QualCode, QualSegment

    db, p = ctx.db, ctx.params
    segs = db.scalars(select(QualSegment).where(QualSegment.project_id == ctx.job.project_id).limit(80)).all()
    if not segs:
        raise PermanentJobError("No text segments to code")
    existing = [c.name for c in db.scalars(select(QualCode).where(QualCode.project_id == ctx.job.project_id)).all()]
    items = [ContextItem("qual", f"SEGMENT {s.id} ({s.source_label})", s.text, private=True, ref={"type": "segment", "segment_id": str(s.id)}) for s in segs]

    def _go():
        out = run_agent(db, ctx.job.owner_id, "analyst", "qual_code_suggestions",
            f"Research focus: {p.get('focus') or 'not specified'}\nExisting codebook: {existing}\n"
            "Propose inductive codes grounded ONLY in the segments. Return JSON: {codes: [{name, description, theme, segment_sids: [S-ids]}]}. "
            "These are suggestions for the researcher to approve; do not over-interpret.", context=items, project_id=ctx.job.project_id,
            job_id=ctx.job.id, json_mode=True)
        return {"data": out.data, "sources": out.sources}

    res = ctx.step("suggest", "Suggest codes", _go)
    sid_map = {s["sid"]: s.get("segment_id") for s in res["sources"]}
    created = []
    from app.models import QualCoding

    for c in (res["data"] or {}).get("codes", []):
        name = (c.get("name") or "").strip()[:200]
        if not name or name in existing:
            continue
        code = QualCode(owner_id=ctx.job.owner_id, project_id=ctx.job.project_id, name=name, description=c.get("description", ""),
                        theme=(c.get("theme") or "")[:200], origin="ai", status="proposed")
        db.add(code)
        db.flush()
        existing.append(name)
        for sid in c.get("segment_sids") or []:
            if sid_map.get(sid):
                db.add(QualCoding(segment_id=_uuid(sid_map[sid]), code_id=code.id, origin="ai", status="proposed"))
        created.append(name)
    db.commit()
    return {"proposed_codes": created}


# ---------------------------------------------------------------------------
# Manuscripts
# ---------------------------------------------------------------------------
@handler("manuscript_outline")
def manuscript_outline(ctx: JobContext) -> dict:
    m = ctx.db.get(Manuscript, ctx.job.manuscript_id)
    return ctx.step("outline", "Develop outline", lambda: ms.generate_outline(ctx.db, m, job_id=ctx.job.id), force=bool(ctx.params.get("force")))


@handler("section_generate")
def section_generate(ctx: JobContext) -> dict:
    db = ctx.db
    m = db.get(Manuscript, ctx.job.manuscript_id)
    sec = next((s for s in m.sections if s.key == ctx.params["section_key"]), None)
    if sec is None:
        raise PermanentJobError("Section not found")
    return ctx.step(f"write:{sec.key}", f"Write {sec.heading}", lambda: ms.write_section(db, m, sec, job_id=ctx.job.id,
                                                                                         instructions=ctx.params.get("instructions", "")))


@handler("manuscript_generate")
def manuscript_generate(ctx: JobContext) -> dict:
    """Complete-manuscript mode: every phase is a persisted, resumable step."""
    db = ctx.db
    m = db.get(Manuscript, ctx.job.manuscript_id)
    secs = sorted(m.sections, key=lambda s: s.position)
    body = [s for s in secs if s.key != "abstract"]
    abstract = [s for s in secs if s.key == "abstract"]
    regenerate = set(ctx.params.get("regenerate_sections") or [])
    plan = [("outline", "Develop outline and section objectives")] + [(f"write:{s.key}", f"Write {s.heading}") for s in body + abstract] + [
        ("consistency", "Cross-check consistency"), ("audit", "Verify citations and claim alignment"), ("words", "Check word allocation"),
        ("review", "Critical review"), ("assemble", "Assemble manuscript version")]
    ctx.plan_steps(plan)
    total = len(plan)
    if not all(s.outline or s.key == "abstract" for s in secs) or ctx.params.get("force_outline"):
        ctx.step("outline", plan[0][1], lambda: ms.generate_outline(db, m, job_id=ctx.job.id))
    else:
        ctx.step("outline", plan[0][1], lambda: {"skipped": "existing outline reused"})
    if ctx.params.get("pause_after_outline") and not ctx.params.get("outline_approved"):
        raise JobAwaitingApproval("Outline ready for researcher approval. Approve to continue generation.", {"manuscript_id": str(m.id)})
    m.status = "generating"
    db.commit()
    done = 1
    keep_existing = ctx.params.get("keep_existing", True)
    for s in body + abstract:  # abstract last, once the paper exists
        # A step that already succeeded in this job is never re-run (resume semantics).
        def write(s=s):
            if s.key not in regenerate and s.status == "approved":
                return {"skipped": "approved by researcher"}
            if s.key not in regenerate and keep_existing and s.content and s.status != "generating":
                return {"skipped": "existing draft kept"}
            return ms.write_section(db, m, s, job_id=ctx.job.id)

        ctx.step(f"write:{s.key}", f"Write {s.heading}", write)
        done += 1
        ctx.progress(done / total)
    report = dict(m.quality_report or {})
    report["consistency"] = ctx.step("consistency", "Cross-check consistency", lambda: ms.check_consistency(db, m, job_id=ctx.job.id))
    report["citation_audit"] = ctx.step("audit", "Verify citations", lambda: (lambda r: (db.commit(), r)[1])(ms.audit_manuscript(db, m, job_id=ctx.job.id)))
    report["word_allocation"] = ctx.step("words", "Check word allocation", lambda: ms.check_word_allocation(m))
    report["critical_review"] = ctx.step("review", "Critical review", lambda: ms.critical_review(db, m, job_id=ctx.job.id))
    m.quality_report = report
    db.commit()
    vid = ctx.step("assemble", "Assemble manuscript version", lambda: (lambda v: (db.commit(), str(v.id))[1])(
        ms.snapshot(db, m, f"AI-generated draft (job {str(ctx.job.id)[:8]})", created_by="ai")))
    m.status = "draft_complete"
    db.commit()
    return {"manuscript_id": str(m.id), "version_id": vid, "total_words": report["word_allocation"]["total_words_excluding_abstract"]}


@handler("manuscript_audit")
def manuscript_audit(ctx: JobContext) -> dict:
    db = ctx.db
    m = db.get(Manuscript, ctx.job.manuscript_id)
    rep = ms.audit_manuscript(db, m, alignment=ctx.params.get("alignment", True), job_id=ctx.job.id)
    m.quality_report = {**(m.quality_report or {}), "citation_audit": rep, "word_allocation": ms.check_word_allocation(m)}
    db.commit()
    return {"summary": rep["summary"]}


@handler("manuscript_review")
def manuscript_review(ctx: JobContext) -> dict:
    db = ctx.db
    m = db.get(Manuscript, ctx.job.manuscript_id)
    rev = ms.critical_review(db, m, job_id=ctx.job.id)
    m.quality_report = {**(m.quality_report or {}), "critical_review": rev}
    db.commit()
    return {"readiness": rev.get("readiness")}
