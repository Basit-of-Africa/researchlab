"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Tabs, Textarea } from "@/components/ui";
import { fmtDate } from "@/lib/utils";

function ProjectsInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const [status, setStatus] = useState<"active" | "archived">("active");
  const { data, mutate } = useApi<any[]>(`/projects?status=${status}`);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", discipline: "", objectives: "" });
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { setOpen(sp.get("new") === "1"); }, [sp]);

  const create = async () => {
    setErr(null);
    try {
      const p = await api("/projects", { body: { ...form, objectives: form.objectives.split("\n").map((s) => s.trim()).filter(Boolean) } });
      router.push(`/projects/${p.id}`);
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHeader title="Research projects" description="Each project is an independent workspace with its own questions, literature, decisions, analyses and manuscripts."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus size={14} /> New project</Button>} />
      <Tabs value={status} onChange={setStatus} tabs={[{ value: "active", label: "Active" }, { value: "archived", label: "Archived" }]} />
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data?.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`}>
            <Card className="h-full p-4 hover:border-accent/50">
              <div className="font-medium leading-snug">{p.title}</div>
              {p.discipline && <div className="mt-0.5 text-xs text-muted">{p.discipline}</div>}
              <p className="mt-2 line-clamp-3 text-sm text-muted">{p.description || "No description."}</p>
              <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                <Badge>{p.counts.questions} questions</Badge><Badge>{p.counts.documents} documents</Badge>
                <Badge>{p.counts.manuscripts} manuscripts</Badge><Badge>{p.counts.decisions} decisions</Badge>
              </div>
              <div className="mt-2 text-[11px] text-muted">Updated {fmtDate(p.updated_at)}</div>
            </Card>
          </Link>
        ))}
      </div>
      {data && !data.length && <div className="mt-4"><Empty title={status === "active" ? "No active projects" : "No archived projects"} /></div>}
      <Modal open={open} onClose={() => setOpen(false)} title="New research project">
        <div className="space-y-3">
          <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Discipline"><Input value={form.discipline} onChange={(e) => setForm({ ...form, discipline: e.target.value })} placeholder="e.g. Information systems; innovation management" /></Field>
          <Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Research objectives" hint="(one per line)"><Textarea value={form.objectives} onChange={(e) => setForm({ ...form, objectives: e.target.value })} /></Field>
          <ErrorNote error={err} />
          <div className="flex justify-end gap-2"><Button onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" onClick={create} disabled={form.title.length < 2}>Create</Button></div>
        </div>
      </Modal>
      <span className="hidden">{mutate.length}</span>
    </>
  );
}

export default function ProjectsPage() {
  return <Suspense><ProjectsInner /></Suspense>;
}
