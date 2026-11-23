# Deployment (zero-cost-first)

The target topology is: **Vercel** (frontend), **Render** (FastAPI), **Neon** (PostgreSQL + pgvector),
**Supabase Storage** (original files) and the **Gemini** free tier or another provider (AI). Each piece can
be swapped. The application does not depend on any single provider's free quota. Read
[FREE_TIER_LIMITS.md](FREE_TIER_LIMITS.md) before relying on any of them.

> This deployment configuration has not been executed against live Vercel, Render, Neon or Supabase
> accounts from this repository's development environment. The same build, migration and start commands
> were run locally against PostgreSQL 16 + pgvector (see [STATUS.md](STATUS.md)).

## 1. Database: Neon

1. Create a Neon project (PostgreSQL 16) and a database named `researchlab`.
2. pgvector is available on Neon. The first migration runs `CREATE EXTENSION IF NOT EXISTS vector`.
3. Copy the connection string and change the scheme to the psycopg driver:
   `postgresql+psycopg://USER:PASSWORD@ep-xxxx.REGION.aws.neon.tech/researchlab?sslmode=require`
4. Migrations run automatically when the Render service starts (`alembic upgrade head` in
   `render.yaml`). To run them manually:
   `cd backend && DATABASE_URL=... alembic upgrade head`

## 2. File storage: Supabase Storage

Render's free filesystem is **ephemeral**, so original documents must not be stored there.

1. Create a Supabase project and, under Storage, a **private** bucket named `researchlab`.
2. Note the project URL and the **service-role** key. The key is server-side only and grants full storage
   access, so never expose it to the browser.
3. Set `STORAGE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `SUPABASE_BUCKET` on the backend.

## 3. Backend: Render

1. Push the repository to a private GitHub repository.
2. In Render, choose **New → Blueprint** and select the repository. `render.yaml` defines the
   `researchlab-api` free web service, rooted at `backend/`.
3. Fill in the variables marked `sync: false`:
   - `DATABASE_URL`
   - `CORS_ORIGINS` (your Vercel URL)
   - the `SUPABASE_*` variables
   - `GEMINI_API_KEY` and/or other provider settings

   `SECRET_KEY` is generated automatically.
4. Health check: `GET /api/health` reports database, pgvector and storage status.
5. The job worker runs inside the web process (`RUN_EMBEDDED_WORKER=true`). Jobs are durable in
   PostgreSQL, so work interrupted when a free instance sleeps resumes after it wakes. A paid
   *Background Worker* running `python worker.py` is optional, not required.

Other configuration (see `backend/.env.example` for the full list):

- **Cookies:** `COOKIE_SECURE=true`. Leave `COOKIE_SAMESITE=lax`; the browser only ever talks to the
  Vercel origin, which proxies `/api`.
- **Analysis:** free instances have 512 MB RAM, so keep `ANALYSIS_MEMORY_MB` around 384 and expect heavy
  procedures (large EFA or bootstrap mediation) to be slower.

## 4. Frontend: Vercel

1. Import the repository in Vercel and set **Root Directory** to `frontend`.
2. Set the environment variable `BACKEND_URL=https://researchlab-api.onrender.com` (no trailing slash).
   It is read server-side by `next.config.ts`, which proxies `/api/*`.
3. Deploy. Open the site; the first visit creates the researcher account, and registration then closes
   (unless `ALLOW_REGISTRATION=true`).

**Large uploads.** Requests pass through Vercel's proxy, which may impose request-size limits
on your plan. If large PDFs fail to upload through the deployed site, upload them from a locally running
frontend pointed at the same backend, or lower `MAX_UPLOAD_MB`.

## 5. AI configuration

| Option | Settings | Notes |
|---|---|---|
| Gemini (free tier) | `LLM_PROVIDERS=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL` | External service. Check whether your tier's terms allow Google to use inputs; keep the private-material data policy off unless acceptable. |
| Ollama (local, private) | `LLM_PROVIDERS=ollama` (or `gemini,ollama` for fallback), `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Runs on your own machine; it is not reachable from Render unless you expose it securely. Best for local use with unpublished material. |
| OpenAI-compatible | add `openai_compat` to `LLM_PROVIDERS`; set `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_MODEL`, `OPENAI_COMPAT_API_KEY` | Groq, OpenRouter, LM Studio, vLLM, etc. |

Embeddings are configured independently (`EMBEDDING_PROVIDER`). Changing the embedding model requires
regenerating embeddings for existing documents (Knowledge Centre → document → *Regenerate embeddings*).

## 6. Backup and recovery

- **Database:** Neon keeps point-in-time history for a limited window on the free plan. For your own
  copies, run:

  ```bash
  DATABASE_URL=... ./scripts/backup.sh ./backups
  ```

  This writes a `pg_dump` custom-format archive.
- **Restore into an empty database:**

  ```bash
  DATABASE_URL=... ./scripts/restore.sh ./backups/researchlab-<stamp>.dump
  ```

  If the code is newer than the dump, run `alembic upgrade head` afterwards. The backup/restore round
  trip was tested locally.
- **Files:** download the Supabase bucket periodically (dashboard or Supabase CLI). With local storage,
  `backup.sh` also archives the storage directory.
- **Projects:** *Export* on a project produces a ZIP of its JSON record and manuscripts in Markdown.
  Manuscripts also export to DOCX and LaTeX.

## 7. Post-deployment checklist

- [ ] `/api/health` returns `"status": "ok"` with a pgvector version and `"storage": true`
- [ ] Settings page shows the expected AI provider as *available*, and storage backend `supabase`
- [ ] Upload a PDF; it reaches `ready`, and search finds it
- [ ] `/api/docs` is not exposed (disabled when `ENVIRONMENT=production`)
- [ ] `COOKIE_SECURE=true`, `CORS_ORIGINS` is set to the Vercel URL only, and the repository is private
