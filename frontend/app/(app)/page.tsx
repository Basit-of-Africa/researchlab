"use client";
import Link from "next/link";
import { BookOpen, BrainCircuit, FileText, FlaskConical, FolderPlus, Lightbulb, ShieldQuestion, Upload } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge, Card, CardBody, CardHeader, Empty, PageHeader, Stat } from "@/components/ui";
import { CategoryBadge, JobProgress } from "@/components/research";
import { fmtDateTime } from "@/lib/utils";

const ACTIONS = [
  { href: "/workspace?tab=idea", label: "Explore a research idea", icon: Lightbulb },
  { href: "/projects?new=1", label: "Start a research project", icon: FolderPlus },
  { href: "/literature", label: "Discover literature", icon: BookOpen },
  { href: "/manuscripts?new=1", label: "Develop a manuscript", icon: FileText },
  { href: "/analysis", label: "Analyse research data", icon: FlaskConical },
  { href: "/workspace?tab=challenge", label: "Review / challenge a paper", icon: ShieldQuestion },
  { href: "/knowledge?upload=1", label: "Add to the knowledge base", icon: Upload },
];

export default function Dashboard() {
  const { data } = useApi<any>("/dashboard", { refreshInterval: 10000 });
  const { data: health } = useApi<any>("/health", { refreshInterval: 30000 });
  const s = data?.stats ?? {};
  return (
    <>
      <PageHeader title="Research dashboard" description="Your research activity, knowledge base and ongoing processing at a glance." />
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Active projects" value={s.projects ?? "—"} />
        <Stat label="Knowledge-base documents" value={s.documents ?? "—"} />
        <Stat label="Publication records" value={s.publications ?? "—"} />
        <Stat label="Research notes" value={s.notes ?? "—"} />
        <Stat label="Pending literature reviews" value={s.pending_reviews ?? "—"} hint="Discovered items awaiting your decision" />
      </div>
      <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {ACTIONS.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className="flex flex-col items-start gap-2 rounded-lg border border-border bg-surface p-3 text-sm hover:border-accent/50 hover:bg-accent-soft/40">
            <Icon size={18} className="text-accent" strokeWidth={1.75} />
            <span className="leading-snug">{label}</span>
          </Link>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Active research projects" actions={<Link href="/projects" className="text-xs text-accent">All projects</Link>} />
          <CardBody className="p-0">
            {data?.projects?.length ? (
              <ul className="divide-y divide-border">
                {data.projects.map((p: any) => (
                  <li key={p.id}><Link href={`/projects/${p.id}`} className="flex items-center justify-between px-4 py-2.5 hover:bg-surface-2">
                    <span className="truncate">{p.title}</span><span className="text-xs text-muted">{fmtDateTime(p.updated_at)}</span></Link></li>
                ))}
              </ul>
            ) : <div className="p-4"><Empty title="No projects yet">Start a project to keep its questions, literature, decisions and manuscripts together.</Empty></div>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Processing status" description="Background tasks and job queue" />
          <CardBody className="space-y-2">
            {health && (
              <div className="flex flex-wrap gap-1.5 text-xs">
                <Badge tone={health.database ? "ok" : "danger"}>Database {health.database ? "online" : "offline"}</Badge>
                <Badge tone={health.pgvector ? "ok" : "danger"}>pgvector {health.pgvector ?? "missing"}</Badge>
                <Badge tone={health.storage ? "ok" : "danger"}>Storage {health.storage ? "ok" : "error"}</Badge>
              </div>
            )}
            {data?.jobs?.length ? data.jobs.slice(0, 6).map((j: any) => <JobProgress key={j.id} jobId={j.id} compact />) : <p className="text-sm text-muted">No ongoing tasks.</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recently accessed manuscripts" actions={<Link href="/manuscripts" className="text-xs text-accent">All</Link>} />
          <CardBody className="p-0">
            {data?.manuscripts?.length ? (
              <ul className="divide-y divide-border">
                {data.manuscripts.map((m: any) => (
                  <li key={m.id}><Link href={`/manuscripts/${m.id}`} className="block px-4 py-2.5 hover:bg-surface-2">
                    <div className="truncate">{m.title}</div>
                    <div className="text-xs text-muted tabular-nums">{m.words.toLocaleString()} / {m.target.toLocaleString()} words · {m.status.replace("_", " ")}</div>
                  </Link></li>
                ))}
              </ul>
            ) : <p className="p-4 text-sm text-muted">No manuscripts yet.</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recently added to the knowledge base" actions={<Link href="/knowledge" className="text-xs text-accent">Knowledge Centre</Link>} />
          <CardBody className="p-0">
            {data?.recent_documents?.length ? (
              <ul className="divide-y divide-border">
                {data.recent_documents.map((d: any) => (
                  <li key={d.id}><Link href={`/knowledge/${d.id}`} className="block px-4 py-2.5 hover:bg-surface-2">
                    <div className="truncate">{d.title}</div>
                    <div className="mt-0.5 flex gap-1.5"><CategoryBadge category={d.knowledge_category} />{d.status !== "ready" && <Badge tone={d.status === "failed" ? "danger" : "warn"}>{d.status}</Badge>}</div>
                  </Link></li>
                ))}
              </ul>
            ) : <p className="p-4 text-sm text-muted">Nothing added yet.</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent research notes" />
          <CardBody className="p-0">
            {data?.recent_notes?.length ? (
              <ul className="divide-y divide-border">
                {data.recent_notes.map((n: any) => (
                  <li key={n.id} className="px-4 py-2.5"><div className="truncate text-sm">{n.title}</div><div className="mt-0.5"><CategoryBadge category={n.knowledge_category} /></div></li>
                ))}
              </ul>
            ) : <p className="p-4 text-sm text-muted">No notes yet.</p>}
          </CardBody>
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader title="Research activity history" />
          <CardBody>
            {data?.activity?.length ? (
              <ul className="grid gap-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
                {data.activity.map((a: any, i: number) => (
                  <li key={i} className="flex justify-between gap-2"><span className="truncate">{a.action.replaceAll(/[._]/g, " ")}</span><span className="shrink-0 text-muted">{fmtDateTime(a.at)}</span></li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">No activity recorded.</p>}
          </CardBody>
        </Card>
      </div>
      <p className="mt-6 flex items-center gap-1.5 text-xs text-muted"><BrainCircuit size={12} /> ResearchLab assists; you retain intellectual ownership and final authority over every research decision.</p>
    </>
  );
}
