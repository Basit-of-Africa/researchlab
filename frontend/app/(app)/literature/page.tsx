"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { api, useApi, type Publication } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Tabs } from "@/components/ui";
import { AIWarning, JobProgress, VerificationBadge } from "@/components/research";
import { authorsShort, fmtDateTime } from "@/lib/utils";

type Tab = "search" | "library" | "queue" | "watches";

function LiteratureInner() {
  const sp = useSearchParams();
  const [tab, setTab] = useState<Tab>("search");
  const { data: q } = useApi<any>("/literature/queue?status=pending&limit=1");
  return (
    <>
      <PageHeader title="Literature discovery" description="Search OpenAlex, Crossref, Semantic Scholar and CORE. Discovery is not endorsement: verify, then decide what enters your evidence base." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "search", label: "Search" }, { value: "library", label: "Publication library" },
        { value: "queue", label: `Review queue${q?.counts?.pending ? ` (${q.counts.pending})` : ""}` }, { value: "watches", label: "Scholarly updates" }]} />
      <div className="mt-4">
        {tab === "search" && <SearchTab projectId={sp.get("project")} />}
        {tab === "library" && <LibraryTab projectId={sp.get("project")} />}
        {tab === "queue" && <QueueTab />}
        {tab === "watches" && <WatchesTab />}
      </div>
    </>
  );
}

function SearchTab({ projectId }: { projectId: string | null }) {
  const [f, setF] = useState({ query: "", sources: ["openalex"], year_from: "", year_to: "", pub_type: "", open_access: "", min_citations: "", sort: "" });
  const [page, setPage] = useState(1);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const run = async (p = 1) => {
    setBusy(true);
    setErr(null);
    setPage(p);
    try {
      setRes(await api("/literature/search", { body: {
        query: f.query, sources: f.sources, page: p, per_page: 25, year_from: f.year_from ? Number(f.year_from) : null, year_to: f.year_to ? Number(f.year_to) : null,
        pub_type: f.pub_type || null, open_access: f.open_access === "" ? null : f.open_access === "true", min_citations: f.min_citations ? Number(f.min_citations) : null, sort: f.sort || null,
      } }));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (s: string) => setF({ ...f, sources: f.sources.includes(s) ? f.sources.filter((x) => x !== s) : [...f.sources, s] });
  return (
    <div className="space-y-3">
      <Card><CardBody className="space-y-3">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); run(1); }}>
          <Input placeholder="Topic, keywords or title" value={f.query} onChange={(e) => setF({ ...f, query: e.target.value })} />
          <Button variant="primary" disabled={busy || !f.query}>{busy ? <Spinner /> : <Search size={14} />} Search</Button>
        </form>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {[["openalex", "OpenAlex"], ["crossref", "Crossref"], ["semantic_scholar", "Semantic Scholar"], ["core", "CORE (needs key)"], ["library", "My library"]].map(([k, l]) => (
            <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={f.sources.includes(k)} onChange={() => toggle(k)} /> {l}</label>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
          <Input placeholder="From year" value={f.year_from} onChange={(e) => setF({ ...f, year_from: e.target.value })} />
          <Input placeholder="To year" value={f.year_to} onChange={(e) => setF({ ...f, year_to: e.target.value })} />
          <Select value={f.pub_type} onChange={(e) => setF({ ...f, pub_type: e.target.value })}><option value="">Any type</option>
            {["article", "review", "book", "book-chapter", "dissertation", "preprint", "report"].map((t) => <option key={t}>{t}</option>)}</Select>
          <Select value={f.open_access} onChange={(e) => setF({ ...f, open_access: e.target.value })}><option value="">Any access</option><option value="true">Open access only</option><option value="false">Closed only</option></Select>
          <Input placeholder="Min. citations" value={f.min_citations} onChange={(e) => setF({ ...f, min_citations: e.target.value })} />
          <Select value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}><option value="">Relevance</option><option value="cited_by_count:desc">Most cited</option><option value="publication_date:desc">Newest</option></Select>
        </div>
        <p className="text-[11px] text-muted">Filters apply to OpenAlex; Crossref honours the year range. Results are saved as metadata records only — not added to any collection or project until you choose.</p>
      </CardBody></Card>
      <ErrorNote error={err} />
      {res && Object.entries(res.errors ?? {}).map(([k, v]) => <div key={k} className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn"><b>{k}</b>: {String(v)}</div>)}
      {res && <div className="text-xs text-muted">{res.totals?.openalex ? `${res.totals.openalex.toLocaleString()} OpenAlex matches · ` : ""}showing {res.results.length}</div>}
      {res?.results.map((p: any) => <PubCard key={(p.id ?? p.doi ?? p.title) + p.source_db_hit} p={p} projectId={projectId} />)}
      {res?.results.length > 0 && f.sources.includes("openalex") && (
        <div className="flex justify-center gap-2"><Button size="sm" disabled={page === 1} onClick={() => run(page - 1)}>Previous</Button><Button size="sm" onClick={() => run(page + 1)}>Next page</Button></div>
      )}
    </div>
  );
}

function PubCard({ p, projectId, onChange }: { p: Publication & any; projectId?: string | null; onChange?: () => void }) {
  const [open, setOpen] = useState(false);
  const [related, setRelated] = useState<any>(null);
  const [msg, setMsg] = useState<string>("");
  const [job, setJob] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const { data: projects } = useApi<any[]>(addOpen ? "/projects" : null);
  const { data: colls } = useApi<any[]>(addOpen ? "/knowledge/collections" : null);
  const [proj, setProj] = useState(projectId ?? "");
  const [coll, setColl] = useState("");
  const [vp, setVp] = useState<Publication & any>(p);
  const act = async (fn: () => Promise<any>, ok: string) => {
    try { await fn(); setMsg(ok); onChange?.(); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <Card><CardBody>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium leading-snug">{vp.title}</div>
          <div className="mt-0.5 text-xs text-muted">{authorsShort(vp.authors)} · {vp.year ?? "n.d."}{vp.venue ? ` · ${vp.venue}` : ""}</div>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          <VerificationBadge status={vp.verification_status} />
          {vp.is_oa ? <Badge tone="ok">open access</Badge> : vp.is_oa === false ? <Badge>closed</Badge> : null}
          <Badge tone={vp.full_text_available ? "ok" : "neutral"}>{vp.full_text_available ? "full text available" : "no full text"}</Badge>
          <Badge>{vp.abstract ? `abstract (${vp.abstract_source || vp.source_db})` : "no abstract"}</Badge>
          {vp.cited_by_count != null && <Badge>cited by {vp.cited_by_count}</Badge>}
          <Badge tone="accent">{p.source_db_hit ?? vp.source_db}</Badge>
        </div>
      </div>
      {vp.abstract && <p className={`mt-2 text-sm text-muted ${open ? "" : "line-clamp-2"}`} onClick={() => setOpen(!open)}>{vp.abstract}</p>}
      {vp.id && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {vp.doi && <a href={`https://doi.org/${vp.doi}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">DOI {vp.doi} <ExternalLink size={11} /></a>}
          {vp.oa_url && <a href={vp.oa_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">OA copy</a>}
          <span className="flex-1" />
          <Button size="sm" variant="outline" onClick={() => act(async () => { const r = await api(`/literature/publications/${vp.id}/verify`, { method: "POST" }); setVp(r.publication); }, "Verification complete")}>Verify</Button>
          <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>Add to…</Button>
          <Button size="sm" variant="ghost" onClick={async () => setRelated(await api(`/literature/publications/${vp.id}/related?relation=cites`))}>Cited by</Button>
          <Button size="sm" variant="ghost" onClick={async () => setRelated(await api(`/literature/publications/${vp.id}/related?relation=references`))}>References</Button>
          <Button size="sm" variant="ghost" disabled={!vp.abstract && !vp.full_text_available} onClick={async () => setJob((await api(`/literature/publications/${vp.id}/evidence`, { method: "POST" })).job_id)}>Extract evidence</Button>
        </div>
      )}
      {msg && <p className="mt-1 text-xs text-muted">{msg}</p>}
      <JobProgress jobId={job} compact onDone={() => setMsg("Evidence extracted — open the publication in your library to review it.")} />
      {related && <div className="mt-3 space-y-1 border-t border-border pt-2">
        <div className="text-xs font-medium text-muted">{related.relation === "cites" ? "Works citing this" : "Works it references"} ({related.results.length})</div>
        {related.results.slice(0, 15).map((r: any) => <div key={r.id} className="text-xs">• {r.title} <span className="text-muted">({r.year ?? "n.d."})</span></div>)}
      </div>}
      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="Add publication">
        <div className="space-y-3">
          <Field label="Add to a project's reference list (citable by the Academic Writer)">
            <div className="flex gap-2"><Select value={proj} onChange={(e) => setProj(e.target.value)}><option value="">Choose project</option>{projects?.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</Select>
              <Button variant="primary" disabled={!proj} onClick={() => act(() => api("/literature/references", { body: { project_id: proj, publication_id: vp.id } }), "Added to project references")}>Add</Button></div>
          </Field>
          <Field label="Save to a knowledge collection">
            <div className="flex gap-2"><Select value={coll} onChange={(e) => setColl(e.target.value)}><option value="">Choose collection</option>{colls?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select>
              <Button disabled={!coll} onClick={() => act(() => api(`/literature/publications/${vp.id}/collections/${coll}`, { method: "POST" }), "Saved to collection")}>Save</Button></div>
          </Field>
          {vp.doi && <Button variant="outline" onClick={() => act(() => api("/knowledge/documents/doi", { body: { doi: vp.doi, project_id: proj || null } }), "Imported into the knowledge base (metadata + abstract)")}>Import into knowledge base by DOI</Button>}
          {msg && <p className="text-xs text-muted">{msg}</p>}
        </div>
      </Modal>
    </CardBody></Card>
  );
}

function LibraryTab({ projectId }: { projectId: string | null }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const { data, mutate } = useApi<any>(`/literature/publications?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}${status ? `&verification_status=${status}` : ""}`);
  const [detail, setDetail] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex gap-2"><Input placeholder="Search your saved publication records" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-48"><option value="">Any verification</option><option value="verified">Verified</option><option value="unverified">Unverified</option><option value="mismatch">Mismatch</option><option value="not_found">Not found</option></Select></div>
      {data?.items.map((p: any) => <div key={p.id} onDoubleClick={() => setDetail(p.id)}><PubCard p={p} projectId={projectId} onChange={mutate} />
        <button className="ml-1 mt-1 text-xs text-accent" onClick={() => setDetail(p.id)}>Evidence & details →</button></div>)}
      {data?.items.length === 0 && <Empty title="No publication records" />}
      <EvidenceModal pubId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

function EvidenceModal({ pubId, onClose }: { pubId: string | null; onClose: () => void }) {
  const { data, mutate } = useApi<any>(pubId ? `/literature/publications/${pubId}` : null);
  return (
    <Modal open={!!pubId} onClose={onClose} title={data?.title ?? "Publication"} wide>
      {data && <div className="space-y-3">
        {data.evidence.length === 0 && <p className="text-sm text-muted">No evidence extracted yet. Use “Extract evidence”.</p>}
        {data.evidence.map((e: any) => (
          <div key={e.id} className="space-y-2">
            <AIWarning>Extracted by {e.model} from the {e.basis.replace("_", " ")}. Status: {e.status.replace("_", " ")}. Every field must be traceable to its quoted location.</AIWarning>
            <dl className="grid gap-2 text-sm">{Object.entries(e.fields).map(([k, v]: any) => (
              <div key={k} className="rounded-md border border-border p-2"><dt className="text-xs font-medium text-muted">{k.replaceAll("_", " ")}</dt>
                <dd>{v?.value ?? <span className="text-muted">Not stated in source</span>}</dd>
                {v?.quote && <dd className="mt-1 text-xs italic text-muted">“{v.quote}”</dd>}
                {v?.locations?.length > 0 && <dd className="mt-1 text-[11px] text-accent">{v.locations.map((l: any) => [l.section, l.page && `p. ${l.page}`].filter(Boolean).join(", ") || l.label).join(" · ")}</dd>}
              </div>))}</dl>
            <div className="flex gap-2"><Button size="sm" variant="primary" onClick={async () => { await api(`/literature/evidence/${e.id}`, { method: "PUT", body: { status: "researcher_verified" } }); mutate(); }}>Mark as verified by me</Button>
              <Button size="sm" variant="danger" onClick={async () => { await api(`/literature/evidence/${e.id}`, { method: "PUT", body: { status: "rejected" } }); mutate(); }}>Reject extraction</Button></div>
          </div>))}
      </div>}
    </Modal>
  );
}

function QueueTab() {
  const [status, setStatus] = useState("pending");
  const { data, mutate } = useApi<any>(`/literature/queue?status=${status}`);
  const { data: projects } = useApi<any[]>("/projects");
  const { data: colls } = useApi<any[]>("/knowledge/collections");
  const [target, setTarget] = useState({ project_id: "", collection_id: "", classification: "" });
  const decide = async (id: string, s: string) => {
    await api(`/literature/queue/${id}`, { body: { status: s, project_id: target.project_id || null, collection_id: target.collection_id || null, classification: target.classification } });
    mutate();
  };
  return (
    <div className="space-y-3">
      <Card><CardBody className="grid gap-2 md:grid-cols-4">
        <Select value={status} onChange={(e) => setStatus(e.target.value)}>{["pending", "saved", "approved", "rejected"].map((s) => <option key={s} value={s}>{s} ({data?.counts?.[s] ?? 0})</option>)}</Select>
        <Select value={target.project_id} onChange={(e) => setTarget({ ...target, project_id: e.target.value })}><option value="">On approval: no project</option>{projects?.map((p) => <option key={p.id} value={p.id}>→ references of {p.title}</option>)}</Select>
        <Select value={target.collection_id} onChange={(e) => setTarget({ ...target, collection_id: e.target.value })}><option value="">No collection</option>{colls?.map((c) => <option key={c.id} value={c.id}>→ {c.name}</option>)}</Select>
        <Input placeholder="Classification (e.g. core, background)" value={target.classification} onChange={(e) => setTarget({ ...target, classification: e.target.value })} />
      </CardBody></Card>
      {data?.items.length === 0 && <Empty title="Nothing here">Create a scholarly update watch to populate the review queue.</Empty>}
      {data?.items.map((it: any) => (
        <div key={it.id}>
          <PubCard p={it.publication} />
          {status === "pending" || status === "saved" ? (
            <div className="mt-1 flex gap-1.5"><Button size="sm" variant="primary" onClick={() => decide(it.id, "approved")}>Approve</Button><Button size="sm" onClick={() => decide(it.id, "saved")}>Save for later</Button><Button size="sm" variant="danger" onClick={() => decide(it.id, "rejected")}>Reject</Button></div>
          ) : <div className="mt-1 text-xs text-muted">{it.status}{it.classification ? ` · ${it.classification}` : ""}</div>}
        </div>
      ))}
    </div>
  );
}

function WatchesTab() {
  const { data, mutate } = useApi<any[]>("/literature/watches");
  const [f, setF] = useState({ name: "", query: "", authors: "", frequency_days: 7, year_from: "" });
  const [authorQ, setAuthorQ] = useState("");
  const [authors, setAuthors] = useState<any[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const create = async () => {
    const r = await api("/literature/watches", { body: { name: f.name, query: f.query, authors: f.authors.split(",").map((s) => s.trim()).filter(Boolean),
      frequency_days: Number(f.frequency_days), filters: f.year_from ? { year_from: Number(f.year_from) } : {}, sources: ["openalex", "crossref"] } });
    setJob(r.job_id);
    mutate();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="h-fit"><CardHeader title="New scholarly update" description="Periodic discovery by topic, keyword or author. New items enter the review queue — never your evidence base directly." />
        <CardBody className="space-y-2">
          <Input placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <Input placeholder="Query / keywords" value={f.query} onChange={(e) => setF({ ...f, query: e.target.value })} />
          <div className="flex gap-1"><Input placeholder="Find an author (OpenAlex)" value={authorQ} onChange={(e) => setAuthorQ(e.target.value)} />
            <Button onClick={async () => setAuthors(await api(`/literature/authors?q=${encodeURIComponent(authorQ)}`))}>Find</Button></div>
          {authors.map((a) => <button key={a.id} className="block w-full rounded px-2 py-1 text-left text-xs hover:bg-surface-2" onClick={() => setF({ ...f, authors: [f.authors, a.id].filter(Boolean).join(", ") })}>
            {a.name} <span className="text-muted">· {a.institution} · {a.works_count} works</span></button>)}
          <Input placeholder="OpenAlex author IDs (comma-separated)" value={f.authors} onChange={(e) => setF({ ...f, authors: e.target.value })} />
          <div className="flex gap-2"><Input type="number" min={1} value={f.frequency_days} onChange={(e) => setF({ ...f, frequency_days: Number(e.target.value) })} /><span className="self-center text-xs text-muted">days</span>
            <Input placeholder="From year" value={f.year_from} onChange={(e) => setF({ ...f, year_from: e.target.value })} /></div>
          <Button variant="primary" onClick={create} disabled={!f.name || (!f.query && !f.authors)}>Create and run now</Button>
          <JobProgress jobId={job} compact onDone={() => mutate()} />
        </CardBody></Card>
      <div className="space-y-2 lg:col-span-2">{data?.map((w) => (
        <Card key={w.id}><CardBody className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1"><div className="font-medium">{w.name}</div><div className="text-xs text-muted">{w.query} {w.authors.length ? `· ${w.authors.length} author(s)` : ""} · every {w.frequency_days} days · last run {fmtDateTime(w.last_run_at)} ({w.last_result_count} new)</div></div>
          <Badge tone={w.is_active ? "ok" : "neutral"}>{w.is_active ? "active" : "paused"}</Badge>
          <Button size="sm" onClick={async () => setJob((await api(`/literature/watches/${w.id}/run`, { method: "POST" })).job_id)}>Run now</Button>
          <Button size="sm" variant="ghost" onClick={async () => { await api(`/literature/watches/${w.id}`, { method: "PUT", body: { ...w, is_active: !w.is_active } }); mutate(); }}>{w.is_active ? "Pause" : "Activate"}</Button>
          <Button size="sm" variant="ghost" onClick={async () => { await api(`/literature/watches/${w.id}`, { method: "DELETE" }); mutate(); }}>Delete</Button>
        </CardBody></Card>))}
        {data?.length === 0 && <Empty title="No scholarly updates configured" />}
      </div>
    </div>
  );
}

export default function LiteraturePage() {
  return <Suspense><LiteratureInner /></Suspense>;
}
