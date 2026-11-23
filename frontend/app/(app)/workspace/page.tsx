"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Plus, Send, Trash2 } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Field, PageHeader, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { AIWarning, ApprovalBar, BulletList, JobProgress, Prose, SourceList, StatusBadge } from "@/components/research";
import { fmtDateTime } from "@/lib/utils";

type Tab = "chat" | "idea" | "challenge";

function WorkspaceInner() {
  const sp = useSearchParams();
  const [tab, setTab] = useState<Tab>((sp.get("tab") as Tab) || "chat");
  const [project, setProject] = useState(sp.get("project") ?? "");
  const { data: projects } = useApi<any[]>("/projects");
  return (
    <>
      <PageHeader title="Research workspace" description="Work with specialised agents in your project's context. Agents recommend; you decide."
        actions={<Select value={project} onChange={(e) => setProject(e.target.value)} className="w-72"><option value="">No project context</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</Select>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "chat", label: "Agent conversation" }, { value: "idea", label: "Idea exploration" }, { value: "challenge", label: "Research Challenge Mode" }]} />
      <div className="mt-4">
        {tab === "chat" && <Chat projectId={project} />}
        {tab === "idea" && <IdeaExplorer projectId={project} initialIdea={sp.get("idea")} />}
        {tab === "challenge" && <Challenge projectId={project} />}
      </div>
    </>
  );
}

function Chat({ projectId }: { projectId: string }) {
  const { data: agents } = useApi<any[]>("/research/agents");
  const { data: threads, mutate: mThreads } = useApi<any[]>(`/research/threads${projectId ? `?project_id=${projectId}` : ""}`);
  const [tid, setTid] = useState<string | null>(null);
  const { data: thread, mutate } = useApi<any>(tid ? `/research/threads/${tid}` : null);
  const [agent, setAgent] = useState("strategist");
  const [msg, setMsg] = useState("");
  const [oa, setOa] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [thread?.messages?.length]);
  const send = async () => {
    setErr(null);
    setBusy(true);
    try {
      let t = tid;
      if (!t) {
        t = (await api("/research/threads", { body: { project_id: projectId || null } })).id;
        setTid(t);
      }
      await api(`/research/threads/${t}/messages`, { body: { content: msg, agent, use_openalex: oa } });
      setMsg("");
      mutate();
      mThreads();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const promote = async (id: string, cat: string) => {
    await api(`/research/messages/${id}/promote`, { body: { knowledge_category: cat } });
    mutate();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-4">
      <Card className="h-fit">
        <CardHeader title="Conversations" actions={<Button size="sm" variant="ghost" onClick={() => setTid(null)}><Plus size={13} /></Button>} />
        <CardBody className="space-y-0.5 p-2">
          {threads?.map((t) => (
            <div key={t.id} className={`group flex items-center rounded px-2 py-1.5 text-sm ${tid === t.id ? "bg-accent-soft text-accent" : "hover:bg-surface-2"}`}>
              <button className="flex-1 truncate text-left" onClick={() => setTid(t.id)}>{t.title}</button>
              <button className="hidden text-muted group-hover:block" onClick={async () => { await api(`/research/threads/${t.id}`, { method: "DELETE" }); if (tid === t.id) setTid(null); mThreads(); }}><Trash2 size={12} /></button>
            </div>
          ))}
          {threads?.length === 0 && <p className="px-2 text-xs text-muted">No conversations yet.</p>}
        </CardBody>
      </Card>
      <div className="space-y-3 lg:col-span-3">
        <div className="min-h-[300px] space-y-3">
          {!thread?.messages?.length && <Empty title="Start a conversation">Choose an agent. The conversation is not treated as research knowledge unless you explicitly save a message as a note.</Empty>}
          {thread?.messages?.map((m: any) => (
            <div key={m.id} className={m.role === "researcher" ? "ml-auto max-w-[85%]" : "max-w-[95%]"}>
              <Card className={m.role === "researcher" ? "bg-accent-soft/50" : ""}><CardBody className="space-y-2">
                <div className="flex items-center gap-1.5 text-xs text-muted">
                  {m.role === "researcher" ? "You" : <><Badge tone="ai">{agents?.find((a) => a.key === m.agent)?.name ?? m.agent}</Badge><span>AI interpretation</span></>}
                  <span className="ml-auto">{fmtDateTime(m.created_at)}</span>
                </div>
                {m.role === "agent" ? <Prose text={m.content} /> : <p className="whitespace-pre-wrap text-sm">{m.content}</p>}
                {m.role === "agent" && <SourceList sources={m.sources} />}
                {m.promoted_to?.note_id ? <Badge tone="ok">saved as note ({m.promoted_to.knowledge_category.replace("_", " ")})</Badge> : (
                  <div className="flex gap-1">
                    {m.role === "researcher" ? <>
                      <Button size="sm" variant="ghost" onClick={() => promote(m.id, "provisional_opinion")}>Save as provisional opinion</Button>
                      <Button size="sm" variant="ghost" onClick={() => promote(m.id, "established_position")}>Save as established position</Button>
                    </> : <Button size="sm" variant="ghost" onClick={() => promote(m.id, "ai_interpretation")}>Save as note (AI interpretation)</Button>}
                  </div>
                )}
              </CardBody></Card>
            </div>
          ))}
          <div ref={end} />
        </div>
        <Card><CardBody className="space-y-2">
          <Textarea value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="Ask, explain your thinking, or request a critique…" onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && msg) send(); }} />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={agent} onChange={(e) => setAgent(e.target.value)} className="w-56">{agents?.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}</Select>
            <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={oa} onChange={(e) => setOa(e.target.checked)} /> Also search OpenAlex</label>
            <span className="flex-1" />
            <Button variant="primary" onClick={send} disabled={!msg || busy}>{busy ? <Spinner /> : <Send size={14} />} Send</Button>
          </div>
          <ErrorNote error={err} />
        </CardBody></Card>
      </div>
    </div>
  );
}

function IdeaExplorer({ projectId, initialIdea }: { projectId: string; initialIdea: string | null }) {
  const { data: ideas, mutate: mIdeas } = useApi<any[]>(`/research/ideas${projectId ? `?project_id=${projectId}` : ""}`);
  const [sel, setSel] = useState<string | null>(initialIdea);
  const { data: idea, mutate } = useApi<any>(sel ? `/research/ideas/${sel}` : null);
  const [statement, setStatement] = useState("");
  const [context, setContext] = useState("");
  const [useOA, setUseOA] = useState(true);
  const [job, setJob] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const explore = async () => {
    setErr(null);
    try {
      const r = await api("/research/ideas", { body: { statement, context, project_id: projectId || null, use_openalex: useOA } });
      setSel(r.idea.id);
      setJob(r.job_id);
      mIdeas();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4">
        <Card><CardHeader title="Describe the idea" description="The agents will interrogate it before anything is written." />
          <CardBody className="space-y-2">
            <Textarea value={statement} onChange={(e) => setStatement(e.target.value)} placeholder="Your research idea, curiosity or problem" className="min-h-[120px]" />
            <Textarea value={context} onChange={(e) => setContext(e.target.value)} placeholder="Context, motivation, what you already believe (optional)" />
            <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={useOA} onChange={(e) => setUseOA(e.target.checked)} /> Search OpenAlex as well as my knowledge base</label>
            <Button variant="primary" onClick={explore} disabled={statement.length < 10}>Interrogate this idea</Button>
            <ErrorNote error={err} />
          </CardBody></Card>
        <Card><CardHeader title="Previous ideas" /><CardBody className="space-y-1 p-2">
          {ideas?.map((i) => <button key={i.id} onClick={() => { setSel(i.id); setJob(null); }} className={`block w-full rounded px-2 py-1.5 text-left text-sm ${sel === i.id ? "bg-accent-soft" : "hover:bg-surface-2"}`}>
            <span className="line-clamp-2">{i.statement}</span><span className="text-[11px] text-muted">v{i.version} · {i.status}</span></button>)}
        </CardBody></Card>
      </div>
      <div className="space-y-4 lg:col-span-2">
        <JobProgress jobId={job} onDone={() => { mutate(); mIdeas(); }} />
        {idea && <IdeaAssessment idea={idea} projectId={projectId || idea.project_id} onRevised={(id, j) => { setSel(id); setJob(j); mIdeas(); }} />}
        {!idea && !job && <Empty title="No idea selected" />}
      </div>
    </div>
  );
}

function IdeaAssessment({ idea, projectId, onRevised }: { idea: any; projectId?: string; onRevised: (id: string, job: string | null) => void }) {
  const a = idea.assessment ?? {};
  const as = a.assessment ?? {};
  const [response, setResponse] = useState("");
  const [revised, setRevised] = useState(idea.statement);
  const [msg, setMsg] = useState("");
  if (!a.assessment) return <Card><CardBody><p className="text-sm">{idea.statement}</p><p className="mt-2 text-xs text-muted">Assessment pending…</p></CardBody></Card>;
  const decideGap = async (gap: any, decision: string, rationale: string) => {
    if (!projectId) return setMsg("Select a project to record gap decisions.");
    await api(`/projects/${projectId}/approvals`, { body: { entity_type: "gap", decision, rationale, subject: `${gap.type} gap: ${gap.description}`, payload: gap } });
    setMsg(`Gap decision recorded (${decision}).`);
  };
  const addRQ = async (text: string) => {
    if (!projectId) return setMsg("Select a project first.");
    await api(`/projects/${projectId}/questions`, { body: { text, origin: "ai" } });
    setMsg("Added to the project as a proposed research question — approve it in the project.");
  };
  return (
    <div className="space-y-4">
      <AIWarning>This assessment is an AI interpretation produced by the Research Strategist, Literature Researcher and Critical Reviewer from {a.retrieval_counts?.kb ?? 0} knowledge-base passages and {a.retrieval_counts?.publications ?? 0} publication records. Limited retrieval is not evidence of novelty.</AIWarning>
      {a.retrieval_warnings?.length > 0 && <div className="text-xs text-warn">{a.retrieval_warnings.join(" · ")}</div>}
      {msg && <div className="rounded-md bg-ok-soft px-3 py-2 text-xs text-ok">{msg}</div>}
      <Card><CardHeader title="Central problem" /><CardBody className="space-y-3">
        <p className="text-[15px]">{a.frame?.central_problem}</p>
        <div className="grid gap-4 md:grid-cols-2">
          <div><div className="mb-1 text-xs font-medium text-muted">Assumptions (explicit and hidden)</div><BulletList items={a.frame?.assumptions} /></div>
          <div><div className="mb-1 text-xs font-medium text-muted">Questions for you</div><BulletList items={a.frame?.clarifying_questions} /></div>
        </div>
        <div className="flex flex-wrap gap-1">{a.frame?.disciplines?.map((d: string) => <Badge key={d}>{d}</Badge>)}</div>
      </CardBody></Card>
      <Card><CardHeader title="Related research and theoretical perspectives" /><CardBody className="space-y-3">
        <BulletList items={a.synthesis?.related_research} />
        <div><div className="mb-1 text-xs font-medium text-muted">Theoretical perspectives</div><BulletList items={a.synthesis?.theoretical_perspectives} /></div>
        <div><div className="mb-1 text-xs font-medium text-muted">Contradictory or debated findings</div><BulletList items={a.synthesis?.contradictory_or_debated_findings} /></div>
        <p className="text-xs text-muted">Coverage: {a.synthesis?.coverage_assessment}</p>
      </CardBody></Card>
      <Card><CardHeader title="Research gaps" description="Established gaps are supported by multiple sources; potential gaps need further investigation." /><CardBody className="space-y-3">
        {as.gaps?.map((g: any, i: number) => (
          <div key={i} className="rounded-md border border-border p-3">
            <div className="flex flex-wrap items-center gap-1.5"><Badge>{g.type?.replace("_", " ")}</Badge><Badge tone={g.status === "established" ? "ok" : "warn"}>{g.status}</Badge><Badge>confidence: {g.confidence}</Badge>{g.evidence?.length > 0 && <span className="text-[11px] text-accent">[{g.evidence.join(", ")}]</span>}</div>
            <p className="mt-1.5 text-sm">{g.description}</p>
            {g.what_would_confirm && <p className="mt-1 text-xs text-muted">To confirm: {g.what_would_confirm}</p>}
            <div className="mt-2"><ApprovalBar allow={["approved", "rejected", "deferred"]} onDecide={(d, r) => decideGap(g, d, r)} /></div>
          </div>
        ))}
        <div className="grid gap-4 md:grid-cols-2">
          <div><div className="text-xs font-medium text-muted">Originality</div><p className="text-sm">{as.originality_assessment}</p></div>
          <div><div className="text-xs font-medium text-muted">Relevance</div><p className="text-sm">{as.relevance_assessment}</p></div>
          <div><div className="text-xs font-medium text-muted">Possible contributions</div><BulletList items={as.possible_contributions} /></div>
          <div><div className="text-xs font-medium text-muted">Suggested refinements</div><BulletList items={as.suggested_refinements} /></div>
        </div>
      </CardBody></Card>
      <Card><CardHeader title="Candidate research questions" description="Proposals only. Add to the project, then approve them yourself." /><CardBody className="space-y-1.5">
        {as.candidate_research_questions?.map((q: string, i: number) => <div key={i} className="flex items-start justify-between gap-2 text-sm"><span>{q}</span><Button size="sm" variant="outline" onClick={() => addRQ(q)}>Add to project</Button></div>)}
      </CardBody></Card>
      <Card><CardHeader title="Critical Reviewer" /><CardBody className="grid gap-4 md:grid-cols-2">
        <div><div className="text-xs font-medium text-muted">Weaknesses</div><BulletList items={a.critical_review?.weaknesses} /></div>
        <div><div className="text-xs font-medium text-muted">Competing explanations</div><BulletList items={a.critical_review?.competing_explanations} /></div>
        <div><div className="text-xs font-medium text-muted">Overstated claims</div><BulletList items={a.critical_review?.overstated_claims} /></div>
        <div><div className="text-xs font-medium text-muted">Search limitations</div><BulletList items={a.critical_review?.search_limitations} /></div>
        <p className="md:col-span-2 text-sm">{a.critical_review?.recommendation_for_researcher}</p>
      </CardBody></Card>
      <Card><CardBody><SourceList sources={a.sources} /></CardBody></Card>
      <Card><CardHeader title="Challenge or refine this assessment" description="Your response and revised direction create a new version; the history is preserved." /><CardBody className="space-y-2">
        <Field label="Your response to the assessment"><Textarea value={response} onChange={(e) => setResponse(e.target.value)} placeholder="Where is the assessment wrong or incomplete? What perspective is missing?" /></Field>
        <Field label="Revised research direction"><Textarea value={revised} onChange={(e) => setRevised(e.target.value)} /></Field>
        <Button variant="primary" onClick={async () => { const r = await api(`/research/ideas/${idea.id}/revise`, { body: { statement: revised, researcher_response: response, reassess: true } }); onRevised(r.idea.id, r.job_id); }}>Re-assess revised idea</Button>
        <div className="text-xs text-muted">Version {idea.version} · <StatusBadge status={idea.status} /></div>
      </CardBody></Card>
    </div>
  );
}

function Challenge({ projectId }: { projectId: string }) {
  const [arg, setArg] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const [res, setRes] = useState<any>(null);
  const [oa, setOa] = useState(true);
  const SECTIONS: [string, string][] = [["contradictory_evidence", "Contradictory evidence"], ["competing_theoretical_explanations", "Competing theoretical explanations"],
    ["causal_assumption_challenges", "Causal assumptions challenged"], ["research_design_weaknesses", "Research design weaknesses"], ["alternative_interpretations", "Alternative interpretations"],
    ["unsupported_conclusions", "Unsupported conclusions"], ["methodological_limitations", "Methodological limitations"], ["originality_questions", "Originality questions"], ["confirmation_bias_risks", "Confirmation-bias risks"]];
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="h-fit"><CardHeader title="Argument to challenge" description="The Critical Reviewer searches for contradictory evidence and competing explanations. It is designed not to validate you." />
        <CardBody className="space-y-2">
          <Textarea value={arg} onChange={(e) => setArg(e.target.value)} className="min-h-[200px]" placeholder="Paste an argument, a section of a paper, or a set of claims." />
          <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={oa} onChange={(e) => setOa(e.target.checked)} /> Search OpenAlex for counter-evidence</label>
          <Button variant="primary" disabled={arg.length < 10} onClick={async () => { setRes(null); setJob((await api("/research/challenge", { body: { argument: arg, project_id: projectId || null, use_openalex: oa } })).job_id); }}>Challenge</Button>
          <JobProgress jobId={job} onDone={(j) => j.status === "succeeded" && setRes(j.result)} />
        </CardBody></Card>
      <div className="space-y-4 lg:col-span-2">
        {res ? <>
          <AIWarning />
          <Card><CardHeader title="Overall assessment" /><CardBody className="space-y-3"><Prose text={res.challenge.overall_assessment ?? ""} />
            <div><div className="text-xs font-medium text-muted">Questions for you</div><BulletList items={res.challenge.questions_for_the_researcher} /></div></CardBody></Card>
          <div className="grid gap-4 md:grid-cols-2">{SECTIONS.map(([k, l]) => (
            <Card key={k}><CardHeader title={l} /><CardBody><BulletList items={res.challenge[k]} empty="None identified." /></CardBody></Card>))}</div>
          <Card><CardBody><SourceList sources={res.sources} /></CardBody></Card>
        </> : <Empty title="No challenge yet" />}
      </div>
    </div>
  );
}

export default function WorkspacePage() {
  return <Suspense><WorkspaceInner /></Suspense>;
}
