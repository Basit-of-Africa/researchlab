"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Textarea } from "@/components/ui";
import { fmtDate } from "@/lib/utils";

const TYPES: Record<string, string> = { empirical_article: "Empirical article (quantitative)", qualitative_article: "Empirical article (qualitative)",
  conceptual_article: "Conceptual / theoretical article", systematic_review: "Systematic or scoping review" };

function ManuscriptsInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const { data } = useApi<any[]>("/manuscripts");
  const { data: projects } = useApi<any[]>("/projects");
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(sp.get("new") === "1"); }, [sp]);
  const [f, setF] = useState({ project_id: sp.get("project") ?? "", title: "", manuscript_type: "empirical_article", target_word_count: 7000, custom: "", citation_style: "apa7",
    mode: "guided", discipline: "", target_journal: "", topic: "", objectives: "", research_questions: "", methodological_requirements: "", special_instructions: "", count_references_in_words: false });
  const { data: structures } = useApi<any>(`/manuscripts/structures?target_word_count=${f.target_word_count || 7000}`);
  const [alloc, setAlloc] = useState<Record<string, number>>({});
  const plan = useMemo(() => (structures?.[f.manuscript_type] ?? []) as { key: string; heading: string; target_words: number }[], [structures, f.manuscript_type]);
  useEffect(() => { setAlloc(Object.fromEntries(plan.map((s) => [s.key, s.target_words]))); }, [plan]);
  const total = Object.values(alloc).reduce((a, b) => a + b, 0);
  const [err, setErr] = useState<unknown>(null);

  const create = async () => {
    setErr(null);
    try {
      const m = await api("/manuscripts", { body: {
        project_id: f.project_id, title: f.title, manuscript_type: f.manuscript_type, target_word_count: f.target_word_count, citation_style: f.citation_style, mode: f.mode,
        discipline: f.discipline, target_journal: f.target_journal, count_references_in_words: f.count_references_in_words,
        requirements: { topic: f.topic, objectives: f.objectives, research_questions: f.research_questions, methodological_requirements: f.methodological_requirements, special_instructions: f.special_instructions },
        sections: plan.map((s) => ({ key: s.key, heading: s.heading, target_words: alloc[s.key] ?? s.target_words })),
      } });
      router.push(`/manuscripts/${m.id}`);
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHeader title="Manuscripts" description="Develop complete manuscripts section by section (guided) or end-to-end from an approved research foundation (complete)."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus size={14} /> New manuscript</Button>} />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data?.map((m) => (
          <Link key={m.id} href={`/manuscripts/${m.id}`}><Card className="h-full p-4 hover:border-accent/50">
            <div className="font-medium leading-snug">{m.title}</div>
            <div className="mt-1 text-xs text-muted">{TYPES[m.manuscript_type] ?? m.manuscript_type} · {m.citation_style.toUpperCase()} · {m.mode} mode</div>
            <div className="mt-3 h-1.5 rounded bg-surface-2"><div className="h-1.5 rounded bg-accent" style={{ width: `${Math.min(100, (m.total_words / m.target_word_count) * 100)}%` }} /></div>
            <div className="mt-1 flex justify-between text-xs text-muted tabular-nums"><span>{m.total_words.toLocaleString()} / {m.target_word_count.toLocaleString()} words</span><span>{m.progress.approved}/{m.progress.sections} approved</span></div>
            <div className="mt-2 flex gap-1.5"><Badge>{m.status.replace("_", " ")}</Badge><span className="text-[11px] text-muted">updated {fmtDate(m.updated_at)}</span></div>
          </Card></Link>
        ))}
      </div>
      {data?.length === 0 && <Empty title="No manuscripts yet">A manuscript belongs to a project, so that it can draw on the project's approved questions, framework, methods and references.</Empty>}
      <Modal open={open} onClose={() => setOpen(false)} title="New manuscript" wide>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Project"><Select value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">Choose…</option>{projects?.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</Select></Field>
          <Field label="Working title"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Manuscript type"><Select value={f.manuscript_type} onChange={(e) => setF({ ...f, manuscript_type: e.target.value })}>{Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
          <Field label="Target word count">
            <div className="flex gap-2"><Select value={[3000, 5000, 7000, 10000].includes(f.target_word_count) ? String(f.target_word_count) : "custom"} onChange={(e) => e.target.value !== "custom" && setF({ ...f, target_word_count: Number(e.target.value) })}>
              {[3000, 5000, 7000, 10000].map((n) => <option key={n} value={n}>{n.toLocaleString()} words</option>)}<option value="custom">Custom</option></Select>
              <Input type="number" min={500} max={60000} value={f.target_word_count} onChange={(e) => setF({ ...f, target_word_count: Number(e.target.value) })} className="w-28" /></div>
          </Field>
          <Field label="Mode"><Select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="guided">Guided — discuss and approve each section</option><option value="complete">Complete — develop the full manuscript from the approved foundation</option></Select></Field>
          <Field label="Referencing style"><Select value={f.citation_style} onChange={(e) => setF({ ...f, citation_style: e.target.value })}>
            {Object.entries(structures?._citation_styles ?? { apa7: "APA 7th" }).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</Select></Field>
          <Field label="Discipline"><Input value={f.discipline} onChange={(e) => setF({ ...f, discipline: e.target.value })} /></Field>
          <Field label="Target journal"><Input value={f.target_journal} onChange={(e) => setF({ ...f, target_journal: e.target.value })} /></Field>
          <Field label="Research topic" className="md:col-span-2"><Textarea className="min-h-[60px]" value={f.topic} onChange={(e) => setF({ ...f, topic: e.target.value })} /></Field>
          <Field label="Research objectives"><Textarea className="min-h-[60px]" value={f.objectives} onChange={(e) => setF({ ...f, objectives: e.target.value })} /></Field>
          <Field label="Research questions"><Textarea className="min-h-[60px]" value={f.research_questions} onChange={(e) => setF({ ...f, research_questions: e.target.value })} placeholder="Leave empty to use the project's approved questions" /></Field>
          <Field label="Methodological requirements"><Textarea className="min-h-[60px]" value={f.methodological_requirements} onChange={(e) => setF({ ...f, methodological_requirements: e.target.value })} /></Field>
          <Field label="Special instructions / journal conventions"><Textarea className="min-h-[60px]" value={f.special_instructions} onChange={(e) => setF({ ...f, special_instructions: e.target.value })} /></Field>
          <div className="md:col-span-2">
            <div className="mb-1 flex items-center justify-between text-xs font-medium text-muted"><span>Section structure and word allocation (editable)</span>
              <span className={`tabular-nums ${Math.abs(total - f.target_word_count) > f.target_word_count * 0.02 ? "text-warn" : ""}`}>{total.toLocaleString()} / {f.target_word_count.toLocaleString()}</span></div>
            <div className="grid gap-1.5 sm:grid-cols-2">{plan.map((s) => (
              <div key={s.key} className="flex items-center gap-2 text-sm"><span className="flex-1 truncate">{s.heading}</span>
                <Input type="number" className="h-8 w-24" value={alloc[s.key] ?? s.target_words} onChange={(e) => setAlloc({ ...alloc, [s.key]: Number(e.target.value) })} /></div>))}</div>
            <label className="mt-2 flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={f.count_references_in_words} onChange={(e) => setF({ ...f, count_references_in_words: e.target.checked })} /> The target journal counts references towards the word limit</label>
          </div>
          <div className="md:col-span-2 flex items-center justify-between"><ErrorNote error={err} /><Button variant="primary" onClick={create} disabled={!f.project_id || f.title.length < 3}>Create manuscript</Button></div>
        </div>
      </Modal>
    </>
  );
}

export default function ManuscriptsPage() {
  return <Suspense><ManuscriptsInner /></Suspense>;
}
