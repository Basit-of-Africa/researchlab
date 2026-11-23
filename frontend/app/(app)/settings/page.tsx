"use client";
import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, ErrorNote, Field, Input, PageHeader } from "@/components/ui";

export default function SettingsPage() {
  const { data: s, mutate } = useApi<any>("/settings");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<unknown>(null);
  if (!s) return null;
  return (
    <>
      <PageHeader title="Settings" description="Credentials are configured as server-side environment variables and are never shown here or sent to the browser." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="AI providers" description="Tried in order; later providers are fallbacks when quotas are exhausted. Configure with LLM_PROVIDERS." />
          <CardBody className="space-y-2">
            {s.ai_providers.map((p: any) => (
              <div key={p.name} className="flex items-center justify-between rounded border border-border px-3 py-2 text-sm">
                <div><div className="font-medium">{p.name}</div><div className="text-xs text-muted">{p.model || "no model configured"}</div></div>
                <div className="flex gap-1.5"><Badge tone={p.external ? "warn" : "ok"}>{p.external ? "external service" : "local"}</Badge><Badge tone={p.available ? "ok" : "danger"}>{p.available ? "available" : "unavailable"}</Badge></div>
              </div>
            ))}
            {!s.ai_providers.some((p: any) => p.available) && <p className="text-xs text-danger">No provider is available. Set GEMINI_API_KEY (free tier), run Ollama locally, or configure an OpenAI-compatible endpoint. Non-AI features continue to work.</p>}
          </CardBody></Card>
        <Card><CardHeader title="Data policy for unpublished material" description="Applies whenever the active provider is an external service." />
          <CardBody className="space-y-3 text-sm">
            <div className="flex items-start gap-2"><ShieldCheck size={18} className="mt-0.5 shrink-0 text-ok" />
              <p>{s.ai_data_policy.allow_external_private
                ? "Private and unpublished documents, notes and data MAY be sent to external AI providers. Review the provider's data-use terms (free tiers may use inputs to improve their models)."
                : "Private and unpublished material is withheld from external AI providers. Agents still work with published and public sources, and tell you how many private sources were withheld. Use a local model (Ollama) to include everything."}</p></div>
            <Button variant={s.ai_data_policy.allow_external_private ? "outline" : "danger"} onClick={async () => {
              const next = !s.ai_data_policy.allow_external_private;
              if (next && !confirm("Allow sending private/unpublished research material to external AI providers? Check the provider's data policy first.")) return;
              await api("/settings/ai-data-policy", { method: "PUT", body: { allow_external_private: next } }); mutate();
            }}>{s.ai_data_policy.allow_external_private ? "Withhold private material again" : "Allow external providers to see private material"}</Button>
          </CardBody></Card>
        <Card><CardHeader title="Retrieval" /><CardBody className="space-y-1 text-sm">
          <div>Embedding provider: <b>{s.embedding.provider}</b> ({s.embedding.model}, {s.embedding.dimension} dimensions){s.embedding.external && <Badge tone="warn" className="ml-1.5">external</Badge>}</div>
          {s.embedding.note && <p className="text-xs text-warn">{s.embedding.note}</p>}
          <p className="text-xs text-muted">Changing the embedding model requires regenerating embeddings (Knowledge Centre → document → Regenerate embeddings). Full-text search is always available.</p>
        </CardBody></Card>
        <Card><CardHeader title="Scholarly sources & storage" /><CardBody className="space-y-1 text-sm">
          <div>OpenAlex, Crossref: <Badge tone="ok">enabled</Badge></div>
          <div>Semantic Scholar: {s.scholarly.semantic_scholar.api_key ? <Badge tone="ok">API key set</Badge> : <Badge tone="warn">shared rate limit (no key)</Badge>}</div>
          <div>CORE: {s.scholarly.core.configured ? <Badge tone="ok">configured</Badge> : <Badge>not configured (CORE_API_KEY)</Badge>}</div>
          <div>Polite-pool contact email: {s.scholarly.contact_email_set ? "set" : "not set"}</div>
          <div className="pt-2">File storage: <b>{s.storage.backend}</b></div>
          {s.storage.warning && <p className="text-xs text-danger">{s.storage.warning}</p>}
          <div className="text-xs text-muted">Upload limit {s.limits.max_upload_mb} MB · analysis time limit {s.limits.analysis_timeout_seconds}s</div>
        </CardBody></Card>
        <Card><CardHeader title="Account security" /><CardBody className="space-y-2">
          <Field label="Current password"><Input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password (12+ characters)"><Input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
          <div className="flex gap-2">
            <Button variant="primary" onClick={async () => { setErr(null); try { await api("/auth/password", { body: pw }); setMsg("Password changed; other sessions were signed out."); setPw({ current_password: "", new_password: "" }); } catch (e) { setErr(e); } }}>Change password</Button>
            <Button variant="outline" onClick={async () => { await api("/auth/logout-all", { method: "POST" }); location.href = "/login"; }}>Sign out everywhere</Button>
          </div>
          {msg && <p className="text-xs text-ok">{msg}</p>}<ErrorNote error={err} />
        </CardBody></Card>
      </div>
    </>
  );
}
