"use client";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Download, RefreshCw, Trash2, Upload } from "lucide-react";
import { api, download, useApi, type Doc } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, ErrorNote, Field, Input, PageHeader, Select, Tabs, Textarea } from "@/components/ui";
import { CategoryBadge, CategorySelect, JobProgress } from "@/components/research";
import { fmtDateTime } from "@/lib/utils";

type Tab = "metadata" | "content" | "versions" | "notes" | "references";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: d, mutate } = useApi<Doc & any>(`/knowledge/documents/${id}`, { refreshInterval: (x) => (x && (x.status === "pending" || x.status === "processing") ? 2000 : 0) });
  const [tab, setTab] = useState<Tab>("metadata");
  const [job, setJob] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  if (!d) return null;
  const act = async (fn: () => Promise<any>) => {
    setErr(null);
    try {
      const r = await fn();
      if (r?.job_id) setJob(r.job_id);
      mutate();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <>
      <PageHeader title={d.title || "(untitled document)"}
        description={<span className="flex flex-wrap items-center gap-1.5"><CategoryBadge category={d.knowledge_category} /><Badge>{d.doc_type}</Badge><Badge>{d.visibility.replaceAll("_", " ")}</Badge>
          <Badge tone={d.status === "ready" ? "ok" : d.status === "failed" ? "danger" : "warn"}>{d.status}</Badge>{d.metadata_verified && <Badge tone="ok">metadata verified (Crossref)</Badge>}</span>}
        actions={<>
          <Button size="sm" variant="outline" onClick={() => act(() => api(`/knowledge/documents/${id}/reindex`, { method: "POST" }))}><RefreshCw size={13} /> Re-index</Button>
          <Button size="sm" variant="outline" onClick={() => act(() => api(`/knowledge/documents/${id}/reindex?embeddings_only=true`, { method: "POST" }))}>Regenerate embeddings</Button>
          <Button size="sm" variant="outline" onClick={() => act(() => api(`/literature/documents/${id}/evidence`, { method: "POST" }))}>Extract evidence</Button>
          {d.deleted_at ? <Button size="sm" onClick={() => act(() => api(`/knowledge/documents/${id}/restore`, { method: "POST" }))}>Restore</Button> :
            <Button size="sm" variant="danger" onClick={() => act(async () => { await api(`/knowledge/documents/${id}`, { method: "DELETE" }); router.push("/knowledge"); })}><Trash2 size={13} /> Remove</Button>}
        </>} />
      {d.status_detail && <div className={`mb-3 rounded-md px-3 py-2 text-sm ${d.status === "failed" ? "bg-danger-soft text-danger" : "bg-surface-2 text-muted"}`}>{d.status_detail}</div>}
      {d.extra?.possible_duplicates?.length > 0 && <div className="mb-3 rounded-md bg-warn-soft px-3 py-2 text-sm text-warn">Possible duplicate: {d.extra.possible_duplicates.map((x: any) => <a key={x.document_id} className="ml-1 underline" href={`/knowledge/${x.document_id}`}>{x.reason}</a>)}</div>}
      <ErrorNote error={err} />
      <JobProgress jobId={job} onDone={() => mutate()} compact />
      <div className="mt-3"><Tabs value={tab} onChange={setTab} tabs={[{ value: "metadata", label: "Metadata" }, { value: "content", label: `Indexed content (${d.chunk_count ?? 0})` },
        { value: "versions", label: `Versions (${d.versions?.length ?? 0})` }, { value: "notes", label: `Notes (${d.notes?.length ?? 0})` }, { value: "references", label: `Extracted references (${d.extracted_references?.length ?? 0})` }]} /></div>
      <div className="mt-4">
        {tab === "metadata" && <Metadata d={d} onSaved={mutate} onVerify={() => act(() => api(`/knowledge/documents/${id}/verify-metadata`, { method: "POST" }))} />}
        {tab === "content" && <Chunks id={id} />}
        {tab === "versions" && <Versions d={d} onJob={(j) => { setJob(j); mutate(); }} />}
        {tab === "notes" && <DocNotes d={d} onSaved={mutate} />}
        {tab === "references" && (
          <Card><CardBody>{d.extracted_references?.length ? <ol className="list-decimal space-y-1.5 pl-5 text-sm">{d.extracted_references.map((r: string, i: number) => <li key={i}>{r}</li>)}</ol>
            : <p className="text-sm text-muted">No reference section detected.</p>}
            <p className="mt-3 text-xs text-muted">These are extracted strings, not verified records. Add them to a project as references and run verification before citing.</p></CardBody></Card>
        )}
      </div>
    </>
  );
}

function Metadata({ d, onSaved, onVerify }: { d: any; onSaved: () => void; onVerify: () => void }) {
  const [f, setF] = useState({ title: d.title, authors: (d.authors ?? []).join("; "), year: d.year ?? "", doi: d.doi ?? "", venue: d.venue, abstract: d.abstract,
    doc_type: d.doc_type, visibility: d.visibility, knowledge_category: d.knowledge_category, is_own_work: d.is_own_work });
  const [err, setErr] = useState<unknown>(null);
  const src = (k: string) => d.metadata_source?.[k] ? <span className="ml-1 font-normal text-[10px] uppercase tracking-wide text-muted">source: {d.metadata_source[k]}</span> : null;
  const save = async () => {
    setErr(null);
    try {
      await api(`/knowledge/documents/${d.id}`, { method: "PATCH", body: { ...f, authors: f.authors.split(";").map((a: string) => a.trim()).filter(Boolean), year: f.year ? Number(f.year) : null, doi: f.doi || null } });
      onSaved();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <Card>
      <CardHeader title="Bibliographic metadata" description="Corrections are recorded in the audit log and never overwritten by re-processing. Only the DOI is sent to Crossref for verification."
        actions={<Button size="sm" variant="outline" onClick={onVerify} disabled={!d.doi}>Verify via DOI</Button>} />
      <CardBody className="grid gap-3 md:grid-cols-2">
        <Field label="Title" className="md:col-span-2"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />{src("title")}</Field>
        <Field label="Authors (separate with ;)"><Input value={f.authors} onChange={(e) => setF({ ...f, authors: e.target.value })} />{src("authors")}</Field>
        <div className="grid grid-cols-2 gap-2"><Field label="Year"><Input value={f.year} onChange={(e) => setF({ ...f, year: e.target.value })} />{src("year")}</Field>
          <Field label="DOI"><Input value={f.doi} onChange={(e) => setF({ ...f, doi: e.target.value })} />{src("doi")}</Field></div>
        <Field label="Journal / venue"><Input value={f.venue} onChange={(e) => setF({ ...f, venue: e.target.value })} /></Field>
        <Field label="Document type"><Input value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })} /></Field>
        <Field label="Visibility"><Select value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value })}><option value="private_unpublished">Private / unpublished</option><option value="published">Published</option><option value="public_source">Public source</option></Select></Field>
        <Field label="Knowledge category"><CategorySelect value={f.knowledge_category} onChange={(v) => setF({ ...f, knowledge_category: v })} exclude={["ai_interpretation"]} /></Field>
        <Field label="Abstract" className="md:col-span-2"><Textarea value={f.abstract} onChange={(e) => setF({ ...f, abstract: e.target.value })} className="min-h-[120px]" />{src("abstract")}</Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_own_work} onChange={(e) => setF({ ...f, is_own_work: e.target.checked })} /> My own work</label>
        <div className="md:col-span-2 flex items-center justify-between"><ErrorNote error={err} /><Button variant="primary" onClick={save}>Save corrections</Button></div>
        {d.extra?.keywords && <div className="md:col-span-2 text-xs text-muted">Extracted keywords: {d.extra.keywords.join(", ")}</div>}
        {d.embedding_models?.length > 0 && <div className="md:col-span-2 text-xs text-muted">Embedding model(s): {d.embedding_models.join(", ")}</div>}
      </CardBody>
    </Card>
  );
}

function Chunks({ id }: { id: string }) {
  const { data } = useApi<any[]>(`/knowledge/documents/${id}/chunks`);
  return (
    <div className="space-y-2">{data?.map((c) => (
      <Card key={c.id}><CardBody>
        <div className="mb-1 flex gap-1.5 text-xs text-muted"><Badge>#{c.index}</Badge>{c.page_start && <Badge>p. {c.page_start}{c.page_end !== c.page_start ? `–${c.page_end}` : ""}</Badge>}<span className="truncate">{c.section}</span></div>
        <p className="prose-academic whitespace-pre-wrap text-[14px]">{c.text}</p>
      </CardBody></Card>))}
    </div>
  );
}

function Versions({ d, onJob }: { d: any; onJob: (j: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const replace = async () => {
    if (!file) return;
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("change_note", note);
      onJob((await api(`/knowledge/documents/${d.id}/replace`, { form: fd })).job_id);
      setFile(null);
      setNote("");
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="space-y-4">
      <Card><CardHeader title="Upload a new version" description="The previous file is preserved. Notes and references remain attached; only changed passages are re-embedded." />
        <CardBody className="flex flex-wrap items-center gap-2">
          <input type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
          <Input className="w-72" placeholder="What changed?" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="primary" disabled={!file} onClick={replace}><Upload size={13} /> Replace</Button>
          <ErrorNote error={err} />
        </CardBody></Card>
      <Card><CardBody className="p-0"><table className="w-full text-sm">
        <thead><tr className="border-b border-border text-left text-xs text-muted"><th className="px-4 py-2">Version</th><th>File</th><th>Pages</th><th>Note</th><th>Status</th><th>Uploaded</th><th /></tr></thead>
        <tbody>{[...(d.versions ?? [])].reverse().map((v: any) => (
          <tr key={v.id} className="border-b border-border last:border-0">
            <td className="px-4 py-2">v{v.version_no}{v.id === d.current_version_id && <Badge tone="ok" className="ml-1.5">current</Badge>}</td>
            <td className="max-w-[220px] truncate">{v.filename || "registry abstract"}</td><td>{v.page_count ?? "—"}</td><td className="text-xs">{v.change_note}</td>
            <td><Badge>{v.processing_status}</Badge></td><td className="text-xs text-muted">{fmtDateTime(v.created_at)}</td>
            <td className="pr-3">{v.filename && <button onClick={() => download(`/knowledge/documents/${d.id}/versions/${v.id}/file`)} className="text-muted hover:text-text"><Download size={14} /></button>}</td>
          </tr>))}</tbody></table></CardBody></Card>
    </div>
  );
}

function DocNotes({ d, onSaved }: { d: any; onSaved: () => void }) {
  const [body, setBody] = useState("");
  const [page, setPage] = useState("");
  const [cat, setCat] = useState("provisional_opinion");
  return (
    <div className="space-y-3">
      <Card><CardBody className="space-y-2">
        <Textarea placeholder="Annotation on this document" value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="flex flex-wrap gap-2"><Input className="w-28" placeholder="Page" value={page} onChange={(e) => setPage(e.target.value)} />
          <div className="min-w-[280px] flex-1"><CategorySelect value={cat} onChange={setCat} exclude={["ai_interpretation"]} /></div>
          <Button variant="primary" disabled={!body} onClick={async () => { await api("/notes", { body: { body, document_id: d.id, note_type: "annotation", knowledge_category: cat, anchor: page ? { page: Number(page) } : {} } }); setBody(""); onSaved(); }}>Add annotation</Button></div>
      </CardBody></Card>
      {d.notes?.map((n: any) => <Card key={n.id}><CardBody><div className="flex gap-1.5"><CategoryBadge category={n.knowledge_category} />{n.anchor?.page && <Badge>p. {n.anchor.page}</Badge>}</div><p className="mt-1 whitespace-pre-wrap text-sm">{n.body}</p></CardBody></Card>)}
    </div>
  );
}
