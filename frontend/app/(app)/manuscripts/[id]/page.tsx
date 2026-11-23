"use client";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Eye, FileCheck2, History, Pencil, Play, Quote, Save, Sparkles, Wand2 } from "lucide-react";
import { api, download, useApi, type Section } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorNote, Input, Modal, Spinner, Tabs, Textarea } from "@/components/ui";
import { AIWarning, ApprovalBar, BulletList, JobProgress, StatusBadge, VerificationBadge } from "@/components/research";
import { cn, fmtDateTime } from "@/lib/utils";

function countWords(t: string) {
  const s = t.replace(/\[@[^\]]+\]/g, " ").replace(/\[(DATA REQUIRED|CITATION NEEDED|EVIDENCE REQUIRED)[^\]]*\]/g, " ").replace(/^#+\s.*$/gm, " ").replace(/[*_`>|#-]/g, " ");
  return (s.match(/\b[\w'’-]+\b/g) ?? []).length;
}

type Panel = "plan" | "refs" | "evidence" | "quality" | "versions";

export default function ManuscriptEditor() {
  const { id } = useParams<{ id: string }>();
  const { data: m, mutate } = useApi<any>(`/manuscripts/${id}`);
  const { data: refs } = useApi<any[]>(m ? `/literature/references?project_id=${m.project_id}` : null);
  const [key, setKey] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState(false);
  const [panel, setPanel] = useState<Panel>("plan");
  const [job, setJob] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [warn, setWarn] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const sections: Section[] = m?.sections ?? [];
  const sec = sections.find((s) => s.key === key) ?? sections[0];

  useEffect(() => {
    if (sec && !dirty) setDraft(sec.content ?? "");
  }, [sec?.key, sec?.content_version, sec?.content]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const active = m?.jobs?.find((j: any) => ["queued", "running", "paused", "cancel_requested", "awaiting_approval"].includes(j.status));
    if (active && !job) setJob(active.id);
  }, [m?.jobs]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback(async () => {
    if (!sec || !dirty) return;
    setErr(null);
    try {
      await api(`/manuscripts/${id}/sections/${sec.key}`, { method: "PUT", body: { content: draft } });
      setDirty(false);
      mutate();
    } catch (e) {
      setErr(e);
    }
  }, [sec, dirty, draft, id, mutate]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [save]);

  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    setWarn("");
    setBusy(true);
    try {
      const r = await fn();
      if (r?.job_id) setJob(r.job_id);
      if (r?.warning) setWarn(r.warning);
      mutate();
      return r;
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const switchTo = async (k: string) => {
    if (dirty && !confirm("Discard unsaved changes in this section?")) return;
    setDirty(false);
    setKey(k);
    const s = sections.find((x) => x.key === k);
    setDraft(s?.content ?? "");
  };

  const insertCitation = (citekey: string, locator = "") => {
    const el = ta.current;
    const marker = `[@${citekey}${locator ? `, ${locator}` : ""}]`;
    if (!el) return setDraft((d) => d + " " + marker);
    const { selectionStart: a, selectionEnd: b } = el;
    const next = draft.slice(0, a) + marker + draft.slice(b);
    setDraft(next);
    setDirty(true);
    setPreview(false);
    requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = a + marker.length; });
  };

  if (!m) return <div className="text-muted"><Spinner /></div>;
  const words = countWords(draft);
  const total = sections.filter((s) => s.key !== "abstract").reduce((a, s) => a + (s.key === sec?.key ? words : s.word_count), 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{m.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <Badge>{m.manuscript_type.replace("_", " ")}</Badge><Badge>{m.citation_style.toUpperCase()}</Badge><Badge>{m.mode} mode</Badge><Badge>{m.status.replace("_", " ")}</Badge>
            <span className="tabular-nums">{total.toLocaleString()} / {m.target_word_count.toLocaleString()} words (excl. abstract{m.count_references_in_words ? "" : " and references"})</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api(`/manuscripts/${id}/outline`, { method: "POST" }))}><Wand2 size={13} /> Outline</Button>
          <Button size="sm" variant="primary" disabled={busy} onClick={() => run(() => api(`/manuscripts/${id}/generate`, { body: { pause_after_outline: m.mode === "guided" } }))}><Sparkles size={13} /> Generate complete manuscript</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api(`/manuscripts/${id}/audit`, { method: "POST" }))}><FileCheck2 size={13} /> Citation audit</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api(`/manuscripts/${id}/review`, { method: "POST" }))}>Critical review</Button>
          <Button size="sm" variant="outline" onClick={() => run(() => api(`/manuscripts/${id}/versions?label=${encodeURIComponent("Researcher snapshot " + new Date().toLocaleString())}`, { method: "POST" }))}><History size={13} /> Snapshot</Button>
          <Button size="sm" onClick={() => download(`/manuscripts/${id}/export/docx`)}><Download size={13} /> DOCX</Button>
          <Button size="sm" variant="ghost" onClick={() => download(`/manuscripts/${id}/export/tex`)}>LaTeX</Button>
          <Button size="sm" variant="ghost" onClick={() => download(`/manuscripts/${id}/export/md`)}>MD</Button>
        </div>
      </div>
      <JobProgress jobId={job} onDone={() => mutate()} />
      {warn && <div className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn">{warn}</div>}
      <ErrorNote error={err} />

      <div className="grid gap-4 lg:grid-cols-[210px_minmax(0,1fr)_360px]">
        {/* Section navigation */}
        <Card className="h-fit lg:sticky lg:top-4">
          <CardBody className="space-y-0.5 p-2">
            {sections.map((s) => {
              const w = s.key === sec?.key ? words : s.word_count;
              const pct = s.target_words ? w / s.target_words : 0;
              return (
                <button key={s.key} onClick={() => switchTo(s.key)} className={cn("block w-full rounded px-2 py-1.5 text-left", s.key === sec?.key ? "bg-accent-soft" : "hover:bg-surface-2")}>
                  <div className="flex items-center justify-between gap-1 text-sm"><span className="truncate">{s.heading}</span>
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", s.status === "approved" ? "bg-ok" : s.status === "draft" ? "bg-accent" : s.status === "generating" ? "bg-warn animate-pulse" : s.status === "needs_revision" ? "bg-danger" : "bg-border")} title={s.status} /></div>
                  <div className="mt-1 h-1 rounded bg-surface-2"><div className={cn("h-1 rounded", pct > 1.15 ? "bg-warn" : "bg-accent/70")} style={{ width: `${Math.min(100, pct * 100)}%` }} /></div>
                  <div className="mt-0.5 text-[11px] tabular-nums text-muted">{w} / {s.target_words}</div>
                </button>
              );
            })}
          </CardBody>
        </Card>

        {/* Editor */}
        {sec ? (
          <Card className="min-w-0">
            <CardHeader title={sec.heading} description={<span className="flex flex-wrap items-center gap-1.5"><StatusBadge status={sec.status} /><Badge>{sec.origin === "ai" ? "AI draft" : sec.origin === "mixed" ? "AI + researcher" : "researcher"}</Badge>
              <span className="tabular-nums">{words} words · target {sec.target_words}</span>{dirty && <Badge tone="warn">unsaved</Badge>}</span>}
              actions={<>
                <Button size="sm" variant="ghost" onClick={() => setPreview(!preview)}>{preview ? <><Pencil size={13} /> Edit</> : <><Eye size={13} /> Preview</>}</Button>
                <Button size="sm" variant="primary" disabled={!dirty} onClick={save}><Save size={13} /> Save</Button>
              </>} />
            <CardBody className="space-y-3">
              {sec.status === "generating" && <div className="flex items-center gap-2 text-xs text-warn"><Spinner /> This section is being generated…</div>}
              {preview ? <RenderedSection text={draft} refs={refs ?? []} /> : (
                <Textarea ref={ta} value={draft} onChange={(e) => { setDraft(e.target.value); setDirty(true); }}
                  className="prose-academic min-h-[60vh] resize-y border-none p-0 text-[15px] focus:ring-0" placeholder="Write this section, or generate a draft from the outline." />
              )}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                <ApprovalBar allow={["approved", "revised", "instruction", "regenerate"]} busy={busy}
                  onDecide={(d, r) => run(async () => { if (dirty) await save(); return api(`/manuscripts/${id}/sections/${sec.key}/decision`, { body: { decision: d, rationale: r, instructions: r } }); })} />
                <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => api(`/manuscripts/${id}/sections/${sec.key}/generate`, { method: "POST" }))}><Play size={12} /> {sec.content ? "Regenerate draft" : "Generate draft"}</Button>
              </div>
              <p className="text-[11px] text-muted">Citations use markers such as [@citekey] or [@citekey, p. 12]; they are rendered in {m.citation_style.toUpperCase()} on export. Editing an approved section returns it to draft. Ctrl/⌘+S saves.</p>
            </CardBody>
          </Card>
        ) : <Empty title="No sections" />}

        {/* Side panel */}
        <Card className="h-fit lg:sticky lg:top-4">
          <div className="px-2 pt-1"><Tabs value={panel} onChange={setPanel} tabs={[{ value: "plan", label: "Plan" }, { value: "refs", label: "Cite" }, { value: "evidence", label: "Evidence" }, { value: "quality", label: "Quality" }, { value: "versions", label: "Versions" }]} /></div>
          <CardBody className="max-h-[75vh] overflow-y-auto">
            {panel === "plan" && sec && <PlanPanel m={m} sec={sec} onSaved={mutate} />}
            {panel === "refs" && <RefsPanel refs={refs ?? []} onInsert={insertCitation} />}
            {panel === "evidence" && sec && <EvidencePanel m={m} sec={sec} />}
            {panel === "quality" && <QualityPanel m={m} />}
            {panel === "versions" && <VersionsPanel id={id} onRestored={() => { setDirty(false); mutate(); }} />}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function RenderedSection({ text, refs }: { text: string; refs: any[] }) {
  const keys = useMemo(() => new Map(refs.map((r) => [r.citekey, r])), [refs]);
  const render = (para: string) =>
    para.split(/(\[@[^\]]+\]|\[(?:DATA REQUIRED|CITATION NEEDED|EVIDENCE REQUIRED)[^\]]*\]|\*\*[^*]+\*\*|\*[^*]+\*)/g).map((p, i) => {
      if (p.startsWith("[@")) {
        const ks = [...p.matchAll(/@([\w:\-]+)/g)].map((x) => x[1]);
        const bad = ks.filter((k) => !keys.has(k));
        return <span key={i} className={cn("rounded px-0.5 font-sans text-[12px]", bad.length ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent")}
          title={ks.map((k) => keys.get(k)?.formatted ?? `${k}: not in project references`).join("\n")}>{p}</span>;
      }
      if (p.startsWith("[")) return <mark key={i} className="flag">{p}</mark>;
      if (p.startsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
      if (p.startsWith("*") && p.length > 2) return <i key={i}>{p.slice(1, -1)}</i>;
      return p;
    });
  return (
    <div className="prose-academic min-h-[60vh]">
      {text.split(/\n{2,}/).map((b, i) => (/^#{2,4}\s/.test(b) ? <h2 key={i}>{b.replace(/^#+\s/, "")}</h2> : <p key={i}>{render(b)}</p>))}
    </div>
  );
}

function PlanPanel({ m, sec, onSaved }: { m: any; sec: Section; onSaved: () => void }) {
  const [obj, setObj] = useState(sec.objective);
  const [target, setTarget] = useState(sec.target_words);
  useEffect(() => { setObj(sec.objective); setTarget(sec.target_words); }, [sec.key, sec.objective, sec.target_words]);
  return (
    <div className="space-y-3 text-sm">
      {m.quality_report?.central_argument && <div><div className="text-xs font-medium text-muted">Central argument</div><p>{m.quality_report.central_argument}</p></div>}
      <div><div className="text-xs font-medium text-muted">Section objective</div><Textarea className="min-h-[70px]" value={obj} onChange={(e) => setObj(e.target.value)} /></div>
      <div className="flex items-center gap-2"><span className="text-xs text-muted">Target words</span><Input type="number" className="h-8 w-24" value={target} onChange={(e) => setTarget(Number(e.target.value))} />
        <Button size="sm" onClick={async () => { await api(`/manuscripts/${m.id}/sections/${sec.key}`, { method: "PUT", body: { objective: obj, target_words: target } }); onSaved(); }}>Update</Button></div>
      <div><div className="text-xs font-medium text-muted">Outline</div>
        {sec.outline?.length ? <ol className="mt-1 space-y-1.5">{sec.outline.map((o, i) => <li key={i}><div className="font-medium">{o.heading} <span className="text-xs font-normal text-muted">· {o.target_words}w</span></div><BulletList items={o.points} /></li>)}</ol>
          : <p className="text-xs text-muted">No outline yet — run “Outline”.</p>}</div>
      {m.quality_report?.key_terms?.length > 0 && <div><div className="text-xs font-medium text-muted">Key terms</div>
        <ul className="text-xs">{m.quality_report.key_terms.map((t: any, i: number) => <li key={i}><b>{t.term}</b>: {t.definition}</li>)}</ul></div>}
    </div>
  );
}

function RefsPanel({ refs, onInsert }: { refs: any[]; onInsert: (k: string, loc?: string) => void }) {
  const [q, setQ] = useState("");
  const [loc, setLoc] = useState("");
  const list = refs.filter((r) => !q || (r.citekey + r.formatted).toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="space-y-2">
      <div className="flex gap-1"><Input className="h-8 text-xs" placeholder="Filter references" value={q} onChange={(e) => setQ(e.target.value)} /><Input className="h-8 w-20 text-xs" placeholder="p. 12" value={loc} onChange={(e) => setLoc(e.target.value)} /></div>
      {list.length === 0 && <p className="text-xs text-muted">No project references. Add them from Literature Discovery or the project's References tab.</p>}
      {list.map((r) => (
        <button key={r.id} onClick={() => onInsert(r.citekey, loc)} className="block w-full rounded border border-border p-2 text-left text-xs hover:bg-surface-2">
          <div className="flex items-center justify-between gap-1"><span className="font-mono">@{r.citekey}</span><VerificationBadge status={r.verification_status} /></div>
          <div className="mt-1 text-muted">{r.formatted.replaceAll("*", "")}</div>
          <div className="mt-1 flex items-center gap-1 text-accent"><Quote size={11} /> Insert at cursor</div>
        </button>
      ))}
    </div>
  );
}

function EvidencePanel({ m, sec }: { m: any; sec: Section }) {
  const { data: cites } = useApi<any[]>(`/manuscripts/${m.id}/citations`);
  const mine = cites?.filter((c) => c.section === sec.key) ?? [];
  return (
    <div className="space-y-3 text-sm">
      {sec.outstanding_requirements?.length > 0 && <div className="rounded-md bg-warn-soft p-2 text-xs text-warn"><div className="font-medium">Outstanding requirements</div><BulletList items={sec.outstanding_requirements} /></div>}
      <div><div className="text-xs font-medium text-muted">Evidence supplied to the writer</div>
        {sec.evidence_used?.length ? <ul className="mt-1 space-y-1 text-xs">{sec.evidence_used.map((e) => <li key={e.citekey}><span className="font-mono">@{e.citekey}</span> — {e.label.split("—").slice(2).join("—")}</li>)}</ul> : <p className="text-xs text-muted">—</p>}</div>
      <div><div className="text-xs font-medium text-muted">Citations in this section ({mine.length})</div>
        <ul className="mt-1 space-y-2">{mine.map((c) => (
          <li key={c.id} className="rounded border border-border p-2 text-xs">
            <div className="flex items-center justify-between gap-1"><span className="font-mono">@{c.citekey}{c.locator && `, ${c.locator}`}</span>
              <Badge tone={c.alignment_status === "supported" ? "ok" : c.alignment_status === "not_supported" || c.alignment_status === "unresolved" ? "danger" : c.alignment_status === "partially_supported" ? "warn" : "neutral"}>{c.alignment_status.replace("_", " ")}</Badge></div>
            <p className="mt-1 text-muted">“{c.claim.slice(0, 220)}”</p>
            {c.alignment_note && <p className="mt-1">{c.alignment_note}</p>}
            {c.supporting_evidence?.quote && <p className="mt-1 italic">Source: “{c.supporting_evidence.quote}”</p>}
          </li>))}</ul></div>
    </div>
  );
}

function QualityPanel({ m }: { m: any }) {
  const qr = m.quality_report ?? {};
  const audit = qr.citation_audit;
  const wa = qr.word_allocation;
  const rev = qr.critical_review;
  if (!audit && !wa && !rev && !qr.consistency) return <p className="text-xs text-muted">Run the citation audit, critical review or complete generation to populate the quality report.</p>;
  return (
    <div className="space-y-4 text-sm">
      {audit && <div>
        <div className="flex items-center justify-between"><div className="text-xs font-medium text-muted">Citation audit</div><span className="text-[11px] text-muted">{fmtDateTime(audit.checked_at)}</span></div>
        <div className="mt-1 flex flex-wrap gap-1">{Object.entries(audit.summary ?? {}).map(([k, v]) => <Badge key={k} tone={k === "critical" || k === "high" ? "danger" : k === "medium" ? "warn" : "neutral"}>{String(v)} {k}</Badge>)}
          {!Object.keys(audit.summary ?? {}).length && <Badge tone="ok">no findings</Badge>}</div>
        <ul className="mt-2 space-y-1.5">{audit.findings.slice(0, 40).map((f: any, i: number) => (
          <li key={i} className="text-xs"><Badge tone={f.severity === "critical" || f.severity === "high" ? "danger" : f.severity === "medium" ? "warn" : "neutral"}>{f.severity}</Badge> <b>{f.type.replaceAll("_", " ")}</b>{f.citekey && <span className="font-mono"> @{f.citekey}</span>}{f.section && <span className="text-muted"> · {f.section}</span>}
            <div className="text-muted">{f.message}</div>{f.context && <div className="italic text-muted">“{String(f.context).slice(0, 140)}”</div>}</li>))}</ul>
        {audit.uncited_references?.length > 0 && <p className="mt-1 text-xs text-muted">Uncited references: {audit.uncited_references.join(", ")}</p>}
        <p className="mt-1 text-[11px] text-muted">{audit.note}</p>
      </div>}
      {wa && <div><div className="text-xs font-medium text-muted">Word allocation</div>
        <table className="mt-1 w-full text-xs tabular-nums"><tbody>{wa.sections.map((s: any) => (
          <tr key={s.key} className={s.within_tolerance ? "" : "text-warn"}><td className="py-0.5">{s.heading}</td><td className="text-right">{s.actual}</td><td className="text-right text-muted">/ {s.target}</td></tr>))}</tbody></table>
        <p className="mt-1 text-xs">Body total: {wa.total_words_excluding_abstract.toLocaleString()} / {wa.target_body_words.toLocaleString()}</p></div>}
      {qr.consistency && <div><div className="text-xs font-medium text-muted">Consistency</div><p className="text-xs">{qr.consistency.argument_progression}</p>
        <BulletList items={[...(qr.consistency.terminology_inconsistencies ?? []), ...(qr.consistency.contradictions ?? []).map((c: any) => `${c.between}: ${c.issue}`), ...(qr.consistency.repetition ?? [])]} empty="No issues reported." /></div>}
      {rev && <div className="space-y-2"><div className="text-xs font-medium text-muted">Critical review</div><AIWarning />
        <div className="flex gap-1"><Badge tone={rev.readiness === "near_ready" ? "ok" : rev.readiness === "minor_revision" ? "accent" : "warn"}>{rev.readiness?.replace("_", " ")}</Badge></div>
        <p className="text-xs">{rev.overall_assessment}</p>
        <div><div className="text-[11px] font-medium text-muted">Major concerns</div><ul className="text-xs">{rev.major_concerns?.map((c: any, i: number) => <li key={i} className="mt-1">• <b>{c.issue}</b>{c.section && ` (${c.section})`} — {c.suggestion}</li>)}</ul></div>
        <div><div className="text-[11px] font-medium text-muted">Unsupported claims</div><BulletList items={rev.unsupported_claims} /></div>
        <div><div className="text-[11px] font-medium text-muted">Questions for the author</div><BulletList items={rev.questions_for_author} /></div></div>}
    </div>
  );
}

function VersionsPanel({ id, onRestored }: { id: string; onRestored: () => void }) {
  const { data, mutate } = useApi<any[]>(`/manuscripts/${id}/versions`);
  const [cmp, setCmp] = useState<any>(null);
  return (
    <div className="space-y-2">
      {data?.length === 0 && <p className="text-xs text-muted">No versions yet. Use “Snapshot”; complete generation saves one automatically.</p>}
      {data?.map((v) => (
        <div key={v.id} className="rounded border border-border p-2 text-xs">
          <div className="flex justify-between"><b>v{v.version_no}</b><span className="text-muted">{fmtDateTime(v.created_at)}</span></div>
          <div className="text-muted">{v.label} · {v.total_words.toLocaleString()} words · {v.created_by}</div>
          <div className="mt-1 flex gap-1"><Button size="sm" variant="ghost" onClick={async () => setCmp(await api(`/manuscripts/${id}/compare?a=${v.id}&b=current`))}>Compare with current</Button>
            <Button size="sm" variant="ghost" onClick={async () => { if (confirm("Restore this version? The current text is snapshotted first.")) { await api(`/manuscripts/${id}/versions/${v.id}/restore`, { method: "POST" }); mutate(); onRestored(); } }}>Restore</Button></div>
        </div>
      ))}
      <Modal open={!!cmp} onClose={() => setCmp(null)} title="Version comparison" wide>
        {cmp && <div className="space-y-3">{cmp.sections.map((s: any) => (
          <div key={s.key}><div className="text-sm font-medium">{s.heading} {s.changed ? <Badge tone="warn">changed</Badge> : <Badge>unchanged</Badge>} {s.changed && <span className="text-xs text-muted">{s.words_a} → {s.words_b} words</span>}</div>
            {s.changed && <pre className="mt-1 max-h-64 overflow-auto rounded bg-surface-2 p-2 text-[11px] leading-snug">{s.diff.map((l: string, i: number) => (
              <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-ok" : l.startsWith("-") && !l.startsWith("---") ? "text-danger" : "text-muted"}>{l}</div>))}</pre>}
          </div>))}</div>}
      </Modal>
    </div>
  );
}
