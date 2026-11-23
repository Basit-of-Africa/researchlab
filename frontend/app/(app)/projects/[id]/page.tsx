"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Archive, Copy, Download, Plus, Trash2 } from "lucide-react";
import { api, download, useApi } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Tabs, Textarea } from "@/components/ui";
import { ApprovalBar, BulletList, CategoryBadge, CategorySelect, ConceptualModel, JobProgress, StatusBadge, VerificationBadge } from "@/components/research";
import { fmtDate, fmtDateTime } from "@/lib/utils";

type Tab = "overview" | "questions" | "theory" | "methods" | "notes" | "references" | "decisions" | "history";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: p, mutate } = useApi<any>(`/projects/${id}`);
  const [tab, setTab] = useState<Tab>("overview");
  const [err, setErr] = useState<unknown>(null);
  if (!p) return null;

  const decide = async (entity_type: string, entity_id: string, decision: string, rationale: string, extra: any = {}) => {
    setErr(null);
    try {
      await api(`/projects/${id}/approvals`, { body: { entity_type, entity_id, decision, rationale, ...extra } });
      mutate();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHeader title={p.title} description={p.description}
        actions={<>
          <Button variant="outline" size="sm" onClick={async () => { const d = await api(`/projects/${id}/duplicate`, { method: "POST" }); router.push(`/projects/${d.id}`); }}><Copy size={13} /> Duplicate</Button>
          <Button variant="outline" size="sm" onClick={() => download(`/projects/${id}/export`)}><Download size={13} /> Export</Button>
          <Button variant="outline" size="sm" onClick={async () => { await api(`/projects/${id}/archive?archived=${p.status !== "archived"}`, { method: "POST" }); mutate(); }}>
            <Archive size={13} /> {p.status === "archived" ? "Unarchive" : "Archive"}</Button>
          <Button variant="danger" size="sm" onClick={async () => {
            const t = prompt(`Type the project title to permanently delete it. Knowledge-base documents are kept.\n\n${p.title}`);
            if (t) { try { await api(`/projects/${id}?confirm=${encodeURIComponent(t)}`, { method: "DELETE" }); router.push("/projects"); } catch (e) { setErr(e); } }
          }}><Trash2 size={13} /></Button>
        </>} />
      <ErrorNote error={err} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "overview", label: "Overview" }, { value: "questions", label: `Research questions (${p.questions.length})` },
        { value: "theory", label: `Theoretical framework (${p.frameworks.length})` }, { value: "methods", label: `Methodology (${p.methods.length})` },
        { value: "notes", label: "Notes" }, { value: "references", label: "References" }, { value: "decisions", label: "Decision log" }, { value: "history", label: "History" },
      ]} />
      <div className="mt-4">
        {tab === "overview" && <Overview p={p} mutate={mutate} />}
        {tab === "questions" && <Questions p={p} mutate={mutate} decide={decide} />}
        {tab === "theory" && <Theory p={p} mutate={mutate} decide={decide} />}
        {tab === "methods" && <Methods p={p} mutate={mutate} decide={decide} />}
        {tab === "notes" && <Notes projectId={id} />}
        {tab === "references" && <References projectId={id} />}
        {tab === "decisions" && <Decisions projectId={id} />}
        {tab === "history" && <History projectId={id} />}
      </div>
    </>
  );
}

function Overview({ p, mutate }: { p: any; mutate: () => void }) {
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ title: p.title, description: p.description, discipline: p.discipline, objectives: (p.objectives ?? []).join("\n") });
  const { data: mss } = useApi<any[]>(`/manuscripts?project_id=${p.id}`);
  const save = async () => {
    await api(`/projects/${p.id}`, { method: "PATCH", body: { ...f, objectives: f.objectives.split("\n").filter((s: string) => s.trim()) } });
    setEdit(false);
    mutate();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader title="Project definition" actions={<Button size="sm" variant="ghost" onClick={() => setEdit(!edit)}>{edit ? "Cancel" : "Edit"}</Button>} />
        <CardBody className="space-y-3">
          {edit ? (
            <>
              <Field label="Title"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
              <Field label="Discipline"><Input value={f.discipline} onChange={(e) => setF({ ...f, discipline: e.target.value })} /></Field>
              <Field label="Description"><Textarea value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
              <Field label="Objectives (one per line)"><Textarea value={f.objectives} onChange={(e) => setF({ ...f, objectives: e.target.value })} /></Field>
              <Button variant="primary" onClick={save}>Save</Button>
            </>
          ) : (
            <>
              <div><div className="text-xs text-muted">Discipline</div><div>{p.discipline || "—"}</div></div>
              <div><div className="text-xs text-muted">Objectives</div><BulletList items={p.objectives} empty="No objectives recorded." /></div>
              <div className="text-xs text-muted">Created {fmtDate(p.created_at)} · status {p.status}</div>
            </>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Next steps" />
        <CardBody className="space-y-2 text-sm">
          <Link className="block text-accent hover:underline" href={`/workspace?tab=idea&project=${p.id}`}>Explore or refine the research idea →</Link>
          <Link className="block text-accent hover:underline" href={`/workspace?tab=challenge&project=${p.id}`}>Challenge an argument →</Link>
          <Link className="block text-accent hover:underline" href={`/literature?project=${p.id}`}>Discover literature for this project →</Link>
          <Link className="block text-accent hover:underline" href={`/manuscripts?new=1&project=${p.id}`}>Start a manuscript →</Link>
          <Link className="block text-accent hover:underline" href={`/analysis?project=${p.id}`}>Analyse project data →</Link>
        </CardBody>
      </Card>
      <Card className="lg:col-span-3">
        <CardHeader title="Manuscripts" />
        <CardBody className="p-0">
          {mss?.length ? <ul className="divide-y divide-border">{mss.map((m) => (
            <li key={m.id}><Link href={`/manuscripts/${m.id}`} className="flex justify-between px-4 py-2.5 hover:bg-surface-2"><span>{m.title}</span>
              <span className="text-xs text-muted">{m.total_words.toLocaleString()} / {m.target_word_count.toLocaleString()} words · {m.status}</span></Link></li>))}</ul>
            : <p className="p-4 text-sm text-muted">No manuscripts for this project.</p>}
        </CardBody>
      </Card>
      {p.ideas?.length > 0 && (
        <Card className="lg:col-span-3">
          <CardHeader title="Research ideas explored" />
          <CardBody className="space-y-2">{p.ideas.map((i: any) => (
            <Link key={i.id} href={`/workspace?tab=idea&idea=${i.id}`} className="block rounded-md border border-border p-3 hover:bg-surface-2">
              <div className="text-sm">{i.statement}</div><div className="mt-1 flex gap-1.5"><Badge>v{i.version}</Badge><StatusBadge status={i.status} /></div></Link>))}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Questions({ p, mutate, decide }: { p: any; mutate: () => void; decide: any }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("primary");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const add = async () => {
    await api(`/projects/${p.id}/questions`, { body: { text, kind } });
    setText("");
    mutate();
  };
  return (
    <div className="space-y-4">
      <Card><CardBody className="flex flex-col gap-2 sm:flex-row">
        <Input placeholder="Add a research question, hypothesis or objective" value={text} onChange={(e) => setText(e.target.value)} />
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="sm:w-40">
          <option value="primary">Primary RQ</option><option value="secondary">Secondary RQ</option><option value="hypothesis">Hypothesis</option><option value="objective">Objective</option>
        </Select>
        <Button variant="primary" onClick={add} disabled={text.length < 5}><Plus size={14} /> Add</Button>
      </CardBody></Card>
      <p className="text-xs text-muted">Only questions you approve are treated as settled by the agents. AI-proposed questions start as proposals.</p>
      {p.questions.length === 0 && <Empty title="No research questions yet">Add your own, or explore an idea in the Research Workspace to receive candidates.</Empty>}
      {p.questions.map((q: any) => (
        <Card key={q.id}><CardBody className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5"><Badge>{q.kind}</Badge><StatusBadge status={q.status} />{q.origin === "ai" && <Badge tone="ai">AI-proposed</Badge>}</div>
          {editing === q.id ? (
            <div className="flex gap-2"><Input value={editText} onChange={(e) => setEditText(e.target.value)} />
              <Button size="sm" variant="primary" onClick={async () => { await api(`/projects/${p.id}/questions/${q.id}`, { method: "PATCH", body: { text: editText, kind: q.kind } }); setEditing(null); mutate(); }}>Save</Button></div>
          ) : <p className="text-[15px]">{q.text}</p>}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ApprovalBar allow={["approved", "rejected", "deferred"]} onDecide={(d, r) => decide("research_question", q.id, d, r)} />
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => { setEditing(q.id); setEditText(q.text); }}>Revise</Button>
              <Button size="sm" variant="ghost" onClick={async () => { await api(`/projects/${p.id}/questions/${q.id}`, { method: "DELETE" }); mutate(); }}><Trash2 size={13} /></Button>
            </div>
          </div>
        </CardBody></Card>
      ))}
    </div>
  );
}

function Theory({ p, mutate, decide }: { p: any; mutate: () => void; decide: any }) {
  const [problem, setProblem] = useState(p.description || "");
  const [cands, setCands] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const run = async () => {
    const r = await api("/research/theory", { body: { problem, project_id: p.id, candidate_theories: cands.split(",").map((s) => s.trim()).filter(Boolean) } });
    setJob(r.job_id);
  };
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Theoretical Analyst" description="Identifies and evaluates theories for this specific problem. Recommendations require your approval." />
        <CardBody className="space-y-2">
          <Textarea value={problem} onChange={(e) => setProblem(e.target.value)} placeholder="Describe the research problem" />
          <Input value={cands} onChange={(e) => setCands(e.target.value)} placeholder="Theories you want considered (comma-separated, optional)" />
          <Button variant="primary" onClick={run} disabled={problem.length < 10}>Analyse theoretical options</Button>
          <JobProgress jobId={job} onDone={() => mutate()} />
        </CardBody>
      </Card>
      {p.frameworks.map((f: any) => (
        <Card key={f.id}>
          <CardHeader title={f.name} description={f.origins}
            actions={<><StatusBadge status={f.status} />{f.origin === "ai" ? <Badge tone="ai">AI-proposed</Badge> : <Badge tone="accent">{f.origin}</Badge>}<CategoryBadge category={f.knowledge_category} /></>} />
          <CardBody className="space-y-3">
            {f.model?.nodes?.length > 0 && <ConceptualModel model={f.model} />}
            {f.hypotheses?.length > 0 && <div><div className="text-xs font-medium text-muted">Hypotheses</div>
              <ul className="list-disc pl-5 text-sm">{f.hypotheses.map((h: any, i: number) => <li key={i}><b>{h.id}</b> {h.statement} <span className="text-muted">— {h.rationale}</span></li>)}</ul></div>}
            {f.suitability && <div><div className="text-xs font-medium text-muted">Suitability for this problem</div><p className="text-sm">{f.suitability}</p></div>}
            <div className="grid gap-3 md:grid-cols-3">
              <div><div className="text-xs font-medium text-muted">Assumptions</div><BulletList items={f.assumptions} /></div>
              <div><div className="text-xs font-medium text-muted">Strengths</div><BulletList items={f.strengths} /></div>
              <div><div className="text-xs font-medium text-muted">Limitations</div><BulletList items={f.limitations} /></div>
            </div>
            {f.constructs?.length > 0 && <div><div className="text-xs font-medium text-muted">Constructs</div>
              <ul className="text-sm">{f.constructs.map((c: any, i: number) => <li key={i}><b>{c.name}</b>: {c.definition}</li>)}</ul></div>}
            {f.source_refs?.length > 0 && <div className="text-xs text-muted">Sources: {f.source_refs.map((s: any) => s.title || s.label).filter(Boolean).join("; ")}</div>}
            <ApprovalBar allow={["approved", "rejected", "deferred"]} onDecide={(d, r) => decide("framework", f.id, d, r)} />
          </CardBody>
        </Card>
      ))}
      {!p.frameworks.length && <Empty title="No frameworks yet" />}
    </div>
  );
}

const METHOD_FIELDS = ["approach", "design", "population", "sampling", "sample_size", "instruments", "data_collection", "reliability_validity", "ethics", "analysis_plan", "rationale"];

function Methods({ p, mutate, decide }: { p: any; mutate: () => void; decide: any }) {
  const [problem, setProblem] = useState(p.questions.filter((q: any) => q.status === "approved").map((q: any) => q.text).join("\n") || p.description);
  const [prefs, setPrefs] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const [editing, setEditing] = useState<any>(null);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Methodology Adviser" description="Recommends and justifies a research design. It never fabricates data, respondents or results." />
        <CardBody className="space-y-2">
          <Textarea value={problem} onChange={(e) => setProblem(e.target.value)} placeholder="Research problem / approved questions" />
          <Input value={prefs} onChange={(e) => setPrefs(e.target.value)} placeholder="Preferences or constraints (e.g. mixed methods, 6-month timeline, no budget for incentives)" />
          <div className="flex gap-2">
            <Button variant="primary" disabled={problem.length < 10} onClick={async () => setJob((await api("/research/methodology", { body: { problem, project_id: p.id, preferences: prefs } })).job_id)}>Get methodological advice</Button>
            <Button variant="outline" onClick={() => setEditing(Object.fromEntries(METHOD_FIELDS.map((k) => [k, ""])))}>Write my own design</Button>
          </div>
          <JobProgress jobId={job} onDone={() => mutate()} />
        </CardBody>
      </Card>
      {p.methods.map((m: any) => (
        <Card key={m.id}>
          <CardHeader title={`${m.approach || "Design"}: ${m.design?.slice(0, 120) || ""}`} actions={<><StatusBadge status={m.status} />{m.origin === "ai" && <Badge tone="ai">AI-proposed</Badge>}</>} />
          <CardBody className="space-y-3">
            <dl className="grid gap-x-6 gap-y-2 text-sm md:grid-cols-2">
              {METHOD_FIELDS.map((k) => m[k] && <div key={k}><dt className="text-xs text-muted">{k.replace("_", " ")}</dt><dd className="whitespace-pre-wrap">{String(m[k])}</dd></div>)}
            </dl>
            {m.variables?.length > 0 && <table className="w-full text-xs"><thead><tr className="text-left text-muted"><th>Variable</th><th>Role</th><th>Operational definition</th><th>Measure</th></tr></thead>
              <tbody>{m.variables.map((v: any, i: number) => <tr key={i} className="border-t border-border"><td className="py-1">{v.name}</td><td>{v.role}</td><td>{v.operational_definition}</td><td>{v.measure}</td></tr>)}</tbody></table>}
            <div className="flex flex-wrap justify-between gap-2">
              <ApprovalBar allow={["approved", "rejected", "deferred"]} onDecide={(d, r) => decide("method", m.id, d, r)} />
              <Button size="sm" variant="ghost" onClick={() => setEditing(m)}>Edit</Button>
            </div>
          </CardBody>
        </Card>
      ))}
      <Modal open={!!editing} onClose={() => setEditing(null)} title="Research design" wide>
        {editing && <div className="grid gap-3 md:grid-cols-2">
          {METHOD_FIELDS.map((k) => <Field key={k} label={k.replace("_", " ")} className={k === "rationale" || k === "analysis_plan" ? "md:col-span-2" : ""}>
            <Textarea className="min-h-[60px]" value={editing[k] ?? ""} onChange={(e) => setEditing({ ...editing, [k]: e.target.value })} /></Field>)}
          <div className="md:col-span-2 flex justify-end"><Button variant="primary" onClick={async () => {
            const body = Object.fromEntries(METHOD_FIELDS.map((k) => [k, editing[k] ?? ""]));
            if (editing.id) await api(`/projects/${p.id}/methods/${editing.id}`, { method: "PUT", body: { ...body, variables: editing.variables ?? [] } });
            else await api(`/projects/${p.id}/methods`, { body });
            setEditing(null); mutate();
          }}>Save</Button></div>
        </div>}
      </Modal>
    </div>
  );
}

function Notes({ projectId }: { projectId?: string }) {
  const { data, mutate } = useApi<any[]>(`/notes${projectId ? `?project_id=${projectId}` : ""}`);
  const [f, setF] = useState({ title: "", body: "", note_type: "observation", knowledge_category: "provisional_opinion" });
  const add = async () => {
    await api("/notes", { body: { ...f, project_id: projectId } });
    setF({ ...f, title: "", body: "" });
    mutate();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-1 h-fit">
        <CardHeader title="New research note" description="Classify every note: your established positions and provisional opinions are kept distinct." />
        <CardBody className="space-y-2">
          <Input placeholder="Title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
          <Textarea placeholder="Observation, idea, annotation, argument…" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} className="min-h-[120px]" />
          <Select value={f.note_type} onChange={(e) => setF({ ...f, note_type: e.target.value })}>
            {["observation", "idea", "annotation", "theoretical", "methodological", "question", "argument", "lesson"].map((t) => <option key={t}>{t}</option>)}
          </Select>
          <CategorySelect value={f.knowledge_category} onChange={(v) => setF({ ...f, knowledge_category: v })} exclude={["ai_interpretation"]} />
          <Button variant="primary" onClick={add} disabled={!f.body}>Save note</Button>
        </CardBody>
      </Card>
      <div className="space-y-2 lg:col-span-2">
        {data?.map((n) => (
          <Card key={n.id}><CardBody>
            <div className="flex flex-wrap items-center gap-1.5"><span className="font-medium">{n.title || "Untitled note"}</span><Badge>{n.note_type}</Badge><CategoryBadge category={n.knowledge_category} />
              <span className="ml-auto text-xs text-muted">{fmtDateTime(n.updated_at)}</span>
              <button className="text-muted hover:text-danger" onClick={async () => { await api(`/notes/${n.id}`, { method: "DELETE" }); mutate(); }}><Trash2 size={13} /></button></div>
            <p className="mt-1.5 whitespace-pre-wrap text-sm">{n.body}</p>
            {n.document_id && <Link href={`/knowledge/${n.document_id}`} className="mt-1 block text-xs text-accent">Linked document →</Link>}
          </CardBody></Card>
        ))}
        {data && !data.length && <Empty title="No notes yet" />}
      </div>
    </div>
  );
}

function References({ projectId }: { projectId: string }) {
  const [style, setStyle] = useState("apa7");
  const { data, mutate } = useApi<any[]>(`/literature/references?project_id=${projectId}&style=${style}`);
  const [job, setJob] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [m, setM] = useState({ title: "", authors: "", year: "", venue: "", doi: "" });
  const importFile = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    await api(`/literature/references/import?project_id=${projectId}`, { form: fd });
    mutate();
  };
  return (
    <Card>
      <CardHeader title="Project references" description="The Academic Writer may cite only these references, by citekey. Verify them against Crossref/OpenAlex before submission."
        actions={<>
          <Select value={style} onChange={(e) => setStyle(e.target.value)} className="h-8 w-36 text-xs"><option value="apa7">APA 7</option><option value="harvard">Harvard</option><option value="chicago_ad">Chicago</option></Select>
          <label className="inline-flex h-7 cursor-pointer items-center rounded-md bg-surface-2 px-2.5 text-xs font-medium hover:bg-border">Import BibTeX / RIS / CSL-JSON
            <input type="file" accept=".bib,.ris,.json" className="hidden" onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} /></label>
          <Button size="sm" variant="outline" onClick={() => setManual(true)}><Plus size={12} /> Manual</Button>
          <Button size="sm" variant="primary" onClick={async () => setJob((await api(`/literature/references/verify?project_id=${projectId}`, { method: "POST" })).job_id)}>Verify all</Button>
        </>} />
      <CardBody className="space-y-2">
        <JobProgress jobId={job} onDone={() => mutate()} />
        {data?.length ? (
          <table className="w-full text-sm"><tbody>{data.map((r) => (
            <tr key={r.id} className="border-b border-border align-top last:border-0">
              <td className="py-2 pr-3 font-mono text-xs">@{r.citekey}</td>
              <td className="py-2 pr-3">{r.formatted.replaceAll("*", "")}</td>
              <td className="py-2 pr-3"><VerificationBadge status={r.verification_status} />{r.source_type === "secondary" && <Badge className="ml-1">secondary</Badge>}
                {r.verification_detail?.comparison?.issues?.length > 0 && <div className="mt-1 text-[11px] text-danger">{r.verification_detail.comparison.issues.map((i: any) => `${i.field}: claimed “${i.claimed}”, registry “${Array.isArray(i.registry) ? i.registry.join(", ") : i.registry}”`).join("; ")}</div>}</td>
              <td className="py-2"><button className="text-muted hover:text-danger" onClick={async () => { await api(`/literature/references/${r.id}`, { method: "DELETE" }); mutate(); }}><Trash2 size={13} /></button></td>
            </tr>))}</tbody></table>
        ) : <Empty title="No references yet">Approve publications from Literature Discovery into this project, add knowledge-base documents, or import a reference file.</Empty>}
      </CardBody>
      <Modal open={manual} onClose={() => setManual(false)} title="Add reference manually">
        <div className="space-y-2">
          <Input placeholder="Title" value={m.title} onChange={(e) => setM({ ...m, title: e.target.value })} />
          <Input placeholder="Authors (Family, Given; Family, Given)" value={m.authors} onChange={(e) => setM({ ...m, authors: e.target.value })} />
          <div className="flex gap-2"><Input placeholder="Year" value={m.year} onChange={(e) => setM({ ...m, year: e.target.value })} /><Input placeholder="DOI" value={m.doi} onChange={(e) => setM({ ...m, doi: e.target.value })} /></div>
          <Input placeholder="Journal / publisher" value={m.venue} onChange={(e) => setM({ ...m, venue: e.target.value })} />
          <p className="text-xs text-muted">Manually entered references start as “unverified”.</p>
          <Button variant="primary" onClick={async () => {
            const authors = m.authors.split(";").map((a) => a.trim()).filter(Boolean).map((a) => { const [family, given] = a.split(",").map((x) => x.trim()); return { family, given: given ?? "" }; });
            await api("/literature/references", { body: { project_id: projectId, csl: { title: m.title, authors, year: Number(m.year) || null, venue: m.venue, doi: m.doi || null } } });
            setManual(false); mutate();
          }}>Add</Button>
        </div>
      </Modal>
    </Card>
  );
}

function Decisions({ projectId }: { projectId: string }) {
  const { data } = useApi<any[]>(`/projects/${projectId}/decisions`);
  return (
    <Card><CardHeader title="Decision and approval log" description="A permanent record of significant research decisions. Only you make these." />
      <CardBody className="p-0">{data?.length ? <ul className="divide-y divide-border">{data.map((d) => (
        <li key={d.id} className="px-4 py-2.5 text-sm">
          <div className="flex flex-wrap items-center gap-1.5"><StatusBadge status={d.decision} /><Badge>{d.decision_type.replace("_", " ")}</Badge><span className="ml-auto text-xs text-muted">{fmtDateTime(d.created_at)}</span></div>
          <div className="mt-1">{d.subject}</div>
          {d.rationale && <div className="mt-0.5 text-xs text-muted">Rationale: {d.rationale}</div>}
          {d.instructions && <div className="mt-0.5 text-xs text-muted">Instructions: {d.instructions}</div>}
        </li>))}</ul> : <p className="p-4 text-sm text-muted">No decisions recorded yet.</p>}</CardBody></Card>
  );
}

function History({ projectId }: { projectId: string }) {
  const { data } = useApi<any>(`/projects/${projectId}/history`);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card><CardHeader title="Project activity" /><CardBody className="p-0">
        <ul className="divide-y divide-border text-sm">{data?.events?.map((e: any, i: number) => <li key={i} className="flex justify-between px-4 py-2"><span>{e.action}</span><span className="text-xs text-muted">{fmtDateTime(e.at)}</span></li>)}</ul></CardBody></Card>
      <Card><CardHeader title="Agent runs" description="Every AI invocation is logged with provider, model and any withheld private sources." /><CardBody className="p-0">
        <ul className="divide-y divide-border text-sm">{data?.agent_runs?.map((r: any) => (
          <li key={r.id} className="px-4 py-2"><div className="flex justify-between"><span><b>{r.agent}</b> · {r.task}</span><Badge tone={r.status === "succeeded" ? "ok" : "danger"}>{r.status}</Badge></div>
            <div className="text-xs text-muted">{r.provider}:{r.model} · {r.duration_ms ?? "?"} ms{r.excluded_private_sources ? ` · ${r.excluded_private_sources} private sources withheld` : ""} · {fmtDateTime(r.at)}</div>
            {r.error && <div className="text-xs text-danger">{r.error}</div>}</li>))}</ul></CardBody></Card>
    </div>
  );
}
