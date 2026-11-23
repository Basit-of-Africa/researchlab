"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CheckCircle2, Download, RotateCcw, Upload } from "lucide-react";
import { api, download, useApi } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { AIWarning, JobProgress } from "@/components/research";
import { fmtDateTime } from "@/lib/utils";

type Tab = "quant" | "qual";

function AnalysisInner() {
  const sp = useSearchParams();
  const [tab, setTab] = useState<Tab>("quant");
  const [project, setProject] = useState(sp.get("project") ?? "");
  const { data: projects } = useApi<any[]>("/projects");
  return (
    <>
      <PageHeader title="Data analysis" description="Reproducible statistical and qualitative analysis. Source data is never modified; every procedure stores its script, assumptions and outputs."
        actions={<Select value={project} onChange={(e) => setProject(e.target.value)} className="w-72"><option value="">All projects</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</Select>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "quant", label: "Datasets & statistics" }, { value: "qual", label: "Qualitative analysis" }]} />
      <div className="mt-4">{tab === "quant" ? <Quant projectId={project} /> : project ? <Qual projectId={project} /> : <Empty title="Select a project">Qualitative coding is organised per project.</Empty>}</div>
    </>
  );
}

function Quant({ projectId }: { projectId: string }) {
  const { data: datasets, mutate } = useApi<any[]>(`/analysis/datasets${projectId ? `?project_id=${projectId}` : ""}`);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const up = async (file: File) => {
    setErr(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (projectId) fd.append("project_id", projectId);
      const d = await api("/analysis/datasets", { form: fd });
      setSel(d.id);
      mutate();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-4">
      <Card className="h-fit">
        <CardHeader title="Datasets" actions={<label className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-md bg-accent px-2.5 text-xs font-medium text-accent-fg">{busy ? <Spinner /> : <Upload size={12} />} Upload
          <input type="file" className="hidden" accept=".csv,.tsv,.xlsx,.xls,.json,.sav,.txt" onChange={(e) => e.target.files?.[0] && up(e.target.files[0])} /></label>} />
        <CardBody className="space-y-0.5 p-2">
          <ErrorNote error={err} />
          {datasets?.map((d) => (
            <button key={d.id} onClick={() => setSel(d.id)} className={`block w-full rounded px-2 py-1.5 text-left text-sm ${sel === d.id ? "bg-accent-soft" : "hover:bg-surface-2"}`}>
              <div className="truncate">{d.name}</div>
              <div className="text-[11px] text-muted">{d.kind === "text" ? "text" : `${d.n_rows} × ${d.n_cols}`} · {d.file_format}{d.parent_dataset_id ? " · derived" : ""}</div>
            </button>
          ))}
          {datasets?.length === 0 && <p className="px-2 text-xs text-muted">CSV, TSV, XLSX, JSON, SAV or TXT (transcripts).</p>}
        </CardBody>
      </Card>
      <div className="lg:col-span-3">{sel ? <DatasetView id={sel} projectId={projectId} onDerived={(id) => { setSel(id); mutate(); }} /> : <Empty title="Select or upload a dataset" />}</div>
    </div>
  );
}

function DatasetView({ id, projectId, onDerived }: { id: string; projectId: string; onDerived: (id: string) => void }) {
  const { data: d } = useApi<any>(`/analysis/datasets/${id}`);
  const { data: procs } = useApi<any>("/analysis/procedures");
  const { data: analyses, mutate } = useApi<any[]>(`/analysis/analyses?dataset_id=${id}`);
  const [proc, setProc] = useState("describe");
  const [params, setParams] = useState<Record<string, any>>({});
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [transform, setTransform] = useState(false);
  if (!d) return <Spinner />;
  const cols: string[] = d.schema?.map((c: any) => c.name) ?? [];
  const spec: Record<string, string> = procs?.[proc]?.params ?? {};
  const runIt = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api("/analysis/run", { body: { dataset_id: id, procedure: proc, params, project_id: projectId || null } });
      mutate();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title={d.name} description={d.kind === "text" ? "Text dataset — use the qualitative tab to segment and code it." : `${d.n_rows} rows × ${d.n_cols} variables · sha256 ${d.content_hash.slice(0, 12)}…`}
          actions={<>{d.kind === "tabular" && <Button size="sm" variant="outline" onClick={() => setTransform(true)}>Clean / transform</Button>}
            <Button size="sm" variant="ghost" onClick={() => download(`/analysis/datasets/${id}/file`)}><Download size={13} /> Original</Button></>} />
        <CardBody className="space-y-3">
          {d.transformations?.length > 0 && <div className="text-xs"><span className="font-medium text-muted">Recorded transformations: </span>{d.transformations.map((t: any) => `${t.op} (${t.rows_before}→${t.rows_after} rows)`).join(" → ")}</div>}
          {d.kind === "tabular" ? (
            <>
              <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-left text-muted"><th className="pr-3">Variable</th><th className="pr-3">Type</th><th className="pr-3">Missing</th><th className="pr-3">Unique</th><th>Examples</th></tr></thead>
                <tbody>{d.schema.map((c: any) => <tr key={c.name} className="border-t border-border"><td className="py-1 pr-3 font-medium">{c.name}</td><td className="pr-3">{c.kind}</td><td className={`pr-3 ${c.n_missing ? "text-warn" : ""}`}>{c.n_missing}</td><td className="pr-3">{c.n_unique}</td><td className="truncate text-muted">{c.example_values.join(", ")}</td></tr>)}</tbody></table></div>
              <details><summary className="cursor-pointer text-xs text-muted">Preview first rows</summary>
                <div className="mt-2 max-h-72 overflow-auto"><table className="text-[11px]"><thead><tr>{d.preview.columns.map((c: string) => <th key={c} className="px-2 text-left">{c}</th>)}</tr></thead>
                  <tbody>{d.preview.rows.map((r: any, i: number) => <tr key={i} className="border-t border-border">{d.preview.columns.map((c: string) => <td key={c} className="px-2 py-0.5">{r[c] === null ? <span className="text-muted">NA</span> : String(r[c])}</td>)}</tr>)}</tbody></table></div></details>
            </>
          ) : <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-2 text-xs">{d.preview_text}</pre>}
        </CardBody>
      </Card>
      {d.kind === "tabular" && (
        <Card>
          <CardHeader title="Run a procedure" description="Procedures are fixed, reviewed templates; parameters are validated against the dataset. No model-written code is executed." />
          <CardBody className="space-y-3">
            <div className="grid gap-2 md:grid-cols-3">
              <Field label="Procedure"><Select value={proc} onChange={(e) => { setProc(e.target.value); setParams({}); }}>
                {procs && Object.entries(procs).filter(([k]) => !k.startsWith("_")).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}</Select></Field>
              {Object.entries(spec).map(([name, typ]) => <ParamInput key={name} name={name} typ={typ} cols={cols} value={params[name]} onChange={(v) => setParams({ ...params, [name]: v })} />)}
            </div>
            <div className="flex items-center gap-2"><Button variant="primary" onClick={runIt} disabled={busy}>{busy ? <Spinner /> : null} Run analysis</Button>
              {procs?._unsupported && <span className="text-[11px] text-muted">{procs._unsupported.sem}</span>}</div>
            <ErrorNote error={err} />
          </CardBody>
        </Card>
      )}
      {analyses?.map((a) => <AnalysisCard key={a.id} a={a} onChange={mutate} />)}
      {transform && <TransformModal d={d} onClose={() => setTransform(false)} onDerived={onDerived} />}
    </div>
  );
}

function ParamInput({ name, typ, cols, value, onChange }: { name: string; typ: string; cols: string[]; value: any; onChange: (v: any) => void }) {
  const optional = typ.endsWith("?");
  const t = typ.replace("?", "");
  const label = `${name.replaceAll("_", " ")}${optional ? " (optional)" : ""}`;
  if (t === "col") return <Field label={label}><Select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}><option value="">—</option>{cols.map((c) => <option key={c}>{c}</option>)}</Select></Field>;
  if (t === "cols") return <Field label={label}><select multiple className="h-24 w-full rounded-md border border-border bg-surface px-2 text-xs" value={value ?? []}
    onChange={(e) => onChange(Array.from(e.target.selectedOptions).map((o) => o.value))}>{cols.map((c) => <option key={c}>{c}</option>)}</select></Field>;
  if (t.startsWith("choice:")) return <Field label={label}><Select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}><option value="">default</option>{t.slice(7).split(",").map((c) => <option key={c}>{c}</option>)}</Select></Field>;
  if (t === "bool") return <label className="flex items-center gap-1.5 self-end pb-2 text-sm"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {label}</label>;
  return <Field label={label}><Input type={t === "int" ? "number" : "text"} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : t === "int" ? Number(e.target.value) : e.target.value)} /></Field>;
}

function AnalysisCard({ a, onChange }: { a: any; onChange: () => void }) {
  const [repro, setRepro] = useState<any>(null);
  const [ai, setAi] = useState<string>(a.ai_interpretation);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <Card>
      <CardHeader title={a.label} description={`${fmtDateTime(a.ran_at)} · ${JSON.stringify(a.params)}`}
        actions={<>
          {a.reproducible === true && <Badge tone="ok"><CheckCircle2 size={11} /> reproduced</Badge>}{a.reproducible === false && <Badge tone="danger">not reproducible</Badge>}
          <Button size="sm" variant="ghost" onClick={async () => { setRepro(await api(`/analysis/analyses/${a.id}/reproduce`, { method: "POST" })); onChange(); }}><RotateCcw size={12} /> Reproduce</Button>
          <Button size="sm" variant="ghost" onClick={() => download(`/analysis/analyses/${a.id}/script`)}><Download size={12} /> Script</Button>
        </>} />
      <CardBody className="space-y-3">
        {a.status === "failed" ? <ErrorNote error={a.error} /> : <>
          <p className="text-sm">{a.interpretation}</p>
          {a.outputs.filter((o: any) => o.kind === "text").map((o: any) => <div key={o.id} className="rounded bg-warn-soft px-2 py-1 text-xs text-warn">{o.content.text}</div>)}
          {a.outputs.filter((o: any) => o.kind === "table").map((o: any) => <OutputTable key={o.id} o={o} />)}
          {a.outputs.filter((o: any) => o.kind === "figure").map((o: any) => <img key={o.id} src={`/api/analysis/outputs/${o.id}/figure`} alt={o.title} className="max-w-xl rounded border border-border" />)}
          {a.assumptions?.length > 0 && <div className="text-xs text-muted"><span className="font-medium">Assumptions: </span>{a.assumptions.join(" · ")}</div>}
          {repro && <div className={`rounded px-2 py-1 text-xs ${repro.reproducible ? "bg-ok-soft text-ok" : "bg-danger-soft text-danger"}`}>{repro.reproducible ? "Re-running the stored script on the stored dataset produced identical results." : "Results differed on re-run."} (hash {repro.new_hash.slice(0, 12)}…)</div>}
          {ai ? <div className="space-y-1"><AIWarning>AI interpretation — verify against the computed tables.</AIWarning><p className="whitespace-pre-wrap text-sm">{ai}</p></div> :
            <Button size="sm" variant="outline" disabled={busy} onClick={async () => { setBusy(true); setErr(null); try { setAi((await api(`/analysis/analyses/${a.id}/interpret`, { method: "POST" })).ai_interpretation); } catch (e) { setErr(e); } finally { setBusy(false); } }}>
              {busy ? <Spinner /> : null} Ask the Research Analyst to interpret</Button>}
          <ErrorNote error={err} />
        </>}
      </CardBody>
    </Card>
  );
}

function fmt(v: any) {
  if (typeof v === "number") return Math.abs(v) < 0.001 && v !== 0 ? v.toExponential(2) : Number.isInteger(v) ? v : v.toFixed(3);
  return v === null || v === undefined ? "" : String(v);
}

function OutputTable({ o }: { o: any }) {
  const t = o.content;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between"><span className="text-xs font-medium">{o.title}</span><button className="text-xs text-accent" onClick={() => download(`/analysis/outputs/${o.id}/csv`)}>CSV</button></div>
      <div className="overflow-x-auto"><table className="w-full text-xs tabular-nums"><thead><tr className="border-b border-border text-left text-muted">{t.columns.map((c: string) => <th key={c} className="pr-3 py-1">{c}</th>)}</tr></thead>
        <tbody>{t.rows.map((r: any, i: number) => <tr key={i} className="border-b border-border/60">{t.columns.map((c: string) => <td key={c} className="pr-3 py-1">{fmt(r[c])}</td>)}</tr>)}</tbody></table></div>
    </div>
  );
}

function TransformModal({ d, onClose, onDerived }: { d: any; onClose: () => void; onDerived: (id: string) => void }) {
  const [steps, setSteps] = useState(`[\n  {"op": "drop_missing", "columns": []}\n]`);
  const [name, setName] = useState(`${d.name} (cleaned)`);
  const [prev, setPrev] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const call = async (confirm: boolean) => {
    setErr(null);
    try {
      const r = await api(`/analysis/datasets/${d.id}/transform`, { body: { steps: JSON.parse(steps), name, confirm } });
      if (confirm) { onDerived(r.id); onClose(); } else setPrev(r);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <Modal open onClose={onClose} title="Clean or transform (creates a new derived dataset)" wide>
      <div className="space-y-3">
        <p className="text-xs text-muted">Allowed operations: drop_missing, fill_missing (mean/median/mode/value), recode, filter, reverse_score, compute_mean, compute_sum, rename, drop_columns, standardize, to_numeric. The original dataset is never changed; the derived dataset records every step and your approval.</p>
        <Textarea className="min-h-[160px] font-mono text-xs" value={steps} onChange={(e) => setSteps(e.target.value)} />
        <Input value={name} onChange={(e) => setName(e.target.value)} />
        <ErrorNote error={err} />
        {prev && <div className="rounded bg-surface-2 p-2 text-xs"><div>Preview: {prev.preview.n_rows} rows × {prev.preview.n_cols} columns</div><div>{prev.log.map((l: any) => `${l.op}: ${l.rows_before}→${l.rows_after}`).join(" · ")}</div></div>}
        <div className="flex justify-end gap-2"><Button onClick={() => call(false)}>Preview</Button><Button variant="primary" disabled={!prev} onClick={() => call(true)}>Approve and create derived dataset</Button></div>
      </div>
    </Modal>
  );
}

function Qual({ projectId }: { projectId: string }) {
  const { data: datasets } = useApi<any[]>(`/analysis/datasets?project_id=${projectId}`);
  const { data: segs, mutate: mSegs } = useApi<any[]>(`/analysis/qual/segments?project_id=${projectId}`);
  const { data: codes, mutate: mCodes } = useApi<any[]>(`/analysis/qual/codes?project_id=${projectId}`);
  const { data: summary, mutate: mSum } = useApi<any>(`/analysis/qual/summary?project_id=${projectId}`);
  const [newCode, setNewCode] = useState({ name: "", theme: "", description: "" });
  const [job, setJob] = useState<string | null>(null);
  const [active, setActive] = useState<string>("");
  const refreshAll = () => { mSegs(); mCodes(); mSum(); };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-3 lg:col-span-2">
        <Card><CardBody className="flex flex-wrap items-center gap-2">
          <span className="text-sm">Segment a transcript:</span>
          {datasets?.filter((d) => d.kind === "text").map((d) => <Button key={d.id} size="sm" variant="outline" onClick={async () => { await api("/analysis/qual/segments/from-source", { body: { project_id: projectId, dataset_id: d.id } }); refreshAll(); }}>{d.name}</Button>)}
          {datasets?.filter((d) => d.kind === "text").length === 0 && <span className="text-xs text-muted">Upload a .txt transcript in the Datasets tab first.</span>}
        </CardBody></Card>
        <div className="flex items-center gap-2 text-xs"><span className="text-muted">Click a segment to apply the selected code:</span>
          <Select value={active} onChange={(e) => setActive(e.target.value)} className="h-8 w-60 text-xs"><option value="">Choose code</option>{codes?.filter((c) => c.status === "approved").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></div>
        {segs?.map((s) => (
          <Card key={s.id} className={active ? "cursor-pointer hover:border-accent/50" : ""} onClick={async () => { if (active) { await api("/analysis/qual/codings", { body: { segment_id: s.id, code_id: active } }); refreshAll(); } }}>
            <CardBody className="space-y-1.5"><div className="text-[11px] text-muted">{s.source_label}</div><p className="whitespace-pre-wrap text-sm">{s.text}</p>
              <div className="flex flex-wrap gap-1">{s.codes.map((c: any) => <span key={c.coding_id} className="inline-flex items-center gap-1"><Badge tone={c.status === "approved" ? "accent" : "ai"}>{c.name}{c.status !== "approved" ? " (AI-proposed)" : ""}</Badge>
                <button className="text-[10px] text-muted" onClick={async (e) => { e.stopPropagation(); await api(`/analysis/qual/codings/${c.coding_id}`, { method: "DELETE" }); refreshAll(); }}>✕</button></span>)}</div></CardBody></Card>
        ))}
        {segs?.length === 0 && <Empty title="No segments yet" />}
      </div>
      <div className="space-y-3">
        <Card><CardHeader title="Codebook" actions={<Button size="sm" variant="outline" onClick={async () => setJob((await api(`/analysis/qual/suggest?project_id=${projectId}`, { method: "POST" })).job_id)}>Suggest codes (AI)</Button>} />
          <CardBody className="space-y-2">
            <JobProgress jobId={job} compact onDone={refreshAll} />
            <div className="flex gap-1"><Input className="h-8 text-xs" placeholder="Code" value={newCode.name} onChange={(e) => setNewCode({ ...newCode, name: e.target.value })} />
              <Input className="h-8 text-xs" placeholder="Theme" value={newCode.theme} onChange={(e) => setNewCode({ ...newCode, theme: e.target.value })} />
              <Button size="sm" disabled={!newCode.name} onClick={async () => { await api("/analysis/qual/codes", { body: { ...newCode, project_id: projectId } }); setNewCode({ name: "", theme: "", description: "" }); refreshAll(); }}>Add</Button></div>
            {codes?.map((c) => (
              <div key={c.id} className="flex items-center gap-1.5 text-sm"><span className="flex-1 truncate">{c.name} <span className="text-xs text-muted">{c.theme}</span></span>
                {c.status === "proposed" ? <><Badge tone="ai">AI-proposed</Badge><Button size="sm" variant="ghost" onClick={async () => { await api(`/analysis/qual/codes/${c.id}`, { method: "PUT", body: { status: "approved" } }); refreshAll(); }}>Approve</Button>
                  <Button size="sm" variant="ghost" onClick={async () => { await api(`/analysis/qual/codes/${c.id}`, { method: "PUT", body: { status: "rejected" } }); refreshAll(); }}>Reject</Button></> : <Badge tone={c.status === "rejected" ? "danger" : "neutral"}>{c.status}</Badge>}
              </div>))}
          </CardBody></Card>
        {summary && <Card><CardHeader title="Thematic & content analysis" description={summary.note} /><CardBody className="space-y-3 text-sm">
          <div className="text-xs text-muted">{summary.coded_segments} of {summary.segments} segments coded</div>
          <div><div className="text-xs font-medium text-muted">Code frequencies</div>{Object.entries(summary.code_frequencies).map(([k, v]) => <div key={k} className="flex justify-between text-xs"><span>{k}</span><span className="tabular-nums">{String(v)}</span></div>)}</div>
          <div><div className="text-xs font-medium text-muted">Themes</div>{Object.entries(summary.themes).map(([k, v]: any) => <div key={k} className="text-xs"><b>{k}</b>: {v.join(", ")}</div>)}</div>
          <div><div className="text-xs font-medium text-muted">Co-occurrence</div>{summary.co_occurrence.map((c: any, i: number) => <div key={i} className="text-xs">{c.a} × {c.b}: {c.n}</div>)}</div>
          <div><div className="text-xs font-medium text-muted">By source</div>{Object.entries(summary.by_source).map(([k, v]: any) => <div key={k} className="text-xs"><b>{k}</b>: {Object.entries(v).map(([c, n]) => `${c} (${n})`).join(", ")}</div>)}</div>
        </CardBody></Card>}
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  return <Suspense><AnalysisInner /></Suspense>;
}
