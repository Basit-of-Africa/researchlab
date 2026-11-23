# Free-tier limitations and fallbacks

Free tiers change without notice. The figures below are indicative as of this writing; **verify each
provider's current terms** before relying on them. Nothing in ResearchLab purchases or upgrades a service.

| Service | Typical free-tier constraints | Impact on ResearchLab | Fallback / mitigation |
|---|---|---|---|
| **Render** (backend) | ~512 MB RAM, shared CPU; instance sleeps after inactivity (cold start of tens of seconds); ephemeral disk; monthly instance-hour allowance | First request after idle is slow; jobs pause while asleep; local files are lost on restart | Durable PostgreSQL job queue resumes work on wake; files go to Supabase; any Docker/Python host (Fly.io, Railway, a VPS, your own machine) works with the same start command |
| **Neon** (PostgreSQL) | Small storage quota per project (~0.5 GB); compute auto-suspends; limited compute hours and history | Large libraries of full-text PDFs consume storage via `document_chunks.text` and 768-d embeddings (~3 KB/chunk) | Prefer abstracts/metadata for peripheral literature; permanently delete unneeded documents; any PostgreSQL 16 + pgvector works (Supabase Postgres, local Docker) |
| **Supabase Storage** | ~1 GB storage; per-file size cap; projects pause after a period of inactivity | Original PDFs/datasets count against storage | Keep `MAX_UPLOAD_MB` modest; `STORAGE_BACKEND=local` with a persistent disk is an alternative |
| **Vercel Hobby** | Personal/non-commercial use; request/response size limits on proxied requests | Very large uploads may be rejected at the proxy | Upload large files via a locally run frontend; lower `MAX_UPLOAD_MB` |
| **Gemini API free tier** | Per-minute and per-day request/token limits that vary by model; **free-tier inputs may be used by Google to improve products** (check current terms) | A 7,000-word manuscript uses roughly 20–40 model calls (outline, ~10 sections/subsections, consistency, ~25 alignment checks, review). Quotas can interrupt it | Jobs requeue on quota errors (5-min backoff) and resume without redoing completed steps; configure a fallback (`LLM_PROVIDERS=gemini,openai_compat` or `ollama`); private material is withheld from external providers by default |
| **Ollama** (local) | Free, but requires your hardware; quality and speed depend on the model | Not reachable from a cloud backend by default | Use for local, privacy-sensitive work |
| **OpenAlex** | Free; polite-pool access with a contact email; daily request limits; terms evolve (API keys may be required for higher volume) | Literature search, citation graph, idea/theory retrieval | Set `SCHOLARLY_CONTACT_EMAIL` and, if issued, `OPENALEX_API_KEY`; Crossref and the local library remain available |
| **Crossref** | Free REST API; polite pool with contact email; rate limits under load | DOI verification and metadata | Verification status becomes `unverifiable` (not `verified`) when unreachable; retry later |
| **Semantic Scholar** | Unauthenticated access shares a small global rate limit — **HTTP 429 was observed during development** | Search may fail intermittently without a key | Request a free API key (`SEMANTIC_SCHOLAR_API_KEY`); errors are shown per source without blocking other sources |
| **CORE** | Free API key required | Disabled until configured | Set `CORE_API_KEY`; otherwise OpenAlex OA links are used |

## Behaviour when a service is unavailable

- **No AI provider:** every AI feature returns HTTP 503 with a message naming exactly what is missing
  (for example `gemini: GEMINI_API_KEY is not set | ollama: Ollama not reachable at …`). The knowledge
  base, search, discovery, verification, editing, export and statistics are unaffected.
- **AI quota exhausted:** the router tries the next configured provider. Background jobs are re-queued
  with backoff and resume from the last completed step.
- **Embedding provider unavailable:** search returns 503 for semantic mode. The default `hashing`
  embedder has no external dependency, so it never fails.
- **Scholarly API down or rate limited:** per-source errors are shown alongside results from the other
  sources. Unreachable verifications are recorded as `unverifiable`, never as `verified`.
- **Storage misconfigured:** `/api/health` reports `storage: false` and uploads fail with a clear
  storage error.

## Paid upgrades (only with your approval)

None are required. If you outgrow the free tiers, the first upgrades worth considering are:

1. A Render paid instance, for more RAM and no sleep
2. A Neon paid plan, for storage
3. A paid model tier with data-use terms appropriate for unpublished work
