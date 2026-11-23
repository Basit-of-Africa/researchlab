"""ORM models: the persistent source of truth for ResearchLab.

Conventions
-----------
* UUID primary keys, ``created_at``/``updated_at`` timestamps (UTC).
* Every top-level entity carries ``owner_id`` so that multi-researcher support
  can be introduced later without schema rework; all queries are owner-scoped.
* Original files live in object storage; only their storage keys and metadata
  live here.
* ``knowledge_category`` separates researcher writing, established positions,
  provisional opinions, external evidence and AI interpretations. These are
  never interchangeable.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EMBEDDING_DIM = 768


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk(target: str, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# Enumerations (stored as strings for forward compatibility)
# ---------------------------------------------------------------------------
class KnowledgeCategory(str, enum.Enum):
    researcher_writing = "researcher_writing"
    established_position = "established_position"
    provisional_opinion = "provisional_opinion"
    external_evidence = "external_evidence"
    ai_interpretation = "ai_interpretation"


class CollectionKind(str, enum.Enum):
    personal_materials = "personal_materials"
    scholarly_literature = "scholarly_literature"
    research_notes = "research_notes"
    theory_methods = "theory_methods"
    research_history = "research_history"
    custom = "custom"


class Visibility(str, enum.Enum):
    private_unpublished = "private_unpublished"  # never sent to external AI unless permitted
    published = "published"
    public_source = "public_source"


class ApprovalStatus(str, enum.Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    revised = "revised"
    deferred = "deferred"  # "save for later"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    paused = "paused"
    cancel_requested = "cancel_requested"
    awaiting_approval = "awaiting_approval"


# ---------------------------------------------------------------------------
# Identity & profile
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke sessions
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearcherProfile(TimestampMixin, Base):
    __tablename__ = "researcher_profiles"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    full_name: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    affiliation: Mapped[str] = mapped_column(String(500), default="")
    orcid: Mapped[str] = mapped_column(String(50), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    # [{degree, field, institution, year}] — only what the researcher supplies
    qualifications: Mapped[list] = mapped_column(JSONB, default=list)
    disciplines: Mapped[list] = mapped_column(JSONB, default=list)
    methodological_experience: Mapped[list] = mapped_column(JSONB, default=list)
    theoretical_preferences: Mapped[list] = mapped_column(JSONB, default=list)
    referencing_style: Mapped[str] = mapped_column(String(40), default="apa7")
    language_variant: Mapped[str] = mapped_column(String(10), default="en-GB")
    writing_conventions: Mapped[str] = mapped_column(Text, default="")
    research_history_summary: Mapped[str] = mapped_column(Text, default="")


class ResearchInterest(TimestampMixin, Base):
    __tablename__ = "research_interests"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    label: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WritingProfile(TimestampMixin, Base):
    __tablename__ = "writing_profiles"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    name: Mapped[str] = mapped_column(String(200), default="Primary writing profile")
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    sample_document_ids: Mapped[list] = mapped_column(JSONB, default=list)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)  # deterministic stylometrics
    qualitative: Mapped[dict] = mapped_column(JSONB, default=dict)  # AI-generated description (labelled)
    researcher_overrides: Mapped[dict] = mapped_column(JSONB, default=dict)  # researcher corrections win
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")  # draft | reviewed


class AppSetting(TimestampMixin, Base):
    """Per-researcher runtime preferences (never secrets — those are env vars)."""

    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("owner_id", "key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[Any] = mapped_column(JSONB)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ResearchProject(TimestampMixin, Base):
    __tablename__ = "research_projects"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    objectives: Mapped[list] = mapped_column(JSONB, default=list)
    discipline: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_collections.id", ondelete="SET NULL")
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    role: Mapped[str] = mapped_column(String(30), default="owner")


class ResearchIdea(TimestampMixin, Base):
    __tablename__ = "research_ideas"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    researcher_context: Mapped[str] = mapped_column(Text, default="")
    assessment: Mapped[dict] = mapped_column(JSONB, default=dict)  # AI interpretation (labelled)
    researcher_response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="exploring")
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("research_ideas.id", ondelete="SET NULL"))


class ResearchQuestion(TimestampMixin, Base):
    __tablename__ = "research_questions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="primary")  # primary | secondary | hypothesis | objective
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.proposed.value)
    origin: Mapped[str] = mapped_column(String(20), default="researcher")  # researcher | ai
    position: Mapped[int] = mapped_column(Integer, default=0)
    rationale: Mapped[str] = mapped_column(Text, default="")


class ResearchDecision(TimestampMixin, Base):
    """Immutable log of significant researcher decisions and approvals."""

    __tablename__ = "research_decisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    decision_type: Mapped[str] = mapped_column(String(40))  # research_question|gap|theory|method|argument|manuscript|data|other
    subject: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(20))  # approved|rejected|revised|regenerate|deferred|instruction
    rationale: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_by: Mapped[str] = mapped_column(String(20), default="researcher")


class TheoreticalFramework(TimestampMixin, Base):
    __tablename__ = "theoretical_frameworks"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    origins: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[list] = mapped_column(JSONB, default=list)
    constructs: Mapped[list] = mapped_column(JSONB, default=list)  # [{name, definition}]
    strengths: Mapped[list] = mapped_column(JSONB, default=list)
    limitations: Mapped[list] = mapped_column(JSONB, default=list)
    suitability: Mapped[str] = mapped_column(Text, default="")
    # Conceptual model: {nodes:[{id,label,type}], edges:[{source,target,label,hypothesis}]}
    model: Mapped[dict] = mapped_column(JSONB, default=dict)
    hypotheses: Mapped[list] = mapped_column(JSONB, default=list)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.proposed.value)
    origin: Mapped[str] = mapped_column(String(20), default="ai")
    knowledge_category: Mapped[str] = mapped_column(String(40), default=KnowledgeCategory.ai_interpretation.value)


class ResearchMethod(TimestampMixin, Base):
    __tablename__ = "research_methods"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    approach: Mapped[str] = mapped_column(String(40), default="")  # quantitative|qualitative|mixed|conceptual|review|...
    design: Mapped[str] = mapped_column(Text, default="")
    population: Mapped[str] = mapped_column(Text, default="")
    sampling: Mapped[str] = mapped_column(Text, default="")
    sample_size: Mapped[str] = mapped_column(Text, default="")
    variables: Mapped[list] = mapped_column(JSONB, default=list)  # [{name, role, operational_definition, measure}]
    instruments: Mapped[str] = mapped_column(Text, default="")
    data_collection: Mapped[str] = mapped_column(Text, default="")
    reliability_validity: Mapped[str] = mapped_column(Text, default="")
    ethics: Mapped[str] = mapped_column(Text, default="")
    analysis_plan: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.proposed.value)
    origin: Mapped[str] = mapped_column(String(20), default="ai")


class ResearchNote(TimestampMixin, Base):
    __tablename__ = "research_notes"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True, ondelete="SET NULL")
    publication_id: Mapped[uuid.UUID | None] = fk("publications.id", nullable=True, ondelete="SET NULL")
    title: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text)
    note_type: Mapped[str] = mapped_column(String(30), default="observation")
    knowledge_category: Mapped[str] = mapped_column(String(40), default=KnowledgeCategory.provisional_opinion.value)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    anchor: Mapped[dict] = mapped_column(JSONB, default=dict)  # e.g. {page, section, quote}
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(title,'') || ' ' || coalesce(body,''))", persisted=True)
    )
    __table_args__ = (Index("ix_research_notes_tsv", "tsv", postgresql_using="gin"),)


class ResearcherFeedback(TimestampMixin, Base):
    __tablename__ = "researcher_feedback"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rating: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, default="")
    applies_to_profile: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------
class KnowledgeCollection(TimestampMixin, Base):
    __tablename__ = "knowledge_collections"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), default=CollectionKind.custom.value)
    description: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    publication_id: Mapped[uuid.UUID | None] = fk("publications.id", nullable=True, ondelete="SET NULL")
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    abstract: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[str] = mapped_column(Text, default="")
    doc_type: Mapped[str] = mapped_column(String(50), default="article")
    source_kind: Mapped[str] = mapped_column(String(30), default="upload")  # upload|doi|url|repository|note
    visibility: Mapped[str] = mapped_column(String(30), default=Visibility.private_unpublished.value)
    knowledge_category: Mapped[str] = mapped_column(String(40), default=KnowledgeCategory.external_evidence.value)
    is_own_work: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|ready|failed
    status_detail: Mapped[str] = mapped_column(Text, default="")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_source: Mapped[dict] = mapped_column(JSONB, default=dict)  # field -> extracted|crossref|researcher
    metadata_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version_no"
    )


class DocumentVersion(TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = fk("documents.id")
    version_no: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text, default="")
    filename: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(150), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int | None] = mapped_column(Integer)
    headings: Mapped[list] = mapped_column(JSONB, default=list)
    extracted_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    extracted_references: Mapped[list] = mapped_column(JSONB, default=list)
    change_note: Mapped[str] = mapped_column(Text, default="")
    processing_status: Mapped[str] = mapped_column(String(20), default="pending")

    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"
    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = fk("documents.id")
    version_id: Mapped[uuid.UUID] = fk("document_versions.id")
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(Text, default="")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(section,'') || ' ' || text)", persisted=True)
    )
    __table_args__ = (Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),)


class DocumentEmbedding(TimestampMixin, Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model"),
        Index(
            "ix_document_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = fk("document_chunks.id")
    model: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))


class CollectionDocument(Base):
    __tablename__ = "collection_documents"
    __table_args__ = (UniqueConstraint("collection_id", "document_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    collection_id: Mapped[uuid.UUID] = fk("knowledge_collections.id")
    document_id: Mapped[uuid.UUID] = fk("documents.id")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


class CollectionPublication(Base):
    __tablename__ = "collection_publications"
    __table_args__ = (UniqueConstraint("collection_id", "publication_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    collection_id: Mapped[uuid.UUID] = fk("knowledge_collections.id")
    publication_id: Mapped[uuid.UUID] = fk("publications.id")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


# ---------------------------------------------------------------------------
# Scholarly records
# ---------------------------------------------------------------------------
class Publication(TimestampMixin, Base):
    """A bibliographic record discovered via scholarly APIs or derived from a document.

    ``verification_status`` records whether the metadata was confirmed against
    an authoritative registry (Crossref/OpenAlex). Abstract presence and full
    text availability are tracked separately and never conflated.
    """

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("owner_id", "doi", name="uq_publications_owner_doi"),
        UniqueConstraint("owner_id", "openalex_id", name="uq_publications_owner_openalex"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    publication_date: Mapped[str] = mapped_column(String(20), default="")
    venue: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(Text, default="")
    volume: Mapped[str] = mapped_column(String(50), default="")
    issue: Mapped[str] = mapped_column(String(50), default="")
    pages: Mapped[str] = mapped_column(String(50), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    abstract_source: Mapped[str] = mapped_column(String(40), default="")
    doi: Mapped[str | None] = mapped_column(String(255))
    openalex_id: Mapped[str | None] = mapped_column(String(64))
    s2_id: Mapped[str | None] = mapped_column(String(64))
    core_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text, default="")
    pub_type: Mapped[str] = mapped_column(String(60), default="")
    is_oa: Mapped[bool | None] = mapped_column(Boolean)
    oa_url: Mapped[str] = mapped_column(Text, default="")
    full_text_available: Mapped[bool] = mapped_column(Boolean, default=False)
    cited_by_count: Mapped[int | None] = mapped_column(Integer)
    concepts: Mapped[list] = mapped_column(JSONB, default=list)
    source_db: Mapped[str] = mapped_column(String(40), default="")
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credibility_note: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    authors: Mapped[list[PublicationAuthor]] = relationship(
        back_populates="publication", cascade="all, delete-orphan", order_by="PublicationAuthor.position"
    )


class PublicationAuthor(Base):
    __tablename__ = "publication_authors"
    id: Mapped[uuid.UUID] = uuid_pk()
    publication_id: Mapped[uuid.UUID] = fk("publications.id")
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(400))
    given: Mapped[str] = mapped_column(String(200), default="")
    family: Mapped[str] = mapped_column(String(200), default="")
    orcid: Mapped[str] = mapped_column(String(50), default="")
    openalex_author_id: Mapped[str] = mapped_column(String(64), default="")
    institution: Mapped[str] = mapped_column(Text, default="")

    publication: Mapped[Publication] = relationship(back_populates="authors")


class EvidenceExtraction(TimestampMixin, Base):
    __tablename__ = "evidence_extractions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    publication_id: Mapped[uuid.UUID | None] = fk("publications.id", nullable=True)
    document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True)
    basis: Mapped[str] = mapped_column(String(20), default="abstract")  # abstract | full_text
    # {field: {value, locations:[{chunk_id, page, section, quote}]}}
    fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    provider: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="ai_extracted")  # ai_extracted|researcher_verified|rejected
    knowledge_category: Mapped[str] = mapped_column(String(40), default=KnowledgeCategory.ai_interpretation.value)


class LiteratureWatch(TimestampMixin, Base):
    __tablename__ = "literature_watches"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    name: Mapped[str] = mapped_column(String(300))
    query: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSONB, default=list)  # OpenAlex author ids or names
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    sources: Mapped[list] = mapped_column(JSONB, default=lambda: ["openalex", "crossref"])
    frequency_days: Mapped[int] = mapped_column(Integer, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_result_count: Mapped[int] = mapped_column(Integer, default=0)


class DiscoveryQueueItem(TimestampMixin, Base):
    __tablename__ = "discovery_queue_items"
    __table_args__ = (UniqueConstraint("owner_id", "publication_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    watch_id: Mapped[uuid.UUID | None] = fk("literature_watches.id", nullable=True, ondelete="SET NULL")
    publication_id: Mapped[uuid.UUID] = fk("publications.id")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|saved
    classification: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Citations & integrity
# ---------------------------------------------------------------------------
class Reference(TimestampMixin, Base):
    __tablename__ = "references"
    __table_args__ = (UniqueConstraint("owner_id", "project_id", "citekey", name="uq_reference_citekey"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    publication_id: Mapped[uuid.UUID | None] = fk("publications.id", nullable=True, ondelete="SET NULL")
    document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True, ondelete="SET NULL")
    citekey: Mapped[str] = mapped_column(String(200))
    # CSL-like record: {title, authors:[{family,given}], year, venue, volume, issue, pages, doi, url, publisher, type}
    csl: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(20), default="primary")  # primary | secondary
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    verification_detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zotero_key: Mapped[str] = mapped_column(String(50), default="")


class Citation(TimestampMixin, Base):
    __tablename__ = "citations"
    id: Mapped[uuid.UUID] = uuid_pk()
    section_id: Mapped[uuid.UUID] = fk("manuscript_sections.id")
    reference_id: Mapped[uuid.UUID | None] = fk("references.id", nullable=True, ondelete="SET NULL")
    citekey: Mapped[str] = mapped_column(String(200))
    claim_text: Mapped[str] = mapped_column(Text, default="")
    char_offset: Mapped[int] = mapped_column(Integer, default=0)
    locator: Mapped[str] = mapped_column(String(100), default="")  # page or section
    is_quotation: Mapped[bool] = mapped_column(Boolean, default=False)
    alignment_status: Mapped[str] = mapped_column(String(30), default="unchecked")
    alignment_note: Mapped[str] = mapped_column(Text, default="")
    supporting_evidence: Mapped[dict] = mapped_column(JSONB, default=dict)


class SourceVerificationRecord(TimestampMixin, Base):
    __tablename__ = "source_verification_records"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    reference_id: Mapped[uuid.UUID | None] = fk("references.id", nullable=True)
    publication_id: Mapped[uuid.UUID | None] = fk("publications.id", nullable=True)
    document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True)
    check_type: Mapped[str] = mapped_column(String(40))  # doi_resolve|metadata_match|title_search|claim_alignment
    status: Mapped[str] = mapped_column(String(30))  # verified|mismatch|not_found|error|unverifiable
    source: Mapped[str] = mapped_column(String(40), default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------------------
# Manuscripts
# ---------------------------------------------------------------------------
class Manuscript(TimestampMixin, Base):
    __tablename__ = "manuscripts"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    title: Mapped[str] = mapped_column(Text)
    manuscript_type: Mapped[str] = mapped_column(String(60), default="empirical_article")
    discipline: Mapped[str] = mapped_column(String(200), default="")
    target_journal: Mapped[str] = mapped_column(Text, default="")
    target_word_count: Mapped[int] = mapped_column(Integer, default=7000)
    count_references_in_words: Mapped[bool] = mapped_column(Boolean, default=False)
    citation_style: Mapped[str] = mapped_column(String(30), default="apa7")
    language_variant: Mapped[str] = mapped_column(String(10), default="en-GB")
    mode: Mapped[str] = mapped_column(String(20), default="guided")  # guided | complete
    requirements: Mapped[dict] = mapped_column(JSONB, default=dict)
    writing_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("writing_profiles.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="planning")
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_report: Mapped[dict] = mapped_column(JSONB, default=dict)

    sections: Mapped[list[ManuscriptSection]] = relationship(
        back_populates="manuscript", cascade="all, delete-orphan", order_by="ManuscriptSection.position"
    )


class ManuscriptSection(TimestampMixin, Base):
    __tablename__ = "manuscript_sections"
    __table_args__ = (UniqueConstraint("manuscript_id", "key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    manuscript_id: Mapped[uuid.UUID] = fk("manuscripts.id")
    key: Mapped[str] = mapped_column(String(60))
    heading: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    target_words: Mapped[int] = mapped_column(Integer, default=0)
    objective: Mapped[str] = mapped_column(Text, default="")
    outline: Mapped[list] = mapped_column(JSONB, default=list)  # [{heading, points:[], target_words}]
    evidence_plan: Mapped[list] = mapped_column(JSONB, default=list)
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="planned")  # planned|generating|draft|approved|needs_revision
    origin: Mapped[str] = mapped_column(String(20), default="ai")  # ai | researcher | mixed
    outstanding_requirements: Mapped[list] = mapped_column(JSONB, default=list)
    evidence_used: Mapped[list] = mapped_column(JSONB, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_version: Mapped[int] = mapped_column(Integer, default=0)

    manuscript: Mapped[Manuscript] = relationship(back_populates="sections")


class ManuscriptVersion(TimestampMixin, Base):
    __tablename__ = "manuscript_versions"
    __table_args__ = (UniqueConstraint("manuscript_id", "version_no"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    manuscript_id: Mapped[uuid.UUID] = fk("manuscripts.id")
    version_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(300), default="")
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    total_words: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(20), default="researcher")


# ---------------------------------------------------------------------------
# Background jobs & agents
# ---------------------------------------------------------------------------
class GenerationJob(TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (Index("ix_generation_jobs_status_created", "status", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    manuscript_id: Mapped[uuid.UUID | None] = fk("manuscripts.id", nullable=True)
    job_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.queued.value)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    locked_by: Mapped[str] = mapped_column(String(100), default="")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[GenerationJobStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="GenerationJobStep.position"
    )


class GenerationJobStep(TimestampMixin, Base):
    __tablename__ = "generation_job_steps"
    __table_args__ = (UniqueConstraint("job_id", "step_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = fk("generation_jobs.id")
    step_key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|succeeded|failed|skipped
    output: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[GenerationJob] = relationship(back_populates="steps")


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    job_id: Mapped[uuid.UUID | None] = fk("generation_jobs.id", nullable=True, ondelete="SET NULL")
    agent: Mapped[str] = mapped_column(String(50))
    task: Mapped[str] = mapped_column(String(100), default="")
    input_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    output: Mapped[dict] = mapped_column(JSONB, default=dict)
    provider: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="succeeded")
    error: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    excluded_private_sources: Mapped[int] = mapped_column(Integer, default=0)


class WorkspaceThread(TimestampMixin, Base):
    __tablename__ = "workspace_threads"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True)
    title: Mapped[str] = mapped_column(Text, default="New conversation")


class WorkspaceMessage(TimestampMixin, Base):
    __tablename__ = "workspace_messages"
    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = fk("workspace_threads.id")
    role: Mapped[str] = mapped_column(String(20))  # researcher | agent
    agent: Mapped[str] = mapped_column(String(50), default="")
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    knowledge_category: Mapped[str] = mapped_column(String(40), default="")
    # Researcher statements are NOT knowledge until promoted to a note/decision.
    promoted_to: Mapped[dict] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------------------
# Data analysis
# ---------------------------------------------------------------------------
class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="tabular")  # tabular | text
    storage_key: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, default="")
    file_format: Mapped[str] = mapped_column(String(20), default="csv")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    n_rows: Mapped[int | None] = mapped_column(Integer)
    n_cols: Mapped[int | None] = mapped_column(Integer)
    schema: Mapped[list] = mapped_column(JSONB, default=list)  # [{name, dtype, n_missing, n_unique, label}]
    parent_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL")
    )
    transformations: Mapped[list] = mapped_column(JSONB, default=list)
    approved_by_researcher: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")


class ResearchAnalysis(TimestampMixin, Base):
    __tablename__ = "research_analyses"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID | None] = fk("research_projects.id", nullable=True, ondelete="SET NULL")
    dataset_id: Mapped[uuid.UUID] = fk("datasets.id")
    procedure: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    assumptions: Mapped[list] = mapped_column(JSONB, default=list)
    interpretation: Mapped[str] = mapped_column(Text, default="")  # deterministic explanation
    ai_interpretation: Mapped[str] = mapped_column(Text, default="")  # labelled separately
    error: Mapped[str] = mapped_column(Text, default="")
    ran_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reproduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reproducible: Mapped[bool | None] = mapped_column(Boolean)


class AnalysisScript(TimestampMixin, Base):
    __tablename__ = "analysis_scripts"
    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_id: Mapped[uuid.UUID] = fk("research_analyses.id")
    language: Mapped[str] = mapped_column(String(20), default="python")
    code: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))
    runtime_info: Mapped[dict] = mapped_column(JSONB, default=dict)


class AnalysisOutput(TimestampMixin, Base):
    __tablename__ = "analysis_outputs"
    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_id: Mapped[uuid.UUID] = fk("research_analyses.id")
    kind: Mapped[str] = mapped_column(String(20))  # table | figure | text | json
    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[Any] = mapped_column(JSONB, default=dict)
    storage_key: Mapped[str] = mapped_column(Text, default="")
    output_hash: Mapped[str] = mapped_column(String(64), default="")


class QualCode(TimestampMixin, Base):
    __tablename__ = "qual_codes"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    theme: Mapped[str] = mapped_column(String(200), default="")
    origin: Mapped[str] = mapped_column(String(20), default="researcher")
    status: Mapped[str] = mapped_column(String(20), default="approved")  # AI suggestions start 'proposed'
    color: Mapped[str] = mapped_column(String(20), default="")


class QualSegment(TimestampMixin, Base):
    __tablename__ = "qual_segments"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    project_id: Mapped[uuid.UUID] = fk("research_projects.id")
    dataset_id: Mapped[uuid.UUID | None] = fk("datasets.id", nullable=True)
    document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True)
    source_label: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    memo: Mapped[str] = mapped_column(Text, default="")


class QualCoding(TimestampMixin, Base):
    __tablename__ = "qual_codings"
    __table_args__ = (UniqueConstraint("segment_id", "code_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    segment_id: Mapped[uuid.UUID] = fk("qual_segments.id")
    code_id: Mapped[uuid.UUID] = fk("qual_codes.id")
    origin: Mapped[str] = mapped_column(String(20), default="researcher")
    status: Mapped[str] = mapped_column(String(20), default="approved")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_user_created", "user_id", "created_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


__all__ = [name for name in dir() if not name.startswith("_")]
_ = ARRAY  # imported for downstream migrations
