from __future__ import annotations

import hashlib
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, owned, owned_project, parse_uuid
from app.config import get_settings
from app.models import (
    AnalysisOutput,
    AnalysisScript,
    Dataset,
    Document,
    DocumentVersion,
    QualCode,
    QualCoding,
    QualSegment,
    ResearchAnalysis,
    ResearchDecision,
    User,
)
from app.services.agents.core import ContextItem, run_agent
from app.services.analysis import procedures as P
from app.services.analysis.interpret import interpret
from app.services.analysis.runner import (
    TRANSFORMS,
    AnalysisError,
    apply_transformations,
    detect_format,
    df_preview,
    infer_schema,
    load_dataframe,
    result_hash,
    run_script,
)
from app.services.audit import audit
from app.services.jobs import enqueue
from app.services.storage import get_storage, make_key, sha256_bytes

router = APIRouter(prefix="/analysis", tags=["analysis"])


class TransformIn(BaseModel):
    steps: list[dict]
    name: str = ""
    confirm: bool = False  # explicit researcher approval required to create the derived dataset


class RunIn(BaseModel):
    dataset_id: str
    procedure: str
    params: dict = {}
    project_id: str | None = None


class SegmentIn(BaseModel):
    project_id: str
    text: str = Field(min_length=1)
    source_label: str = ""
    dataset_id: str | None = None
    document_id: str | None = None
    char_start: int = 0
    char_end: int = 0
    memo: str = ""


class SegmentFromSourceIn(BaseModel):
    project_id: str
    dataset_id: str | None = None
    document_id: str | None = None
    split: str = Field(default="paragraph", pattern="^(paragraph|speaker_turn)$")


class CodeIn(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    theme: str = ""
    color: str = ""


class CodingIn(BaseModel):
    segment_id: str
    code_id: str


def ds_out(d: Dataset) -> dict:
    return {"id": str(d.id), "name": d.name, "kind": d.kind, "filename": d.filename, "file_format": d.file_format, "size_bytes": d.size_bytes,
            "n_rows": d.n_rows, "n_cols": d.n_cols, "schema": d.schema, "parent_dataset_id": str(d.parent_dataset_id) if d.parent_dataset_id else None,
            "transformations": d.transformations, "approved_by_researcher": d.approved_by_researcher, "description": d.description,
            "project_id": str(d.project_id) if d.project_id else None, "content_hash": d.content_hash, "created_at": d.created_at.isoformat()}


def an_out(a: ResearchAnalysis, db: Session) -> dict:
    outs = db.scalars(select(AnalysisOutput).where(AnalysisOutput.analysis_id == a.id)).all()
    script = db.scalar(select(AnalysisScript).where(AnalysisScript.analysis_id == a.id))
    return {"id": str(a.id), "dataset_id": str(a.dataset_id), "procedure": a.procedure, "label": P.PROCEDURES.get(a.procedure, {}).get("label"),
            "params": a.params, "status": a.status, "assumptions": a.assumptions, "interpretation": a.interpretation,
            "ai_interpretation": a.ai_interpretation, "error": a.error, "ran_at": a.ran_at.isoformat() if a.ran_at else None,
            "reproducible": a.reproducible, "reproduced_at": a.reproduced_at.isoformat() if a.reproduced_at else None,
            "script_id": str(script.id) if script else None, "script_hash": script.code_hash if script else None,
            "outputs": [{"id": str(o.id), "kind": o.kind, "title": o.title, "content": o.content if o.kind != "figure" else None,
                         "has_file": bool(o.storage_key)} for o in outs]}


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
@router.get("/datasets")
def list_datasets(project_id: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(Dataset).where(Dataset.owner_id == user.id)
    if project_id:
        q = q.where(Dataset.project_id == parse_uuid(project_id))
    return [ds_out(d) for d in db.scalars(q.order_by(Dataset.created_at.desc())).all()]


@router.post("/datasets")
async def upload_dataset(file: UploadFile = File(...), name: str = Form(""), project_id: str = Form(""), description: str = Form(""),
                         user: User = CurrentUser, db: Session = DB) -> dict:
    data = await file.read()
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Dataset exceeds upload limit")
    try:
        fmt = detect_format(file.filename or "")
    except AnalysisError as e:
        raise HTTPException(415, str(e)) from e
    kind, schema, n_rows, n_cols = "tabular", [], None, None
    if fmt == "txt":
        kind = "text"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "Text datasets must be UTF-8") from None
        n_rows = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    else:
        try:
            df = load_dataframe(data, fmt)
        except AnalysisError as e:
            raise HTTPException(422, str(e)) from e
        schema, n_rows, n_cols = infer_schema(df), len(df), df.shape[1]
    h = sha256_bytes(data)
    key = make_key(user.id, "datasets", file.filename or "data", h)
    get_storage().put(key, data)
    d = Dataset(owner_id=user.id, project_id=owned_project(db, project_id, user).id if project_id else None, name=name or (file.filename or "dataset"),
                kind=kind, storage_key=key, filename=file.filename or "", file_format=fmt, size_bytes=len(data), content_hash=h,
                n_rows=n_rows, n_cols=n_cols, schema=schema, description=description)
    db.add(d)
    audit(db, user.id, "dataset.upload", "dataset", d.id, project_id=d.project_id, details={"filename": file.filename, "sha256": h})
    db.commit()
    return ds_out(d)


@router.get("/datasets/{did}")
def get_dataset(did: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Dataset, did, user)
    out = ds_out(d)
    data = get_storage().get(d.storage_key)
    if d.kind == "tabular":
        out["preview"] = df_preview(load_dataframe(data, d.file_format))
    else:
        out["preview_text"] = data.decode("utf-8", "ignore")[:20000]
    return out


@router.delete("/datasets/{did}")
def delete_dataset(did: str, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Dataset, did, user)
    audit(db, user.id, "dataset.delete", "dataset", d.id, details={"name": d.name, "sha256": d.content_hash})
    key = d.storage_key
    db.delete(d)
    db.commit()
    try:
        get_storage().delete(key)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


@router.get("/datasets/{did}/file")
def download_dataset(did: str, user: User = CurrentUser, db: Session = DB):
    d = owned(db, Dataset, did, user)
    return Response(get_storage().get(d.storage_key), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d.filename or "data." + d.file_format}"'})


@router.post("/datasets/{did}/transform")
def transform(did: str, body: TransformIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Preview (confirm=false) or create a derived dataset (confirm=true). The source is never modified."""
    src = owned(db, Dataset, did, user)
    if src.kind != "tabular":
        raise HTTPException(400, "Transformations apply to tabular datasets")
    for st in body.steps:
        if st.get("op") not in TRANSFORMS:
            raise HTTPException(422, f"Unsupported op {st.get('op')}; allowed: {sorted(TRANSFORMS)}")
    df = load_dataframe(get_storage().get(src.storage_key), src.file_format)
    try:
        out, log = apply_transformations(df, body.steps)
    except (AnalysisError, KeyError, ValueError) as e:
        raise HTTPException(422, f"Transformation failed: {e}") from e
    if not body.confirm:
        return {"preview": df_preview(out), "log": log, "schema": infer_schema(out), "requires_confirmation": True}
    buf = io.BytesIO()
    out.to_csv(buf, index=False)
    data = buf.getvalue()
    h = sha256_bytes(data)
    key = make_key(user.id, "datasets", f"derived-{src.filename or 'data'}.csv", h)
    get_storage().put(key, data)
    d = Dataset(owner_id=user.id, project_id=src.project_id, name=body.name or f"{src.name} (derived)", kind="tabular", storage_key=key,
                filename=f"derived-{(src.filename or 'data').rsplit('.', 1)[0]}.csv", file_format="csv", size_bytes=len(data), content_hash=h,
                n_rows=len(out), n_cols=out.shape[1], schema=infer_schema(out), parent_dataset_id=src.id,
                transformations=[*(src.transformations or []), *log], approved_by_researcher=True)
    db.add(d)
    db.flush()
    db.add(ResearchDecision(owner_id=user.id, project_id=src.project_id, decision_type="data", subject=f"Derived dataset from {src.name}",
                            decision="approved", payload={"steps": log, "source_dataset": str(src.id), "derived_dataset": str(d.id)},
                            entity_type="dataset", entity_id=d.id))
    audit(db, user.id, "dataset.transform", "dataset", d.id, project_id=src.project_id, details={"source": str(src.id), "steps": len(log)})
    db.commit()
    return ds_out(d)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
@router.get("/procedures")
def procedures(user: User = CurrentUser) -> dict:
    return {k: {"label": v["label"], "params": v["params"]} for k, v in P.PROCEDURES.items()} | {
        "_unsupported": {"sem": "Structural equation modelling is not bundled (semopy/lavaan). Export the dataset and a script, or install semopy.",
                         "r": "R execution is not available in the free-tier deployment; scripts are Python (pandas/statsmodels/scipy)."}}


def _store_outputs(db, a: ResearchAnalysis, res: dict, owner_id) -> None:
    for o in db.scalars(select(AnalysisOutput).where(AnalysisOutput.analysis_id == a.id)).all():
        db.delete(o)
    db.add(AnalysisOutput(analysis_id=a.id, kind="json", title="Result", content=res.get("result", {}), output_hash=result_hash(res)))
    for t in res.get("tables", []):
        db.add(AnalysisOutput(analysis_id=a.id, kind="table", title=t.get("title", "Table"), content=t))
    for f in res.get("figures", []):
        key = make_key(owner_id, "analysis", f["file"], hashlib.sha256(f["bytes"]).hexdigest())
        get_storage().put(key, f["bytes"], "image/png")
        db.add(AnalysisOutput(analysis_id=a.id, kind="figure", title=f.get("title", "Figure"), storage_key=key, content={"file": f["file"]}))
    for w in res.get("warnings", []):
        db.add(AnalysisOutput(analysis_id=a.id, kind="text", title="Warning", content={"text": w}))


@router.post("/run")
def run(body: RunIn, user: User = CurrentUser, db: Session = DB) -> dict:
    d = owned(db, Dataset, body.dataset_id, user)
    if d.kind != "tabular":
        raise HTTPException(400, "Statistical procedures need a tabular dataset")
    if not d.approved_by_researcher:
        raise HTTPException(400, "Dataset not approved for analysis")
    try:
        params = P.validate_params(body.procedure, body.params, [c["name"] for c in d.schema])
    except P.ParamError as e:
        raise HTTPException(422, str(e)) from e
    code = P.build_script(body.procedure, params, d.file_format)
    a = ResearchAnalysis(owner_id=user.id, project_id=owned_project(db, body.project_id, user).id if body.project_id else d.project_id,
                         dataset_id=d.id, procedure=body.procedure, params=params, status="running")
    db.add(a)
    db.flush()
    import pandas, scipy, statsmodels, sys  # noqa: E401

    db.add(AnalysisScript(analysis_id=a.id, code=code, code_hash=hashlib.sha256(code.encode()).hexdigest(),
                          runtime_info={"python": sys.version.split()[0], "pandas": pandas.__version__, "scipy": scipy.__version__,
                                        "statsmodels": statsmodels.__version__, "dataset_sha256": d.content_hash}))
    try:
        res = run_script(code, get_storage().get(d.storage_key), d.file_format)
    except AnalysisError as e:
        a.status, a.error = "failed", str(e)
        db.commit()
        raise HTTPException(422, str(e)) from e
    _store_outputs(db, a, res, user.id)
    a.status, a.ran_at, a.assumptions = "succeeded", datetime.now(timezone.utc), res.get("assumptions", [])
    a.interpretation = interpret(body.procedure, res)
    audit(db, user.id, "analysis.run", "research_analysis", a.id, project_id=a.project_id, details={"procedure": body.procedure})
    db.commit()
    return an_out(a, db)


@router.get("/analyses")
def list_analyses(dataset_id: str | None = None, project_id: str | None = None, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    q = select(ResearchAnalysis).where(ResearchAnalysis.owner_id == user.id)
    if dataset_id:
        q = q.where(ResearchAnalysis.dataset_id == parse_uuid(dataset_id))
    if project_id:
        q = q.where(ResearchAnalysis.project_id == parse_uuid(project_id))
    return [an_out(a, db) for a in db.scalars(q.order_by(ResearchAnalysis.created_at.desc()).limit(200)).all()]


@router.get("/analyses/{aid}")
def get_analysis(aid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    return an_out(owned(db, ResearchAnalysis, aid, user), db)


@router.post("/analyses/{aid}/reproduce")
def reproduce(aid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    """Re-run the stored script against the stored dataset and compare output hashes."""
    a = owned(db, ResearchAnalysis, aid, user)
    d = db.get(Dataset, a.dataset_id)
    s = db.scalar(select(AnalysisScript).where(AnalysisScript.analysis_id == a.id))
    orig = db.scalar(select(AnalysisOutput).where(AnalysisOutput.analysis_id == a.id, AnalysisOutput.kind == "json"))
    data = get_storage().get(d.storage_key)
    if sha256_bytes(data) != d.content_hash:
        raise HTTPException(409, "Dataset file no longer matches its recorded hash")
    res = run_script(s.code, data, d.file_format)
    same = result_hash(res) == (orig.output_hash if orig else None)
    a.reproducible, a.reproduced_at = same, datetime.now(timezone.utc)
    db.commit()
    return {"reproducible": same, "original_hash": orig.output_hash if orig else None, "new_hash": result_hash(res), "result": res.get("result")}


@router.get("/analyses/{aid}/script")
def download_script(aid: str, user: User = CurrentUser, db: Session = DB):
    a = owned(db, ResearchAnalysis, aid, user)
    s = db.scalar(select(AnalysisScript).where(AnalysisScript.analysis_id == a.id))
    return Response(s.code, media_type="text/x-python", headers={"Content-Disposition": f'attachment; filename="{a.procedure}_{str(a.id)[:8]}.py"'})


@router.get("/outputs/{oid}/figure")
def figure(oid: str, user: User = CurrentUser, db: Session = DB):
    o = db.get(AnalysisOutput, parse_uuid(oid))
    a = db.get(ResearchAnalysis, o.analysis_id) if o else None
    if not o or not a or a.owner_id != user.id or not o.storage_key:
        raise HTTPException(404, "Figure not found")
    return Response(get_storage().get(o.storage_key), media_type="image/png")


@router.get("/outputs/{oid}/csv")
def table_csv(oid: str, user: User = CurrentUser, db: Session = DB):
    o = db.get(AnalysisOutput, parse_uuid(oid))
    a = db.get(ResearchAnalysis, o.analysis_id) if o else None
    if not o or not a or a.owner_id != user.id or o.kind != "table":
        raise HTTPException(404, "Table not found")
    import pandas as pd

    t = o.content
    csv = pd.DataFrame(t.get("rows", []), columns=t.get("columns")).to_csv(index=False)
    return Response(csv, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="table_{str(o.id)[:8]}.csv"'})


@router.post("/analyses/{aid}/interpret")
def ai_interpret(aid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    a = owned(db, ResearchAnalysis, aid, user)
    outs = db.scalars(select(AnalysisOutput).where(AnalysisOutput.analysis_id == a.id, AnalysisOutput.kind.in_(["json", "table"]))).all()
    body = f"Procedure: {a.procedure}\nParameters: {a.params}\nAssumptions: {a.assumptions}\nDeterministic summary: {a.interpretation}\n" + "\n".join(
        f"{o.title}: {str(o.content)[:4000]}" for o in outs)
    out = run_agent(db, user.id, "analyst", "interpret_analysis",
        "Interpret these computed results for an academic write-up. Use only the numbers given. Distinguish statistical significance from "
        "substantive importance, state which assumptions should be checked, and suggest robustness checks. Plain text, 2-4 paragraphs.",
        context=[ContextItem("analysis", "COMPUTED ANALYSIS OUTPUT", body, private=True)], project_id=a.project_id, max_tokens=1500)
    a.ai_interpretation = out.text
    db.commit()
    return {"ai_interpretation": out.text, "label": "AI interpretation — verify against the tables", "excluded_private": out.excluded_private}


# ---------------------------------------------------------------------------
# Qualitative analysis
# ---------------------------------------------------------------------------
def seg_out(s: QualSegment, codes: list[dict]) -> dict:
    return {"id": str(s.id), "text": s.text, "source_label": s.source_label, "memo": s.memo, "char_start": s.char_start, "char_end": s.char_end,
            "dataset_id": str(s.dataset_id) if s.dataset_id else None, "document_id": str(s.document_id) if s.document_id else None, "codes": codes}


@router.post("/qual/segments")
def add_segment(body: SegmentIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, body.project_id, user)
    s = QualSegment(owner_id=user.id, project_id=p.id, text=body.text, source_label=body.source_label, char_start=body.char_start,
                    char_end=body.char_end, memo=body.memo, dataset_id=owned(db, Dataset, body.dataset_id, user).id if body.dataset_id else None,
                    document_id=owned(db, Document, body.document_id, user).id if body.document_id else None)
    db.add(s)
    db.commit()
    return seg_out(s, [])


@router.post("/qual/segments/from-source")
def segments_from_source(body: SegmentFromSourceIn, user: User = CurrentUser, db: Session = DB) -> dict:
    """Split a transcript (text dataset or knowledge-base document) into codable segments."""
    p = owned_project(db, body.project_id, user)
    if body.dataset_id:
        d = owned(db, Dataset, body.dataset_id, user)
        text, label, kw = get_storage().get(d.storage_key).decode("utf-8", "ignore"), d.name, {"dataset_id": d.id}
    elif body.document_id:
        doc = owned(db, Document, body.document_id, user)
        v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
        if not v:
            raise HTTPException(400, "Document has not been processed")
        text, label, kw = v.extracted_text, doc.title, {"document_id": doc.id}
    else:
        raise HTTPException(422, "Provide dataset_id or document_id")
    if body.split == "speaker_turn":
        parts = [m for m in re.finditer(r"(?m)^([A-Z][\w .]{0,30}):\s*(.+?)(?=^\S[\w .]{0,30}:|\Z)", text, re.S)]
        chunks = [(m.start(), m.end(), m.group(0).strip()) for m in parts]
    else:
        chunks, pos = [], 0
        for para in re.split(r"(\n\s*\n)", text):
            if para.strip() and not re.fullmatch(r"\n\s*\n", para):
                chunks.append((pos, pos + len(para), para.strip()))
            pos += len(para)
    n = 0
    for a, b, t in chunks:
        if len(t) >= 3:
            db.add(QualSegment(owner_id=user.id, project_id=p.id, text=t[:20000], source_label=label, char_start=a, char_end=b, **kw))
            n += 1
    db.commit()
    return {"segments_created": n}


@router.get("/qual/segments")
def list_segments(project_id: str, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    p = owned_project(db, project_id, user)
    segs = db.scalars(select(QualSegment).where(QualSegment.project_id == p.id).order_by(QualSegment.source_label, QualSegment.char_start)).all()
    rows = db.execute(select(QualCoding, QualCode).join(QualCode).where(QualCode.project_id == p.id)).all()
    by_seg = defaultdict(list)
    for cd, code in rows:
        by_seg[cd.segment_id].append({"coding_id": str(cd.id), "code_id": str(code.id), "name": code.name, "status": cd.status, "origin": cd.origin})
    return [seg_out(s, by_seg.get(s.id, [])) for s in segs]


@router.delete("/qual/segments/{sid}")
def delete_segment(sid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    db.delete(owned(db, QualSegment, sid, user))
    db.commit()
    return {"ok": True}


def code_out(c: QualCode) -> dict:
    return {"id": str(c.id), "name": c.name, "description": c.description, "theme": c.theme, "origin": c.origin, "status": c.status, "color": c.color}


@router.get("/qual/codes")
def list_codes(project_id: str, user: User = CurrentUser, db: Session = DB) -> list[dict]:
    p = owned_project(db, project_id, user)
    return [code_out(c) for c in db.scalars(select(QualCode).where(QualCode.project_id == p.id).order_by(QualCode.theme, QualCode.name)).all()]


@router.post("/qual/codes")
def add_code(body: CodeIn, user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, body.project_id, user)
    if db.scalar(select(QualCode).where(QualCode.project_id == p.id, QualCode.name == body.name)):
        raise HTTPException(409, "Code exists")
    c = QualCode(owner_id=user.id, project_id=p.id, **body.model_dump(exclude={"project_id"}))
    db.add(c)
    db.commit()
    return code_out(c)


@router.put("/qual/codes/{cid}")
def edit_code(cid: str, body: dict, user: User = CurrentUser, db: Session = DB) -> dict:
    c = owned(db, QualCode, cid, user)
    for k in ("name", "description", "theme", "color"):
        if k in body:
            setattr(c, k, str(body[k])[:200 if k != "description" else 5000])
    if body.get("status") in ("approved", "rejected"):
        c.status = body["status"]
        for cd in db.scalars(select(QualCoding).where(QualCoding.code_id == c.id, QualCoding.status == "proposed")).all():
            cd.status = body["status"]
    db.commit()
    return code_out(c)


@router.delete("/qual/codes/{cid}")
def delete_code(cid: str, user: User = CurrentUser, db: Session = DB) -> dict:
    db.delete(owned(db, QualCode, cid, user))
    db.commit()
    return {"ok": True}


@router.post("/qual/codings")
def apply_code(body: CodingIn, user: User = CurrentUser, db: Session = DB) -> dict:
    s = owned(db, QualSegment, body.segment_id, user)
    c = owned(db, QualCode, body.code_id, user)
    ex = db.scalar(select(QualCoding).where(QualCoding.segment_id == s.id, QualCoding.code_id == c.id))
    if ex:
        ex.status = "approved"
    else:
        db.add(QualCoding(segment_id=s.id, code_id=c.id))
    db.commit()
    return {"ok": True}


@router.delete("/qual/codings/{coding_id}")
def remove_coding(coding_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    cd = db.get(QualCoding, parse_uuid(coding_id))
    c = db.get(QualCode, cd.code_id) if cd else None
    if not cd or not c or c.owner_id != user.id:
        raise HTTPException(404, "Coding not found")
    db.delete(cd)
    db.commit()
    return {"ok": True}


@router.post("/qual/suggest")
def suggest_codes(project_id: str, focus: str = "", user: User = CurrentUser, db: Session = DB) -> dict:
    p = owned_project(db, project_id, user)
    job = enqueue(db, user.id, "qual_code_suggestions", {"focus": focus}, title="Suggest qualitative codes", project_id=p.id)
    db.commit()
    return {"job_id": str(job.id)}


@router.get("/qual/summary")
def qual_summary(project_id: str, user: User = CurrentUser, db: Session = DB) -> dict:
    """Code frequencies, co-occurrence, comparison across sources and themes (approved codings only)."""
    p = owned_project(db, project_id, user)
    rows = db.execute(select(QualCoding, QualCode, QualSegment).join(QualCode, QualCode.id == QualCoding.code_id)
                      .join(QualSegment, QualSegment.id == QualCoding.segment_id)
                      .where(QualCode.project_id == p.id, QualCoding.status == "approved", QualCode.status == "approved")).all()
    freq: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    seg_codes: dict = defaultdict(set)
    themes: dict[str, set] = defaultdict(set)
    examples: dict[str, list] = defaultdict(list)
    for cd, code, seg in rows:
        freq[code.name] += 1
        by_source[seg.source_label or "unlabelled"][code.name] += 1
        seg_codes[seg.id].add(code.name)
        themes[code.theme or "(no theme)"].add(code.name)
        if len(examples[code.name]) < 3:
            examples[code.name].append(seg.text[:300])
    co: Counter = Counter()
    for codes in seg_codes.values():
        cs = sorted(codes)
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                co[(cs[i], cs[j])] += 1
    n_segs = db.scalar(select(func.count(QualSegment.id)).where(QualSegment.project_id == p.id))
    return {"segments": n_segs, "coded_segments": len(seg_codes), "code_frequencies": dict(freq.most_common()),
            "co_occurrence": [{"a": a, "b": b, "n": n} for (a, b), n in co.most_common(50)],
            "by_source": {k: dict(v) for k, v in by_source.items()},
            "themes": {k: sorted(v) for k, v in themes.items()}, "examples": examples,
            "note": "Counts summarise coding decisions; they are not a measure of importance on their own."}

