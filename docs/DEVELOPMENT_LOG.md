# Development log

## 2026-11-23: Initial implementation (phases 1–8)

**Starting point:** an empty repository.

**Environment preparation**
- Installed PostgreSQL 16 + pgvector 0.6 locally, so every test runs against the real database engine
  rather than SQLite.
- Enabled outbound access to OpenAlex, Crossref, Semantic Scholar and CORE for live integration testing.

**Backend**
- Wrote the schema and the initial Alembic migration: 47 tables, an HNSW vector index and GIN full-text
  indexes.
- Built:
  - auth with Argon2 and revocable JWT cookies
  - the knowledge-ingestion pipeline: extraction, section-aware chunking, embedding reuse across versions
  - hybrid retrieval with RRF
  - scholarly clients
  - the LLM router with fallback and a data-policy gate
  - LangGraph agent workflows
  - the durable job queue with resumable steps
  - the manuscript engine
  - citation formatting and audit
  - the sandboxed analysis runner with reproducibility hashing
  - qualitative coding
  - routers for all modules

**Frontend**
- Next.js 16 app router with shadcn-style primitives.
- Pages for all nine navigation areas, including the manuscript editor.

**Tests**
- 55 backend tests, including live OpenAlex and Crossref.
- A Playwright smoke test of the running stack.

**Issues found and fixed during testing**
- Full-text search used AND semantics, so natural-language questions failed to match notes missing any
  word. It now uses OR-of-terms with rank ordering, keeping websearch syntax for explicit operators.
- Form labels were not programmatically associated with their inputs. `Field` now links them.
- Modals and tabs gained `dialog` and `tab` roles.
- A React effect implicitly returned the Promise that recent Chromium's `scrollIntoView()` returns,
  crashing the workspace page. All concise-body effects were converted to block bodies.
- The AI-unavailable error now names the missing configuration for each provider.
- Long float example values in dataset schemas were shortened.

**Decisions**
- The embedding dimension is fixed at 768: it matches Gemini (with output dimensionality), nomic-embed
  and mpnet.
- The `hashing` lexical embedder is the zero-dependency default, labelled as non-semantic.
- The job worker is embedded in the API process, because free hosting tiers lack worker dynos.
- Analysis uses fixed procedure templates rather than model-generated code, for security.
- Manuscript writers may cite only project references, by citekey.
