"use client";
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, ErrorNote, Field, Input, PageHeader, Select, Tabs, Textarea } from "@/components/ui";
import { AIWarning, JobProgress } from "@/components/research";
import { fmtDateTime } from "@/lib/utils";

type Tab = "profile" | "interests" | "writing";
const list = (s: string) => s.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);

export default function ProfilePage() {
  const [tab, setTab] = useState<Tab>("profile");
  return (
    <>
      <PageHeader title="Researcher profile" description="Your academic identity, built only from what you supply. ResearchLab never invents qualifications, publications or positions." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "profile", label: "Academic profile" }, { value: "interests", label: "Research interests" }, { value: "writing", label: "Writing-style profile" }]} />
      <div className="mt-4">{tab === "profile" ? <Profile /> : tab === "interests" ? <Interests /> : <Writing />}</div>
    </>
  );
}

function Profile() {
  const { data, mutate } = useApi<any>("/profile");
  const [f, setF] = useState<any>(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (data) {
      const p = data.profile;
      setF({ ...p, disciplines: p.disciplines.join(", "), methodological_experience: p.methodological_experience.join(", "), theoretical_preferences: p.theoretical_preferences.join(", "),
        qualifications: p.qualifications.map((q: any) => [q.degree, q.field, q.institution, q.year].filter(Boolean).join(" | ")).join("\n") });
    }
  }, [data]);
  if (!f) return null;
  const save = async () => {
    setErr(null);
    try {
      await api("/profile", { method: "PUT", body: { ...f, disciplines: list(f.disciplines), methodological_experience: list(f.methodological_experience), theoretical_preferences: list(f.theoretical_preferences),
        qualifications: f.qualifications.split("\n").filter((l: string) => l.trim()).map((l: string) => { const [degree, field, institution, year] = l.split("|").map((x) => x.trim()); return { degree, field, institution, year }; }) } });
      setSaved(true);
      mutate();
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <Card><CardBody className="grid gap-3 md:grid-cols-2">
      <Field label="Full name"><Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label="Title / position"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
      <Field label="Affiliation"><Input value={f.affiliation} onChange={(e) => setF({ ...f, affiliation: e.target.value })} /></Field>
      <Field label="ORCID"><Input value={f.orcid} onChange={(e) => setF({ ...f, orcid: e.target.value })} /></Field>
      <Field label="Qualifications" hint="(one per line: degree | field | institution | year)" className="md:col-span-2"><Textarea value={f.qualifications} onChange={(e) => setF({ ...f, qualifications: e.target.value })} /></Field>
      <Field label="Preferred disciplines" hint="(comma-separated)"><Input value={f.disciplines} onChange={(e) => setF({ ...f, disciplines: e.target.value })} /></Field>
      <Field label="Methodological experience"><Input value={f.methodological_experience} onChange={(e) => setF({ ...f, methodological_experience: e.target.value })} placeholder="e.g. PLS-SEM, case studies, thematic analysis" /></Field>
      <Field label="Theoretical familiarity" hint="(informs, never restricts, recommendations)"><Input value={f.theoretical_preferences} onChange={(e) => setF({ ...f, theoretical_preferences: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Referencing style"><Select value={f.referencing_style} onChange={(e) => setF({ ...f, referencing_style: e.target.value })}>
          <option value="apa7">APA 7</option><option value="harvard">Harvard</option><option value="chicago_ad">Chicago (author-date)</option><option value="ieee">IEEE</option><option value="vancouver">Vancouver</option></Select></Field>
        <Field label="English convention"><Select value={f.language_variant} onChange={(e) => setF({ ...f, language_variant: e.target.value })}><option value="en-GB">British English</option><option value="en-US">American English</option></Select></Field>
      </div>
      <Field label="Writing conventions" className="md:col-span-2"><Textarea value={f.writing_conventions} onChange={(e) => setF({ ...f, writing_conventions: e.target.value })} placeholder="e.g. avoid first person; Oxford comma; define abbreviations at first use" /></Field>
      <Field label="Research history summary" className="md:col-span-2"><Textarea value={f.research_history_summary} onChange={(e) => setF({ ...f, research_history_summary: e.target.value })} /></Field>
      <Field label="Biography" className="md:col-span-2"><Textarea value={f.bio} onChange={(e) => setF({ ...f, bio: e.target.value })} /></Field>
      <div className="md:col-span-2 flex items-center justify-end gap-2"><ErrorNote error={err} />{saved && <Badge tone="ok">Saved</Badge>}<Button variant="primary" onClick={save}>Save profile</Button></div>
    </CardBody></Card>
  );
}

const SUGGESTED = [
  "Institutional capacity and innovation ecosystems", "Digital trust infrastructure and organisational transformation", "Entrepreneurship, technology adoption and African enterprise performance",
  "Information systems", "Innovation management", "African development", "Digital governance", "Institutional development",
];

function Interests() {
  const { data, mutate } = useApi<any>("/profile");
  const [f, setF] = useState({ label: "", description: "", keywords: "" });
  const interests: any[] = data?.interests ?? [];
  const add = async (label: string, description = "", keywords: string[] = []) => {
    await api("/profile/interests", { body: { label, description, keywords } });
    mutate();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="h-fit"><CardHeader title="Add an interest" description="Interests guide discovery and watches. They never restrict what you can investigate." />
        <CardBody className="space-y-2">
          <Input placeholder="Interest" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
          <Textarea placeholder="Description (optional)" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} className="min-h-[60px]" />
          <Input placeholder="Keywords (comma-separated)" value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} />
          <Button variant="primary" disabled={f.label.length < 2} onClick={async () => { await add(f.label, f.description, list(f.keywords)); setF({ label: "", description: "", keywords: "" }); }}><Plus size={13} /> Add</Button>
          <div className="pt-2 text-xs text-muted">Initial research profile (configurable):</div>
          {SUGGESTED.filter((s) => !interests.some((i) => i.label === s)).map((s) => <button key={s} className="block text-left text-xs text-accent hover:underline" onClick={() => add(s)}>+ {s}</button>)}
        </CardBody></Card>
      <div className="space-y-2 lg:col-span-2">{interests.map((i) => (
        <Card key={i.id}><CardBody className="flex items-start gap-3">
          <div className="flex-1"><div className="font-medium">{i.label}</div>{i.description && <p className="text-sm text-muted">{i.description}</p>}
            <div className="mt-1 flex flex-wrap gap-1">{i.keywords.map((k: string) => <Badge key={k}>{k}</Badge>)}</div></div>
          <Button size="sm" variant="ghost" onClick={async () => { await api(`/profile/interests/${i.id}`, { method: "PUT", body: { ...i, is_active: !i.is_active } }); mutate(); }}>{i.is_active ? "Active" : "Inactive"}</Button>
          <button className="text-muted hover:text-danger" onClick={async () => { await api(`/profile/interests/${i.id}`, { method: "DELETE" }); mutate(); }}><Trash2 size={14} /></button>
        </CardBody></Card>))}</div>
    </div>
  );
}

function Writing() {
  const { data: profiles, mutate } = useApi<any[]>("/profile/writing");
  const { data: docs } = useApi<any>("/knowledge/documents?limit=200");
  const wp = profiles?.[0];
  const [job, setJob] = useState<string | null>(null);
  const [ov, setOv] = useState<Record<string, string>>({});
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { if (wp) setOv(wp.researcher_overrides ?? {}); }, [wp?.id, wp?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!wp) return null;
  const own = (docs?.items ?? []).filter((d: any) => d.is_own_work || d.knowledge_category === "researcher_writing");
  const toggle = async (id: string) => {
    const ids = wp.sample_document_ids.includes(id) ? wp.sample_document_ids.filter((x: string) => x !== id) : [...wp.sample_document_ids, id];
    await api(`/profile/writing/${wp.id}`, { method: "PUT", body: { sample_document_ids: ids } });
    mutate();
  };
  const m = wp.metrics ?? {};
  const OVERRIDES = ["voice", "sentence_style", "paragraph_structure", "hedging", "citation_integration", "preferred_terms", "avoid"];
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="h-fit"><CardHeader title="Writing samples" description="Select your own documents. Only these are analysed." />
        <CardBody className="space-y-1.5">
          {own.length === 0 && <p className="text-xs text-muted">Upload your papers in the Knowledge Centre and mark them as your own work.</p>}
          {own.map((d: any) => <label key={d.id} className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={wp.sample_document_ids.includes(d.id)} onChange={() => toggle(d.id)} /> <span>{d.title}</span></label>)}
          <Button variant="primary" className="mt-2" disabled={!wp.sample_document_ids.length} onClick={async () => { setErr(null); try { setJob((await api(`/profile/writing/${wp.id}/analyse`, { method: "POST" })).job_id); } catch (e) { setErr(e); } }}>Analyse writing style</Button>
          <ErrorNote error={err} />
          <JobProgress jobId={job} onDone={() => mutate()} />
        </CardBody></Card>
      <div className="space-y-4 lg:col-span-2">
        <Card><CardHeader title="Computed characteristics" description={`${wp.labels.metrics} · version ${wp.version} · updated ${fmtDateTime(wp.updated_at)}`} />
          <CardBody>{m.sample_words ? <div className="space-y-3">
            <ul className="list-disc space-y-1 pl-5 text-sm">{wp.metrics_description.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">{[["Sample words", m.sample_words], ["Mean sentence", m.mean_sentence_length], ["Hedges /1k", m.hedges_per_1000], ["Boosters /1k", m.boosters_per_1000],
              ["Passive /1k", m.passive_per_1000], ["Citations /1k", m.citations_per_1000], ["Type-token", m.type_token_ratio], ["Spelling", m.spelling_variant]].map(([k, v]) => (
              <div key={String(k)} className="rounded border border-border p-2"><div className="text-muted">{k}</div><div className="font-medium tabular-nums">{String(v)}</div></div>))}</div>
            {m.frequent_academic_terms?.length > 0 && <div className="text-xs"><span className="text-muted">Frequent academic vocabulary: </span>{m.frequent_academic_terms.join(", ")}</div>}
          </div> : <p className="text-sm text-muted">Not analysed yet.</p>}</CardBody></Card>
        {wp.qualitative?.summary && <Card><CardHeader title="Qualitative description" /><CardBody className="space-y-2">
          <AIWarning>{wp.labels.qualitative}. Style only — this says nothing about your intellectual positions.</AIWarning>
          <p className="text-sm">{wp.qualitative.summary}</p>
          <ul className="list-disc pl-5 text-sm">{(wp.qualitative.traits ?? []).map((t: string, i: number) => <li key={i}>{t}</li>)}</ul>
          <dl className="grid gap-2 text-sm md:grid-cols-2">{["sentence_construction", "paragraph_organisation", "vocabulary", "argumentation_patterns", "literature_integration", "transitions", "tone_formality", "discussion_patterns"]
            .filter((k) => wp.qualitative[k]).map((k) => <div key={k}><dt className="text-xs text-muted">{k.replace("_", " ")}</dt><dd>{wp.qualitative[k]}</dd></div>)}</dl>
        </CardBody></Card>}
        {wp.qualitative?.error && <ErrorNote error={wp.qualitative.error} />}
        <Card><CardHeader title="Your corrections" description={`${wp.labels.researcher_overrides}. Used verbatim when drafting.`} />
          <CardBody className="grid gap-2 md:grid-cols-2">
            {OVERRIDES.map((k) => <Field key={k} label={k.replaceAll("_", " ")}><Input value={ov[k] ?? ""} onChange={(e) => setOv({ ...ov, [k]: e.target.value })} /></Field>)}
            <div className="md:col-span-2 flex justify-end gap-2">
              <Button onClick={async () => { await api(`/profile/writing/${wp.id}`, { method: "PUT", body: { status: "reviewed" } }); mutate(); }}>Mark profile as reviewed</Button>
              <Button variant="primary" onClick={async () => { await api(`/profile/writing/${wp.id}`, { method: "PUT", body: { researcher_overrides: Object.fromEntries(Object.entries(ov).filter(([, v]) => v)) } }); mutate(); }}>Save corrections</Button>
            </div>
            <div className="text-xs text-muted">Status: {wp.status}</div>
          </CardBody></Card>
      </div>
    </div>
  );
}
