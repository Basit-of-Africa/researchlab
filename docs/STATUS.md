# Implementation status

This is an honest record of what is implemented, what was verified and how, and what remains.

## Verification summary

| Check | Result |
|---|---|
| Backend test suite (`pytest`, real PostgreSQL 16 + pgvector 0.6, Alembic migrations) | **55 passed** |
| Live scholarly integration tests (OpenAlex, Crossref) | **5 passed** (part of the 55) |
| Frontend type check (`tsc --noEmit`) and production build (`next build`) | **Pass** (14 routes) |
| Browser smoke test (`e2e/smoke.mjs`, Playwright/Chromium, real backend and frontend) | **12/12 steps passed, no console errors** |
| Backup (`scripts/backup.sh`) → restore (`scripts/restore.sh`) into a fresh database | **Pass**: row counts matched |
| Lint (`ruff --select F,E9`) | **Pass** |

The **only test double is the language model**. No model credentials were available, so agent and
manuscript workflows were tested with a deterministic scripted provider (`tests/conftest.py::ScriptedLLM`).
These tests verify orchestration, persistence, resumability, prompts, privacy filtering and integrity
checks, but **not the quality of real model output**.

## Acceptance scenarios (section 23 of the specification)

| Scenario | Status | Evidence |
|---|---|---|
| Upload PDF; extract; verify metadata; embed; search; replace; re-index; notes intact | Done | `test_knowledge.py` (8 tests) and smoke test |
| Search topic; retrieve publications; verify DOI; identify unavailable full texts; save to collection | Done | `test_scholarly.py` (live) and smoke test |
| Unfamiliar idea → literature → assessment → competing explanations → record decisions | Done (scripted LLM) | `test_research.py` |
| Manuscript: project, outline, allocations, sequential sections, interrupt, resume, verify citations, assemble, export DOCX | Done (scripted LLM) | `test_manuscript.py` (12 tests, including researcher pause, worker crash and lease expiry) and smoke test (DOCX download) |
| Dataset: upload, inspect, run procedure, reproduce from saved script, export tables and figures | Done | `test_analysis.py` (7 tests) and smoke test |
| Security: authentication, access restriction, API keys not exposed, upload validation, data recovery | Done | `test_auth_security.py` (12 tests) and backup/restore round trip |

## Module status

| Module | Status | Notes |
|---|---|---|
| A. Dashboard | Complete | Projects, manuscripts, recent documents and notes, job status, activity, health, quick actions |
| B. Researcher profile and writing style | Complete | Deterministic stylometrics, optional AI description (labelled), researcher overrides |
| C. Knowledge base | Complete | No OCR for scanned PDFs; such files fail with a clear message. Docling is not bundled (heavy dependency); PyMuPDF is used instead |
| Scholarly discovery | Complete for OpenAlex and Crossref | Semantic Scholar implemented but rate-limited without a key; CORE and the Zotero Web API need keys and were not exercised |
| Evidence extraction | Complete (scripted LLM) | Each field keeps source locations and a quote; researcher can verify or reject |
| Idea exploration and gap assessment | Complete (scripted LLM) | LangGraph: strategist → retrieval → literature → strategist → reviewer |
| Theory and conceptual frameworks | Complete (scripted LLM) | Conceptual-model SVG diagram; approval gates |
| Methodology advisory | Complete (scripted LLM) | Researcher-authored designs supported |
| Manuscript generation | Complete (scripted LLM) | Guided and complete modes; resumable; 3,000–60,000 words; no fabricated results |
| Agents (8) | Complete | Shared context, role prompts, run logging |
| Research Challenge Mode | Complete (scripted LLM) | |
| Citation and integrity engine | Complete | APA 7, Harvard, Chicago author-date, IEEE, Vancouver |
| Data analysis | Complete for Python procedures | **R is not available** in this deployment; **SEM is not bundled** (semopy/lavaan) |
| Qualitative analysis | Complete | Segmentation, codebook, AI suggestions as proposals, thematic and content summaries |
| Project management | Complete | Create, duplicate, archive, rename, delete with confirmation, export ZIP, version compare, decision log |
| Approval system | Complete | Approve, reject, revise, regenerate, instructions, save for later; all logged |
| Knowledge development | Complete (scripted LLM) | Uses dated notes and the decision log |
| UI | Complete for all nine navigation areas | Responsive; light and dark |
| Deployment | Configuration written, **not executed on live accounts** | `render.yaml`, Vercel settings, CI workflow, docs |

## Known limitations and next steps

1. **Live AI providers untested.**
   - Run a real Gemini or Ollama session and review output quality, especially JSON-mode reliability for
     smaller local models.
   - Calibrate prompts and section word-count adherence with real models.
2. **Semantic embeddings.** The default `hashing` embedder is lexical. For semantic retrieval, enable
   `sentence_transformers` (heavy for free hosts), `ollama` or `gemini`, then regenerate embeddings.
3. **PostgreSQL row-level security policies** are not enabled. Isolation is enforced in the application
   layer and tested.
4. **OCR** for scanned PDFs is not included.
5. **Vercel proxy body limits** may restrict very large uploads in the hosted configuration.
6. **Rate limiting** is in-memory (per process), which is adequate for a single instance.
7. **Approval-gate UX.** Guided-mode ordering of section approvals is advisory (a warning), not enforced.
