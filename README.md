# ResearchLab

A private, AI-assisted academic research environment for a single researcher. It covers the research
lifecycle: building a personal knowledge base, discovering literature, interrogating ideas, choosing
theory and method, analysing data, writing complete manuscripts and reviewing them critically.

**Principle:** the researcher supplies direction, judgement and accountability. ResearchLab supplies
infrastructure, scholarly retrieval, computation and writing support. It never approves research
decisions on the researcher's behalf, never invents citations or data, and keeps researcher knowledge,
external evidence and AI interpretation visibly separate.

| | |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn-style components (`frontend/`) |
| Backend | Python 3.13, FastAPI, SQLAlchemy 2, Alembic (`backend/`) |
| Database | PostgreSQL 16 + pgvector (Neon in production) |
| AI | Provider abstraction: Gemini, Ollama (local), any OpenAI-compatible endpoint, with fallback; LangGraph agent workflows |
| Scholarly sources | OpenAlex, Crossref, Semantic Scholar, CORE; Zotero / BibTeX / RIS / CSL-JSON import |
| Analysis | pandas, SciPy, statsmodels, matplotlib, run as sandboxed, reproducible scripts |
| Documents | PyMuPDF, python-docx; DOCX, LaTeX and Markdown export |

## What it does

- **Knowledge Centre:** upload PDF, DOCX, TXT or Markdown, or import by DOI or URL. Each file goes
  through text extraction, metadata detection (title, DOI, abstract, headings, references), section-aware
  chunking with page locations, embedding and hybrid indexing (full-text + vector + metadata, fused with
  reciprocal rank fusion). The knowledge base also supports versioned replacement that re-embeds only the
  passages that changed, metadata correction with per-field provenance, duplicate detection,
  soft delete and restore, re-indexing, collections, knowledge-base Q&A and *knowledge development*
  (how your positions evolved over time).
- **Literature Discovery:** multi-source search with filters, a citation graph (cited-by and references),
  DOI and metadata verification against Crossref and OpenAlex, evidence extraction that keeps source
  locations, a review queue fed by scheduled scholarly-update watches, and project reference lists.
- **Research Workspace:** eight specialised agents coordinated with LangGraph: Strategist, Literature
  Researcher, Theoretical Analyst, Methodology Adviser, Research Analyst, Academic Writer, Critical
  Reviewer and Citation Auditor. It also provides:
  - idea interrogation, with gaps classified by type and as *established* vs *potential*
  - Research Challenge Mode
  - theory analysis, with conceptual-model diagrams and hypotheses
  - methodology advice
  - project-aware conversations, where only messages you explicitly promote become notes
- **Approval gates:** research questions, gaps, frameworks, methods, manuscript sections and data
  transformations are all *proposed* until you approve them. Every decision is logged.
- **Manuscripts:** guided mode (section by section) or complete mode (end to end) for 3,000 to 60,000
  words. The default 7,000-word structure follows the specification and is editable. Generation is
  resumable and step-based:
  - outline, then per-section and per-subsection writing
  - consistency check, citation audit and claim-source alignment
  - word-allocation check and critical review
  - assembly into a saved version

  The editor supports direct editing, citation insertion, preview with unresolved-citation highlighting,
  version history with diffs and restore, and DOCX, LaTeX and Markdown export in APA 7, Harvard, Chicago,
  IEEE or Vancouver. Without data, results sections list `[DATA REQUIRED: …]` items instead of findings.
- **Data analysis:** CSV, TSV, XLSX, JSON, SAV and text uploads. The workspace covers:
  - procedures: descriptives, missing values, correlations, t-tests, ANOVA with Tukey, chi-square,
    non-parametric tests, OLS and logistic regression, Cronbach's α, EFA, moderation, bootstrap mediation
    and plots
  - for each procedure, a stored standalone script, its assumptions, output tables and figures, and
    one-click reproduction verified by hash
  - transformations that require approval and always produce a derived dataset, never altering the original
  - qualitative segmentation and coding, with AI code suggestions that stay proposals until approved,
    plus themes, frequencies, co-occurrence and comparison by source
- **Researcher profile:** qualifications, interests and preferences, plus a writing-style profile built
  from your own samples. It combines deterministic stylometrics, an optional AI description (labelled
  as such) and your corrections, which take precedence.

## Quick start (local)

Prerequisites: Python 3.13, Node 22, PostgreSQL 16 with pgvector (or `docker compose up -d`).

```bash
docker compose up -d
```

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set SECRET_KEY; add GEMINI_API_KEY or run Ollama for AI features
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
cp .env.example .env.local    # BACKEND_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000. The first visit creates the single researcher account, and registration then
closes. Features that don't need AI (knowledge base, search, literature discovery, verification,
manuscript editing and export, statistics) work without any AI provider. AI features report clearly
which provider is missing.

## Tests

```bash
cd backend && pytest -q
```

The 55 backend tests run against real PostgreSQL + pgvector, including live OpenAlex and Crossref calls.
Those live tests are skipped when the network is unavailable.

```bash
cd frontend && npx tsc --noEmit && npx next build
```

With both servers running against an empty database, the browser smoke test runs the core workflows
through the real UI:

```bash
BASE_URL=http://localhost:3000 PDF=paper.pdf CSV=data.csv node e2e/smoke.mjs
```

The only test double is the language model: CI has no model credentials, so a deterministic scripted
provider stands in for it. See `docs/STATUS.md` for what has and hasn't been verified.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, agents, job engine, retrieval, integrity, security
- [docs/DATABASE.md](docs/DATABASE.md): schema, indexes and migrations
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): Neon, Supabase, Render, Vercel; environment, backups and recovery
- [docs/FREE_TIER_LIMITS.md](docs/FREE_TIER_LIMITS.md): free-tier constraints, fallbacks and data-policy cautions
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md): scholarly APIs, AI providers, Zotero
- [docs/STATUS.md](docs/STATUS.md): completed and incomplete functionality, with the test record
- [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md)

## Repository layout

```
backend/   FastAPI app (app/api routers, app/services domain logic), Alembic migrations, tests
frontend/  Next.js app router UI (app/(app)/* pages, components/, lib/api.ts)
e2e/       Playwright smoke test of the running application
scripts/   backup.sh / restore.sh
docs/      Architecture, schema, deployment, limits, status
```
