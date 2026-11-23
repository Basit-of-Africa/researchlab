"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Button, Card, ErrorNote, Field, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { data: status } = useApi<{ setup_required: boolean }>("/auth/status");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const setup = status?.setup_required;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api(setup ? "/auth/register" : "/auth/login", { body: setup ? { email, password, display_name: name } : { email, password } });
      router.replace("/");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-5">
          <div className="text-lg font-semibold tracking-tight">ResearchLab</div>
          <p className="mt-1 text-sm text-muted">
            {setup ? "First run: create the researcher account. Registration closes once this account exists." : "Sign in to your research environment."}
          </p>
        </div>
        <form onSubmit={submit} className="space-y-3">
          {setup && (
            <Field label="Your name">
              <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </Field>
          )}
          <Field label="Email">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </Field>
          <Field label="Password" hint={setup ? "(at least 12 characters)" : undefined}>
            <Input type="password" required minLength={setup ? 12 : undefined} value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete={setup ? "new-password" : "current-password"} />
          </Field>
          <ErrorNote error={err} />
          <Button variant="primary" className="w-full" disabled={busy || status === undefined}>{setup ? "Create account" : "Sign in"}</Button>
        </form>
      </Card>
    </main>
  );
}
