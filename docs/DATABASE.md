# Database schema

PostgreSQL 16 with the `pgvector` extension (Neon in production). The schema is created and evolved
exclusively through Alembic migrations in `backend/alembic/versions/`.

```bash
cd backend && alembic upgrade head          # apply migrations
alembic revision --autogenerate -m "..."    # after changing app/models.py
```

## Design principles

- **UUID primary keys** and `created_at` / `updated_at` timestamps (UTC) on every table.
- **Multi-researcher ready.** Every top-level entity has an `owner_id` (→ `users`). The first release is
  single-researcher, but all queries are owner-scoped (`app/api/deps.py::owned`) so adding researchers
  needs no schema change. `project_members` is in place for future collaboration.
- **Originals are stored separately.** Files live in object storage (Supabase Storage or the local
  filesystem); `document_versions.storage_key` and `datasets.storage_key` point at them. Each upload gets a
  new key and is never overwritten.
- **Versioning instead of overwriting.** Documents have `document_versions`; manuscripts have
  `manuscript_versions` (JSON snapshots); datasets derived by cleaning point to their `parent_dataset_id`
  and record every transformation.
- **Provenance is first-class.** `knowledge_category` (researcher_writing / established_position /
  provisional_opinion / external_evidence / ai_interpretation) appears on documents, notes, frameworks,
  evidence extractions and workspace messages. `documents.metadata_source` records, per field, whether a
  value was extracted, came from Crossref, or was set by the researcher.
- **Integrity records.** `references` hold CSL-style bibliographic data with a `verification_status`;
  each registry check is logged in `source_verification_records`; manuscript citations are materialised
  in `citations` with claim text and claim-source alignment results.
- **Durable jobs.** `generation_jobs` (lease-based queue) and `generation_job_steps` (resumable steps).
- **Audit trail.** `audit_logs` records significant actions; `research_decisions` is the researcher's
  decision log (approvals, rejections, revisions, instructions).

## Indexes of note

| Index | Purpose |
|---|---|
| `ix_document_embeddings_hnsw` — HNSW on `document_embeddings.embedding` (`vector_cosine_ops`, m=16, ef_construction=64) | Semantic retrieval |
| `ix_document_chunks_tsv` — GIN on generated `tsvector` (`section` + `text`) | Full-text retrieval |
| `ix_research_notes_tsv` — GIN on generated `tsvector` (`title` + `body`) | Note search |
| `uq_publications_owner_doi`, `uq_publications_owner_openalex` | De-duplicate discovered publications |
| `uq_reference_citekey` (owner, project, citekey) | Stable citekeys per project |
| `document_embeddings (chunk_id, model)` unique + `content_hash` index | Re-use embeddings of unchanged chunks across versions |
| `ix_generation_jobs_status_created` | Queue polling (`FOR UPDATE SKIP LOCKED`) |

Embeddings are fixed at **768 dimensions**; each row stores its `model` name, and retrieval only compares
vectors produced by the configured model.

## Row-level security

Data separation is enforced in the application layer: every endpoint resolves entities through
owner-scoped helpers and returns 404 for anything the caller does not own (covered by
`tests/test_auth_security.py::test_project_isolation_between_researchers`). PostgreSQL RLS policies are
not enabled in this release because the API connects as a single database role; if direct database
access by multiple roles is ever introduced, add RLS policies keyed on `owner_id`.

## Tables

### Identity & profile

**users** — `id` uuid, `email` varchar, `password_hash` varchar, `display_name` varchar, `is_active` boolean, `token_version` integer, `last_login_at` datetime, `created_at` datetime, `updated_at` datetime

**researcher_profiles** — `id` uuid, `user_id` uuid → users, `full_name` varchar, `title` varchar, `affiliation` varchar, `orcid` varchar, `bio` text, `qualifications` jsonb, `disciplines` jsonb, `methodological_experience` jsonb, `theoretical_preferences` jsonb, `referencing_style` varchar, `language_variant` varchar, `writing_conventions` text, `research_history_summary` text, `created_at` datetime, `updated_at` datetime

**research_interests** — `id` uuid, `owner_id` uuid → users, `label` varchar, `description` text, `keywords` jsonb, `is_active` boolean, `created_at` datetime, `updated_at` datetime

**writing_profiles** — `id` uuid, `owner_id` uuid → users, `name` varchar, `is_default` boolean, `sample_document_ids` jsonb, `metrics` jsonb, `qualitative` jsonb, `researcher_overrides` jsonb, `version` integer, `status` varchar, `created_at` datetime, `updated_at` datetime

**app_settings** — `id` uuid, `owner_id` uuid → users, `key` varchar, `value` jsonb, `created_at` datetime, `updated_at` datetime

### Projects & research reasoning

**research_projects** — `id` uuid, `owner_id` uuid → users, `title` varchar, `description` text, `objectives` jsonb, `discipline` varchar, `status` varchar, `archived_at` datetime, `last_accessed_at` datetime, `collection_id` uuid → knowledge_collections, `created_at` datetime, `updated_at` datetime

**project_members** — `id` uuid, `project_id` uuid → research_projects, `user_id` uuid → users, `role` varchar, `created_at` datetime, `updated_at` datetime

**research_ideas** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `statement` text, `researcher_context` text, `assessment` jsonb, `researcher_response` text, `status` varchar, `version` integer, `parent_id` uuid → research_ideas, `created_at` datetime, `updated_at` datetime

**research_questions** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `text` text, `kind` varchar, `status` varchar, `origin` varchar, `position` integer, `rationale` text, `created_at` datetime, `updated_at` datetime

**research_decisions** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `decision_type` varchar, `subject` text, `decision` varchar, `rationale` text, `instructions` text, `payload` jsonb, `entity_type` varchar, `entity_id` uuid, `decided_by` varchar, `created_at` datetime, `updated_at` datetime

**theoretical_frameworks** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `name` varchar, `origins` text, `assumptions` jsonb, `constructs` jsonb, `strengths` jsonb, `limitations` jsonb, `suitability` text, `model` jsonb, `hypotheses` jsonb, `source_refs` jsonb, `status` varchar, `origin` varchar, `knowledge_category` varchar, `created_at` datetime, `updated_at` datetime

**research_methods** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `approach` varchar, `design` text, `population` text, `sampling` text, `sample_size` text, `variables` jsonb, `instruments` text, `data_collection` text, `reliability_validity` text, `ethics` text, `analysis_plan` text, `rationale` text, `status` varchar, `origin` varchar, `created_at` datetime, `updated_at` datetime

**research_notes** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `document_id` uuid → documents, `publication_id` uuid → publications, `title` varchar, `body` text, `note_type` varchar, `knowledge_category` varchar, `tags` jsonb, `anchor` jsonb, `tsv` tsvector, `created_at` datetime, `updated_at` datetime

**researcher_feedback** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `target_type` varchar, `target_id` uuid, `rating` integer, `feedback` text, `applies_to_profile` boolean, `created_at` datetime, `updated_at` datetime

### Knowledge base

**knowledge_collections** — `id` uuid, `owner_id` uuid → users, `name` varchar, `kind` varchar, `description` text, `is_system` boolean, `created_at` datetime, `updated_at` datetime

**documents** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `publication_id` uuid → publications, `title` text, `authors` jsonb, `year` integer, `abstract` text, `doi` varchar, `url` text, `venue` text, `doc_type` varchar, `source_kind` varchar, `visibility` varchar, `knowledge_category` varchar, `is_own_work` boolean, `status` varchar, `status_detail` text, `current_version_id` uuid, `metadata_source` jsonb, `metadata_verified` boolean, `duplicate_of_id` uuid, `extra` jsonb, `deleted_at` datetime, `created_at` datetime, `updated_at` datetime

**document_versions** — `id` uuid, `document_id` uuid → documents, `version_no` integer, `storage_key` text, `filename` text, `mime_type` varchar, `size_bytes` bigint, `content_hash` varchar, `extracted_text` text, `page_count` integer, `headings` jsonb, `extracted_metadata` jsonb, `extracted_references` jsonb, `change_note` text, `processing_status` varchar, `created_at` datetime, `updated_at` datetime

**document_chunks** — `id` uuid, `document_id` uuid → documents, `version_id` uuid → document_versions, `chunk_index` integer, `text` text, `section` text, `page_start` integer, `page_end` integer, `char_start` integer, `char_end` integer, `token_estimate` integer, `content_hash` varchar, `tsv` tsvector, `created_at` datetime, `updated_at` datetime

**document_embeddings** — `id` uuid, `chunk_id` uuid → document_chunks, `model` varchar, `content_hash` varchar, `embedding` vector, `created_at` datetime, `updated_at` datetime

**collection_documents** — `id` uuid, `collection_id` uuid → knowledge_collections, `document_id` uuid → documents, `added_at` datetime

**collection_publications** — `id` uuid, `collection_id` uuid → knowledge_collections, `publication_id` uuid → publications, `added_at` datetime

### Scholarly records & integrity

**publications** — `id` uuid, `owner_id` uuid → users, `title` text, `year` integer, `publication_date` varchar, `venue` text, `publisher` text, `volume` varchar, `issue` varchar, `pages` varchar, `abstract` text, `abstract_source` varchar, `doi` varchar, `openalex_id` varchar, `s2_id` varchar, `core_id` varchar, `url` text, `pub_type` varchar, `is_oa` boolean, `oa_url` text, `full_text_available` boolean, `cited_by_count` integer, `concepts` jsonb, `source_db` varchar, `verification_status` varchar, `verified_at` datetime, `credibility_note` text, `raw` jsonb, `created_at` datetime, `updated_at` datetime

**publication_authors** — `id` uuid, `publication_id` uuid → publications, `position` integer, `name` varchar, `given` varchar, `family` varchar, `orcid` varchar, `openalex_author_id` varchar, `institution` text

**evidence_extractions** — `id` uuid, `owner_id` uuid → users, `publication_id` uuid → publications, `document_id` uuid → documents, `basis` varchar, `fields` jsonb, `provider` varchar, `model` varchar, `status` varchar, `knowledge_category` varchar, `created_at` datetime, `updated_at` datetime

**literature_watches** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `name` varchar, `query` text, `authors` jsonb, `filters` jsonb, `sources` jsonb, `frequency_days` integer, `is_active` boolean, `last_run_at` datetime, `last_result_count` integer, `created_at` datetime, `updated_at` datetime

**discovery_queue_items** — `id` uuid, `owner_id` uuid → users, `watch_id` uuid → literature_watches, `publication_id` uuid → publications, `status` varchar, `classification` varchar, `note` text, `decided_at` datetime, `created_at` datetime, `updated_at` datetime

**references** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `publication_id` uuid → publications, `document_id` uuid → documents, `citekey` varchar, `csl` jsonb, `raw_text` text, `source_type` varchar, `verification_status` varchar, `verification_detail` jsonb, `verified_at` datetime, `zotero_key` varchar, `created_at` datetime, `updated_at` datetime

**citations** — `id` uuid, `section_id` uuid → manuscript_sections, `reference_id` uuid → references, `citekey` varchar, `claim_text` text, `char_offset` integer, `locator` varchar, `is_quotation` boolean, `alignment_status` varchar, `alignment_note` text, `supporting_evidence` jsonb, `created_at` datetime, `updated_at` datetime

**source_verification_records** — `id` uuid, `owner_id` uuid → users, `reference_id` uuid → references, `publication_id` uuid → publications, `document_id` uuid → documents, `check_type` varchar, `status` varchar, `source` varchar, `details` jsonb, `created_at` datetime, `updated_at` datetime

### Manuscripts

**manuscripts** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `title` text, `manuscript_type` varchar, `discipline` varchar, `target_journal` text, `target_word_count` integer, `count_references_in_words` boolean, `citation_style` varchar, `language_variant` varchar, `mode` varchar, `requirements` jsonb, `writing_profile_id` uuid → writing_profiles, `status` varchar, `last_accessed_at` datetime, `quality_report` jsonb, `created_at` datetime, `updated_at` datetime

**manuscript_sections** — `id` uuid, `manuscript_id` uuid → manuscripts, `key` varchar, `heading` text, `position` integer, `target_words` integer, `objective` text, `outline` jsonb, `evidence_plan` jsonb, `content` text, `word_count` integer, `status` varchar, `origin` varchar, `outstanding_requirements` jsonb, `evidence_used` jsonb, `approved_at` datetime, `content_version` integer, `created_at` datetime, `updated_at` datetime

**manuscript_versions** — `id` uuid, `manuscript_id` uuid → manuscripts, `version_no` integer, `label` varchar, `snapshot` jsonb, `total_words` integer, `created_by` varchar, `created_at` datetime, `updated_at` datetime

### Jobs & agents

**generation_jobs** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `manuscript_id` uuid → manuscripts, `job_type` varchar, `title` text, `status` varchar, `params` jsonb, `result` jsonb, `error` text, `progress` float, `attempts` integer, `max_attempts` integer, `locked_by` varchar, `locked_until` datetime, `run_after` datetime, `started_at` datetime, `finished_at` datetime, `created_at` datetime, `updated_at` datetime

**generation_job_steps** — `id` uuid, `job_id` uuid → generation_jobs, `step_key` varchar, `label` text, `position` integer, `status` varchar, `output` jsonb, `error` text, `attempts` integer, `started_at` datetime, `finished_at` datetime, `created_at` datetime, `updated_at` datetime

**agent_runs** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `job_id` uuid → generation_jobs, `agent` varchar, `task` varchar, `input_summary` jsonb, `output` jsonb, `provider` varchar, `model` varchar, `status` varchar, `error` text, `tokens_in` integer, `tokens_out` integer, `duration_ms` integer, `excluded_private_sources` integer, `created_at` datetime, `updated_at` datetime

**workspace_threads** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `title` text, `created_at` datetime, `updated_at` datetime

**workspace_messages** — `id` uuid, `thread_id` uuid → workspace_threads, `role` varchar, `agent` varchar, `content` text, `sources` jsonb, `knowledge_category` varchar, `promoted_to` jsonb, `created_at` datetime, `updated_at` datetime

### Data analysis

**datasets** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `name` varchar, `kind` varchar, `storage_key` text, `filename` text, `file_format` varchar, `size_bytes` bigint, `content_hash` varchar, `n_rows` integer, `n_cols` integer, `schema` jsonb, `parent_dataset_id` uuid → datasets, `transformations` jsonb, `approved_by_researcher` boolean, `description` text, `created_at` datetime, `updated_at` datetime

**research_analyses** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `dataset_id` uuid → datasets, `procedure` varchar, `params` jsonb, `status` varchar, `assumptions` jsonb, `interpretation` text, `ai_interpretation` text, `error` text, `ran_at` datetime, `reproduced_at` datetime, `reproducible` boolean, `created_at` datetime, `updated_at` datetime

**analysis_scripts** — `id` uuid, `analysis_id` uuid → research_analyses, `language` varchar, `code` text, `code_hash` varchar, `runtime_info` jsonb, `created_at` datetime, `updated_at` datetime

**analysis_outputs** — `id` uuid, `analysis_id` uuid → research_analyses, `kind` varchar, `title` text, `content` jsonb, `storage_key` text, `output_hash` varchar, `created_at` datetime, `updated_at` datetime

**qual_codes** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `name` varchar, `description` text, `theme` varchar, `origin` varchar, `status` varchar, `color` varchar, `created_at` datetime, `updated_at` datetime

**qual_segments** — `id` uuid, `owner_id` uuid → users, `project_id` uuid → research_projects, `dataset_id` uuid → datasets, `document_id` uuid → documents, `source_label` varchar, `text` text, `char_start` integer, `char_end` integer, `memo` text, `created_at` datetime, `updated_at` datetime

**qual_codings** — `id` uuid, `segment_id` uuid → qual_segments, `code_id` uuid → qual_codes, `origin` varchar, `status` varchar, `created_at` datetime, `updated_at` datetime

### Audit

**audit_logs** — `id` uuid, `user_id` uuid → users, `action` varchar, `entity_type` varchar, `entity_id` varchar, `project_id` uuid, `details` jsonb, `ip` varchar, `created_at` datetime

