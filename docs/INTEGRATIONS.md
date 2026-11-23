# Integrations

All integrations are real HTTP clients (`backend/app/services/scholarly/clients.py`,
`backend/app/services/llm/providers.py`). Records keep only fields the source actually returned.
Missing metadata stays empty and is never inferred.

## Scholarly sources

| Source | Used for | Configuration | Verified in development |
|---|---|---|---|
| **OpenAlex** | Work search with filters (year, type, OA, author, source, minimum citations, sort); author and institution search; citing works, references, related works; abstracts (reconstructed from the inverted index and labelled as such); literature watches | optional `SCHOLARLY_CONTACT_EMAIL`, `OPENALEX_API_KEY` | Yes, live tests (`tests/test_scholarly.py`) and browser smoke test |
| **Crossref** | DOI resolution and bibliographic verification; bibliographic title search; search source | optional `SCHOLARLY_CONTACT_EMAIL` | Yes, live tests: verified, mismatch and not-found cases |
| **Semantic Scholar** | Search source; citations, references and recommendations | optional `SEMANTIC_SCHOLAR_API_KEY` | Client implemented; unauthenticated requests returned HTTP 429 (shared rate limit) in this environment, so no live result was confirmed |
| **CORE** | Open-access full-text discovery | `CORE_API_KEY` (free) | Integration boundary: returns a clear "not configured" error until a key is set; not exercised live |
| **Zotero** | Import a library or collection as project references (CSL-JSON via the Zotero Web API) | `ZOTERO_API_KEY`, `ZOTERO_USER_ID` | Not exercised live. The BibTeX, RIS and CSL-JSON file import used for Zotero exports is tested (`tests/test_imports.py`) |

What each publication record distinguishes:

- `verification_status`: whether the metadata was confirmed by a registry. Search results start
  `unverified`.
- `abstract` and `abstract_source`: whether an abstract was retrieved, and where from.
- `is_oa` / `oa_url`: open-access status.
- `full_text_available`: whether a legal full text is known to exist. ResearchLab never stores full
  text it did not receive from you.

Discovery is not endorsement. Discovered items enter the library or the review queue, and only items
you add to a project's reference list can be cited by the Academic Writer.

**Licensing.** ResearchLab downloads full text only when you upload it or import a URL you provide.
Respect publisher terms, repository licences and API terms of use.

## AI providers

| Provider | Data leaves your machine? | Configuration |
|---|---|---|
| Gemini (`generativelanguage.googleapis.com`, REST) | Yes | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Ollama (`/api/chat`, `/api/embed`) | No | `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL` |
| OpenAI-compatible (`/chat/completions`) | Depends on host (localhost = no) | `OPENAI_COMPAT_BASE_URL`, `OPENAI_COMPAT_MODEL`, `OPENAI_COMPAT_API_KEY` |

`LLM_PROVIDERS` sets the priority order. The router falls back to the next provider on quota or
availability errors. Model names live only in configuration. Agents call the router, never a vendor SDK,
so you can swap models without touching the agent architecture.

The provider adapters were not exercised against live endpoints during development, because no
credentials or local Ollama were available. The request and response handling follows each provider's
documented REST interface. The agent pipelines above them were tested end to end with a deterministic
test provider. **The first live run of each provider should be watched**; see `docs/STATUS.md`.
