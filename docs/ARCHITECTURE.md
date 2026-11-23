# Architecture

```
Browser ──► Next.js (Vercel)  ──/api/* rewrite──►  FastAPI (Render)  ──►  PostgreSQL + pgvector (Neon)
                                                       │  ├─ embedded job worker (thread)     ├─ object storage (Supabase / local)
                                                       │  ├─ LangGraph agent workflows        ├─ AI providers (Gemini / Ollama / OpenAI-compatible)
                                                       │  └─ analysis sandbox (subprocess)    └─ OpenAlex · Crossref · Semantic Scholar · CORE
```

The browser talks only to the Next.js origin. `/api/*` is proxied to FastAPI, so the session cookie is
first-party and httpOnly, and no API key or backend URL is ever present in client code.

## Backend layout (`backend/app`)

| Layer | Modules |
|---|---|
| HTTP | `api/*` routers. These are thin: validation, ownership checks, enqueueing jobs |
| Domain services | `services/knowledge.py` (ingestion and versioning), `search.py` (hybrid retrieval), `manuscript.py`, `citations.py`, `style.py`, `export.py`, `analysis/*`, `scholarly/*` |
| AI | `services/llm/*` (providers, router, fallback, data-policy gate), `services/agents/core.py` (agent specs, context builder, run logging), `services/agents/workflows.py` (LangGraph graphs) |
| Background | `services/jobs.py` (queue, leases, resumable steps, worker), `services/tasks.py` (job handlers) |
| Persistence | `models.py` (SQLAlchemy), `alembic/` (migrations), `services/storage.py` (object storage) |

## Durable, resumable jobs

Long operations never depend on a single HTTP request. They include document processing, idea
assessment, theory and method advice, challenge mode, evidence extraction, literature watches,
reference verification and manuscript generation.

- **Queue.** Jobs live in `generation_jobs`. A worker claims them with
  `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED)` and sets a lease (`locked_until`). If a worker
  dies, the lease expires and another worker picks the job up.
- **Steps.** Handlers wrap units of work in `ctx.step(key, label, fn)`. Each step's output is persisted.
  On resume, steps that already succeeded return their stored output instead of re-running. That is how a
  7,000-word manuscript resumes after interruption without regenerating finished sections; it is tested
  for both a researcher-initiated pause and a worker crash.
- **Control.**
  - pause: `cancel_requested` takes effect at the next step boundary
  - resume
  - retry with backoff
  - quota errors: requeued with a 5-minute delay
  - permanent errors (bad input, missing configuration): fail immediately with a clear message
  - `awaiting_approval`: e.g. pause after outline in guided complete-generation
- **Workers.**
  - embedded: a thread in the API process (`RUN_EMBEDDED_WORKER=true`), suitable for free tiers
  - standalone: `python worker.py`
  - a periodic scheduler enqueues due literature watches

## Retrieval

- **Chunking** (`chunking.py`) is section-aware, using headings detected from font metrics, numbering
  patterns or Markdown and DOCX styles. Chunks are about 1,400 characters with sentence overlap. Each one
  keeps its section path, page range and character offsets. Reference lists are not indexed as content.
- **Full text:** a generated `tsvector` column with a GIN index. Plain-language questions become
  OR-of-terms queries ranked by `ts_rank_cd`. When the researcher uses quotes or operators, the query uses
  websearch syntax.
- **Semantic:** pgvector HNSW (cosine) over 768-dimension embeddings.
  - The `hashing` provider is a dependency-free *lexical* fallback, so the system always works.
  - `sentence_transformers`, `ollama` and `gemini` provide real semantic embeddings.
  - Vectors are stored with their model name and only compared within the same model.
- **Fusion:** reciprocal rank fusion of the two rankings, after metadata filters (collection, project,
  year, type, knowledge category, own work, visibility).
- **Provenance:** every hit carries its document, authors, year, DOI, section, pages, knowledge category,
  visibility and basis (full text vs abstract only).
- **Re-indexing:** a new document version re-chunks the text but **reuses embeddings for unchanged
  chunks** (matched by content hash), so only affected records are regenerated. A metadata correction
  re-chunks nothing.

## Agents

`AGENTS` in `services/agents/core.py` defines eight roles (strategist, literature, theorist,
methodologist, analyst, writer, reviewer, auditor). They share one underlying model through the provider
router and differ in role prompt, permitted tools and output contract. Every call:

1. builds a shared, labelled context: project foundation (only researcher-approved items are presented
   as approved), notes with their knowledge category, knowledge-base passages and publication abstracts.
   Each item is numbered `[S#]` and labelled, e.g. `EXTERNAL EVIDENCE — … [abstract]` or
   `RESEARCHER NOTE (provisional opinion)`
2. applies the **data-policy gate**: if the active provider is external and the researcher has not
   allowed it, private or unpublished items are removed and the agent is told how many were withheld
3. prepends the integrity rules: cite only supplied sources, never fabricate, label interpretation,
   treat "established" gaps cautiously, never fabricate data
4. logs an `agent_runs` row (provider, model, tokens, duration, withheld-source count, errors)

The LangGraph workflows are small `StateGraph`s:

| Workflow | Sequence |
|---|---|
| Idea assessment | strategist frame → retrieval (knowledge base + OpenAlex) → literature synthesis → strategist gap assessment → reviewer critique |
| Challenge Mode | reviewer counter-queries → retrieval → reviewer challenge |
| Theory analysis | single analyst run |
| Methodology advice | single adviser run |
| Evidence extraction | single literature-researcher run |
| Workspace chat | single run |

Agent output is stored as `ai_interpretation` and creates only *proposed* entities.

## Manuscript generation (`services/manuscript.py`)

1. Allocate words from the selected structure. The default 7,000-word empirical structure follows the
   specification, and allocations are editable.
2. Outline: central argument, key terms, and per-section objectives, subsections, evidence needs and
   outstanding requirements.
3. Write each section, in subsections when it exceeds 1,100 words. Each call receives:
   - the requirements, central argument and key terms
   - a summary of previous sections
   - the approved project foundation
   - linked analysis outputs (if any)
   - the most relevant **project references**, each labelled `cite as [@key]`, with excerpts from the
     linked document or the abstract
   - the style profile

   The writer may cite only those keys, and must write `[CITATION NEEDED: …]` instead of inventing a
   source. With no analysis outputs, results/findings sections must not report findings and emit
   `[DATA REQUIRED: …]`.
4. Check consistency (reviewer), run the deterministic citation audit plus LLM claim-source alignment
   (auditor), check word allocation within ±15%, and run a critical review.
5. Assemble a `manuscript_versions` snapshot.

## Citation integrity (`services/citations.py`)

Markers follow Pandoc syntax (`[@key]`, `[@key, p. 4]`, `[@a; @b]`). The deterministic audit flags:

- nonexistent keys
- references not found in registries
- metadata mismatches
- unverified or incomplete references
- missing DOIs
- direct quotations without page locators
- writer-flagged unsupported claims
- duplicate references
- secondary sources
- hand-written author-year citations that bypass the reference list

Verification resolves DOIs via Crossref, falling back to OpenAlex, and compares title (similarity),
year and first author. Without a DOI it does a Crossref bibliographic title search. Every check is
recorded in `source_verification_records`. Formatters cover APA 7, Harvard, Chicago author-date, IEEE and
Vancouver; missing fields are omitted, never guessed. The audit report states that it is not a
plagiarism check.

## Analysis sandbox

The AI never writes executable code. Each procedure is a fixed template; parameters are validated
against the dataset schema and embedded as a JSON literal. Scripts run in a separate `python -I`
process with:

- `RLIMIT_AS` (memory), `RLIMIT_CPU` and `RLIMIT_NPROC` limits
- a wall-clock timeout
- a minimal environment and a temporary working directory

Each analysis stores its exact script, runtime versions and dataset hash, so reproduction re-runs the
script and compares output hashes. Downloaded scripts also run standalone.

## Security

| Area | Measure |
|---|---|
| Passwords | Argon2 hashing; minimum 12 characters |
| Sessions | HS256 JWT in an httpOnly, SameSite cookie, revocable via `token_version` (sign out everywhere, password change) |
| Registration | Single-researcher mode: closes after the first account |
| Rate limiting | Per IP for the whole API, stricter for login and registration |
| Uploads | Extension whitelist, magic-byte or structure checks (PDF header, DOCX zip structure, UTF-8 text), size limits |
| Data separation | Owner-scoped access on every endpoint; 404 for foreign IDs |
| HTTP headers | Security headers on API and frontend; CORS restricted to configured origins |
| Audit | Log of logins, uploads, metadata corrections, decisions, exports and deletions |
| Secrets | Server-side environment only; the settings endpoint reports availability, never values |
