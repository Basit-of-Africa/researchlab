// Thin client for the ResearchLab API. All requests go to the same origin
// (/api/* is proxied by Next.js), so the httpOnly session cookie is sent
// automatically and no credentials are handled in browser code.
import useSWR, { mutate as globalMutate, type SWRConfiguration } from "swr";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) {
    super(message);
  }
}

async function handle(res: Response) {
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = "/login";
  }
  const ct = res.headers.get("content-type") ?? "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof body === "object" && body ? body.detail ?? JSON.stringify(body) : String(body);
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail), body?.code);
  }
  return body;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const init: RequestInit = { method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"), credentials: "same-origin" };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json" };
  }
  return handle(await fetch(`/api${path}`, init));
}

export const fetcher = (path: string) => api(path);

export function useApi<T = any>(path: string | null, config?: SWRConfiguration) {
  return useSWR<T>(path, fetcher, { revalidateOnFocus: false, ...config });
}

export function refresh(prefix: string) {
  return globalMutate((key) => typeof key === "string" && key.startsWith(prefix));
}

export function download(path: string) {
  window.location.href = `/api${path}`;
}

// ---------------------------------------------------------------------------
// Domain types (subset of API responses used by the UI)
// ---------------------------------------------------------------------------
export type KnowledgeCategory = "researcher_writing" | "established_position" | "provisional_opinion" | "external_evidence" | "ai_interpretation";

export interface Job {
  id: string;
  job_type: string;
  title: string;
  status: "queued" | "running" | "succeeded" | "failed" | "paused" | "cancel_requested" | "awaiting_approval";
  progress: number;
  error: string;
  result: any;
  attempts: number;
  created_at: string;
  steps?: { key: string; label: string; status: string; error: string }[];
}

export interface Source {
  sid: string;
  label: string;
  type: string;
  title?: string;
  page?: number | null;
  section?: string;
  doi?: string | null;
  document_id?: string;
  publication_id?: string;
  citekey?: string;
}

export interface Doc {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  abstract: string;
  doi: string | null;
  venue: string;
  doc_type: string;
  source_kind: string;
  visibility: string;
  knowledge_category: KnowledgeCategory;
  is_own_work: boolean;
  status: string;
  status_detail: string;
  metadata_source: Record<string, string>;
  metadata_verified: boolean;
  duplicate_of_id: string | null;
  extra: any;
  created_at: string;
  versions?: any[];
  notes?: any[];
  collections?: { id: string; name: string }[];
  chunk_count?: number;
  embedding_models?: string[];
  extracted_references?: string[];
}

export interface Publication {
  id: string | null;
  title: string;
  authors: { name: string }[];
  year: number | null;
  venue: string;
  abstract: string;
  abstract_source: string;
  doi: string | null;
  url: string;
  pub_type: string;
  is_oa: boolean | null;
  oa_url: string;
  full_text_available: boolean;
  cited_by_count: number | null;
  source_db: string;
  verification_status: string;
  concepts?: string[];
}

export interface Section {
  id: string;
  key: string;
  heading: string;
  position: number;
  target_words: number;
  objective: string;
  outline: { heading: string; points: string[]; target_words: number }[];
  content: string;
  word_count: number;
  status: string;
  origin: string;
  outstanding_requirements: string[];
  evidence_used: { citekey: string; label: string }[];
  approved_at: string | null;
  content_version: number;
}
