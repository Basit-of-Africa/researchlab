"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { FilePlus2, Link2, Search, Upload } from "lucide-react";
import { api, refresh, useApi, type Doc } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { AIWarning, CategoryBadge, CategorySelect, JobProgress, Prose, SourceList } from "@/components/research";
import { authorsShort, fmtDate } from "@/lib/utils";

type Tab = "library" | "search" | "ask" | "development";

function KnowledgeInner() {
  const sp = useSearchParams();
  const [tab, setTab] = useState<Tab>("library");
  const [uploadOpen, setUploadOpen] = useState(false);
  useEffect(() => { if (sp.get("upload") === "1") setUploadOpen(true); }, [sp]);
  const { data: stats } = useApi<any>("/knowledge/stats");
  return (
    <>
      <PageHeader title="Knowledge Centre"
        description="Your continuously expanding knowledge base: personal academic materials, scholarly literature, notes, theory and methods, and research history."
        actions={<Button variant="primary" onClick={() => setUploadOpen(true)}><Upload size={14} /> Add material</Button>} />
      {stats && (
        <div className="mb-4 flex flex-wrap gap-2 text-xs">
          <Badge>{stats.documents} documents</Badge><Badge>{stats.chunks} indexed passages</Badge><Badge>{stats.publications} publication records</Badge>
          <Badge>{stats.notes} notes</Badge><Badge tone="accent">Embeddings: {stats.embedding_model}</Badge>
          {Object.entries(stats.by_status ?? {}).filter(([k]) => k !== "ready").map(([k, v]) => <Badge key={k} tone={k === "failed" ? "danger" : "warn"}>{String(v)} {k}</Badge>)}
        </div>
      )}
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "library", label: "Library" }, { value: "search", label: "Search" }, { value: "ask", label: "Ask your knowledge base" }, { value: "development", label: "Knowledge development" }]} />
      <div className="mt-4">
        {tab === "library" && <Library />}
        {tab === "search" && <SearchPanel />}
        {tab === "ask" && <AskPanel mode="knowledge" />}
        {tab === "development" && <AskPanel mode="development" />}
      </div>
      <AddMaterial open={uploadOpen} onClose={() => setUploadOpen(false)} />
    </>
  );
}

function Library() {
  const { data: colls, mutate: mColls } = useApi<any[]>("/knowledge/collections");
  const [coll, setColl] = useState<string>("");
  const [q, setQ] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const { data } = useApi<{ total: number; items: Doc[] }>(`/knowledge/documents?limit=200${coll ? `&collection_id=${coll}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}${showDeleted ? "&include_deleted=true" : ""}`,
    { refreshInterval: (d) => (d?.items.some((x: Doc) => x.status === "pending" || x.status === "processing") ? 2500 : 0) });
  const [newColl, setNewColl] = useState("");
  return (
    <div className="grid gap-4 lg:grid-cols-4">
      <Card className="h-fit">
        <CardHeader title="Collections" />
        <CardBody className="space-y-1 p-2">
          <button className={`w-full rounded px-2 py-1.5 text-left text-sm ${!coll ? "bg-accent-soft text-accent" : "hover:bg-surface-2"}`} onClick={() => setColl("")}>All documents</button>
          {colls?.map((c) => (
            <button key={c.id} className={`flex w-full justify-between rounded px-2 py-1.5 text-left text-sm ${coll === c.id ? "bg-accent-soft text-accent" : "hover:bg-surface-2"}`} onClick={() => setColl(c.id)}>
              <span className="truncate">{c.name}</span><span className="text-xs text-muted">{c.document_count + c.publication_count}</span>
            </button>
          ))}
          <div className="flex gap-1 pt-2">
            <Input className="h-8 text-xs" placeholder="New collection" value={newColl} onChange={(e) => setNewColl(e.target.value)} />
            <Button size="sm" onClick={async () => { await api("/knowledge/collections", { body: { name: newColl } }); setNewColl(""); mColls(); }} disabled={!newColl}>Add</Button>
          </div>
        </CardBody>
      </Card>
      <div className="space-y-3 lg:col-span-3">
        <div className="flex gap-2">
          <Input placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="flex items-center gap-1.5 whitespace-nowrap text-xs text-muted"><input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} /> Show removed</label>
        </div>
        {data?.items.length === 0 && <Empty title="No documents">Upload PDFs, DOCX, text or Markdown; import by DOI or URL.</Empty>}
        <div className="divide-y divide-border rounded-lg border border-border bg-surface">
          {data?.items.map((d) => (
            <Link key={d.id} href={`/knowledge/${d.id}`} className="block px-4 py-3 hover:bg-surface-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium leading-snug">{d.title || "(untitled)"}</div>
                  <div className="mt-0.5 text-xs text-muted">{authorsShort(d.authors)} · {d.year ?? "n.d."}{d.venue ? ` · ${d.venue}` : ""}</div>
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  {d.status !== "ready" && <Badge tone={d.status === "failed" ? "danger" : "warn"}>{d.status === "processing" || d.status === "pending" ? <Spinner className="h-2.5 w-2.5" /> : null} {d.status}</Badge>}
                  <CategoryBadge category={d.knowledge_category} />
                  {d.visibility === "private_unpublished" && <Badge tone="warn">private</Badge>}
                  {d.source_kind === "doi" && <Badge>metadata + abstract only</Badge>}
                  {d.metadata_verified && <Badge tone="ok">metadata verified</Badge>}
                  {d.duplicate_of_id && <Badge tone="danger">possible duplicate</Badge>}
                  {(d as any).deleted_at && <Badge tone="danger">removed</Badge>}
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

function AddMaterial({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<"file" | "doi" | "url" | "note">("file");
  const { data: projects } = useApi<any[]>("/projects");
  const [f, setF] = useState({ title: "", doc_type: "article", visibility: "private_unpublished", knowledge_category: "external_evidence", is_own_work: false, project_id: "", doi: "", url: "", note: "" });
  const [files, setFiles] = useState<File[]>([]);
  const [jobs, setJobs] = useState<string[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      if (mode === "file") {
        const ids: string[] = [];
        for (const file of files) {
          const fd = new FormData();
          fd.append("file", file);
          Object.entries({ ...f, title: files.length === 1 ? f.title : "" }).forEach(([k, v]) => ["doi", "url", "note"].includes(k) || fd.append(k, String(v)));
          ids.push((await api("/knowledge/documents/upload", { form: fd })).job_id);
        }
        setJobs(ids);
        setFiles([]);
      } else if (mode === "doi") {
        await api("/knowledge/documents/doi", { body: { doi: f.doi, project_id: f.project_id || null } });
        onClose();
      } else if (mode === "url") {
        setJobs([(await api("/knowledge/documents/url", { body: { url: f.url, project_id: f.project_id || null } })).job_id]);
      } else {
        await api("/notes", { body: { body: f.note, title: f.title, knowledge_category: f.knowledge_category === "external_evidence" ? "provisional_opinion" : f.knowledge_category, project_id: f.project_id || null } });
        onClose();
      }
      refresh("/knowledge");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="Add to the knowledge base" wide>
      <Tabs value={mode} onChange={setMode} tabs={[{ value: "file", label: <span className="flex items-center gap-1"><Upload size={13} /> Upload files</span> },
        { value: "doi", label: "Import by DOI" }, { value: "url", label: <span className="flex items-center gap-1"><Link2 size={13} /> From URL</span> }, { value: "note", label: <span className="flex items-center gap-1"><FilePlus2 size={13} /> Research note</span> }]} />
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {mode === "file" && (
          <>
            <div className="md:col-span-2">
              <input type="file" multiple accept=".pdf,.docx,.txt,.md,.markdown" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} className="text-sm" />
              <p className="mt-1 text-xs text-muted">PDF, DOCX, TXT or Markdown. Originals are preserved; text is extracted, chunked, embedded and indexed in the background.</p>
            </div>
            {files.length === 1 && <Field label="Title" hint="(optional — otherwise extracted)"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>}
            <Field label="Document type"><Select value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })}>
              {["article", "manuscript", "thesis", "proposal", "conference_paper", "report", "book_chapter", "working_paper", "government_report", "writing_sample", "theory", "method", "instrument"].map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
            </Select></Field>
            <Field label="Visibility"><Select value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value })}>
              <option value="private_unpublished">Private / unpublished — never sent to external AI without your permission</option>
              <option value="published">Published work</option><option value="public_source">Public source</option></Select></Field>
            <Field label="Knowledge category"><CategorySelect value={f.knowledge_category} onChange={(v) => setF({ ...f, knowledge_category: v })} exclude={["ai_interpretation"]} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_own_work} onChange={(e) => setF({ ...f, is_own_work: e.target.checked, knowledge_category: e.target.checked ? "researcher_writing" : f.knowledge_category })} /> This is my own work (usable as a writing sample)</label>
          </>
        )}
        {mode === "doi" && <Field label="DOI" className="md:col-span-2"><Input value={f.doi} onChange={(e) => setF({ ...f, doi: e.target.value })} placeholder="10.xxxx/xxxxx" /><p className="mt-1 text-xs text-muted">Creates a registry-verified metadata record (and indexes the abstract if available). Upload the PDF separately if you have legitimate access.</p></Field>}
        {mode === "url" && <Field label="Publication or repository URL" className="md:col-span-2"><Input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://…" /><p className="mt-1 text-xs text-muted">Respect copyright and repository licences: import only material you may legitimately store.</p></Field>}
        {mode === "note" && <>
          <Field label="Title" className="md:col-span-2"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Note" className="md:col-span-2"><Textarea value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} className="min-h-[140px]" /></Field>
          <Field label="Knowledge category"><CategorySelect value={f.knowledge_category === "external_evidence" ? "provisional_opinion" : f.knowledge_category} onChange={(v) => setF({ ...f, knowledge_category: v })} exclude={["ai_interpretation"]} /></Field>
        </>}
        <Field label="Project" hint="(optional)"><Select value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">None</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</Select></Field>
      </div>
      <div className="mt-4 space-y-2">
        <ErrorNote error={err} />
        {jobs.map((j) => <JobProgress key={j} jobId={j} compact onDone={() => refresh("/knowledge")} />)}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Close</Button><Button variant="primary" onClick={submit} disabled={busy || (mode === "file" && !files.length)}>{busy ? <Spinner /> : null} Add</Button></div>
      </div>
    </Modal>
  );
}

function SearchPanel() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [yearFrom, setYearFrom] = useState("");
  const [cat, setCat] = useState("");
  const [own, setOwn] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr(null);
    try {
      setRes(await api("/knowledge/search", { body: { query: q, mode, k: 20, year_from: yearFrom ? Number(yearFrom) : null, knowledge_categories: cat ? [cat] : [], own_work_only: own } }));
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="space-y-3">
      <form onSubmit={run} className="flex flex-wrap gap-2">
        <Input className="min-w-[260px] flex-1" placeholder="Search full text, semantics and metadata…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select value={mode} onChange={(e) => setMode(e.target.value)} className="w-36"><option value="hybrid">Hybrid</option><option value="fulltext">Full text</option><option value="semantic">Semantic</option></Select>
        <Input className="w-28" placeholder="From year" value={yearFrom} onChange={(e) => setYearFrom(e.target.value)} />
        <Select value={cat} onChange={(e) => setCat(e.target.value)} className="w-48"><option value="">All categories</option><option value="researcher_writing">Researcher writing</option><option value="external_evidence">External evidence</option></Select>
        <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={own} onChange={(e) => setOwn(e.target.checked)} /> My work only</label>
        <Button variant="primary" disabled={!q}><Search size={14} /> Search</Button>
      </form>
      <ErrorNote error={err} />
      {res && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-2 lg:col-span-2">
            {res.results.length === 0 && <Empty title="No matching passages" />}
            {res.results.map((h: any) => (
              <Card key={h.chunk_id}><CardBody>
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <Link href={`/knowledge/${h.document_id}`} className="font-medium text-text hover:underline">{h.title}</Link>
                  <span className="text-muted">{h.year ?? "n.d."}</span><CategoryBadge category={h.knowledge_category} /><Badge>{h.basis.replace("_", " ")}</Badge>
                  {h.page_start && <Badge>p. {h.page_start}{h.page_end !== h.page_start ? `–${h.page_end}` : ""}</Badge>}
                </div>
                {h.section && <div className="mt-1 text-xs text-muted">§ {h.section}</div>}
                <p className="prose-academic mt-1.5 text-[14px] leading-relaxed">{h.text.slice(0, 700)}{h.text.length > 700 ? "…" : ""}</p>
                <div className="mt-1 text-[11px] text-muted tabular-nums">score {h.scores.score.toFixed(4)}{h.scores.fts_rank ? ` · text rank ${h.scores.fts_rank}` : ""}{h.scores.vec_rank ? ` · vector rank ${h.scores.vec_rank} (sim ${h.scores.similarity})` : ""}</div>
              </CardBody></Card>
            ))}
          </div>
          <Card className="h-fit"><CardHeader title="Matching notes" /><CardBody className="space-y-2">
            {res.notes.length ? res.notes.map((n: any) => <div key={n.note_id} className="text-sm"><div className="font-medium">{n.title || "Note"}</div><CategoryBadge category={n.knowledge_category} /><p className="mt-1 text-xs text-muted line-clamp-3">{n.body}</p></div>) : <p className="text-sm text-muted">None.</p>}
          </CardBody></Card>
        </div>
      )}
    </div>
  );
}

function AskPanel({ mode }: { mode: "knowledge" | "development" }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const examples = mode === "knowledge"
    ? ["What do I currently know about digital trust infrastructure?", "Which publications in my library discuss institutional voids?", "What theoretical frameworks have I previously used?", "Which authors appear frequently in my research collection?"]
    : ["How has my understanding of institutional capacity evolved?", "Which positions have I revised?", "What new evidence challenges my previous conclusions?", "What emerging research opportunities exist within my knowledge base?"];
  const ask = async (question = q) => {
    setQ(question);
    setBusy(true);
    setErr(null);
    try {
      setRes(await api("/knowledge/ask", { body: { question, mode } }));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-3 lg:col-span-2">
        <div className="flex gap-2"><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question about your knowledge base" onKeyDown={(e) => e.key === "Enter" && q && ask()} />
          <Button variant="primary" onClick={() => ask()} disabled={!q || busy}>{busy ? <Spinner /> : "Ask"}</Button></div>
        <div className="flex flex-wrap gap-1.5">{examples.map((e) => <button key={e} onClick={() => ask(e)} className="rounded-full border border-border px-2.5 py-1 text-xs text-muted hover:text-text">{e}</button>)}</div>
        <ErrorNote error={err} />
        {res && (
          <Card><CardBody className="space-y-3">
            <AIWarning />
            {res.excluded_private_sources > 0 && <div className="text-xs text-warn">{res.excluded_private_sources} private source(s) were withheld from the external AI provider under your data policy (Settings).</div>}
            <Prose text={res.answer} />
            <SourceList sources={res.sources} />
            <div className="text-[11px] text-muted">{res.provider}:{res.model}</div>
          </CardBody></Card>
        )}
      </div>
      <Card className="h-fit"><CardBody className="text-sm text-muted">
        {mode === "knowledge" ? "Answers combine full-text, semantic and metadata retrieval over your documents and notes, and every claim is tied to a numbered source." :
          "Knowledge development traces how your positions evolved, using your dated notes and decision log, and labels external evidence and AI interpretation separately."}
      </CardBody></Card>
    </div>
  );
}

export default function KnowledgePage() {
  return <Suspense><KnowledgeInner /></Suspense>;
}
