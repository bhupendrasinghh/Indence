"""SQLAlchemy models for the target PostgreSQL schema.

Fully maps all required tables for users, sessions, corpus revisions, chunks,
evidence cards, query runs, traces, and feedback.
Uses SQLAlchemy 2.0 syntax.
"""

from __future__ import annotations

import datetime
from typing import Any
from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    Integer,
    Boolean,
    Float,
    DateTime,
    JSON,
    Enum,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[str]: JSON,
    }


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="clinician")  # clinician | researcher | admin
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | active | suspended
    email_verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(nullable=False, unique=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column()
    ip_hash: Mapped[bytes | None] = mapped_column()
    user_agent_hash: Mapped[bytes | None] = mapped_column()
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # pubmed | pmc | guideline_org
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    pmid: Mapped[str | None] = mapped_column(String(50), index=True)
    pmcid: Mapped[str | None] = mapped_column(String(50), index=True)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publication_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    retraction_status: Mapped[str] = mapped_column(String(50), default="not_retracted")  # not_retracted | retracted
    display_rights: Mapped[bool] = mapped_column(Boolean, default=True)
    mesh_terms: Mapped[list[str]] = mapped_column(default=list)

    __table_args__ = (
        UniqueConstraint("source", "source_key", name="uq_source_source_key"),
    )


class CorpusSnapshot(Base):
    __tablename__ = "corpus_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | active | superseded
    active_index_name: Mapped[str | None] = mapped_column(String(255))
    retrieval_profile_version: Mapped[str] = mapped_column(String(50))
    published_at: Mapped[datetime.datetime | None] = mapped_column()
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class DocumentRevision(Base):
    __tablename__ = "document_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False)
    corpus_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("corpus_snapshots.id", ondelete="SET NULL"))
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    study_type: Mapped[str] = mapped_column(String(50), nullable=False)  # rct | meta_analysis | systematic_review | guideline
    metadata_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(50), default="indexed")  # indexed | published | superseded

    __table_args__ = (
        UniqueConstraint("document_id", "revision_no", name="uq_document_revision"),
    )


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False)
    section_path: Mapped[list[str]] = mapped_column(nullable=False)
    section_kind: Mapped[str] = mapped_column(String(50), nullable=False)  # introduction | methods | results | etc.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "ordinal", name="uq_section_ordinal"),
    )


class EvidenceUnit(Base):
    __tablename__ = "evidence_units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # custom ID like E01_P01
    section_id: Mapped[str] = mapped_column(ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), default="prose")  # prose | table | recommendation
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # custom ID like E01_P01_C01
    evidence_unit_id: Mapped[str] = mapped_column(ForeignKey("evidence_units.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_version: Mapped[str | None] = mapped_column(String(50))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(36))
    index_status: Mapped[str] = mapped_column(String(50), default="pending")  # pending | indexed | failed

    __table_args__ = (
        UniqueConstraint("evidence_unit_id", "ordinal", name="uq_chunk_ordinal"),
    )


class EvidenceCardData(Base):
    __tablename__ = "evidence_card_data"

    revision_id: Mapped[str] = mapped_column(ForeignKey("document_revisions.id", ondelete="CASCADE"), primary_key=True)
    fields_json: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(50), nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_run_id: Mapped[str | None] = mapped_column(String(100))
    manifest_checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(50), default="running")  # running | succeeded | failed
    started_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    finished_at: Mapped[datetime.datetime | None] = mapped_column()


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)  # parse | embed | index
    status: Mapped[str] = mapped_column(String(50), default="queued")  # queued | leased | succeeded | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow, index=True)
    lease_until: Mapped[datetime.datetime | None] = mapped_column()
    error_message: Mapped[str | None] = mapped_column(Text)


class QueryRun(Base):
    __tablename__ = "query_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text)
    filters_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    corpus_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("corpus_snapshots.id"))
    status: Mapped[str] = mapped_column(String(50))  # running | answered | abstained | failed
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow, index=True)


class RetrievalTrace(Base):
    __tablename__ = "retrieval_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    query_run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id", ondelete="CASCADE"), unique=True, nullable=False)
    normalization_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_versions_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    timings_json: Mapped[dict[str, Any]] = mapped_column(default=dict)


class RetrievalCandidate(Base):
    __tablename__ = "retrieval_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(ForeignKey("retrieval_traces.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)  # dense | sparse | fused | reranked | selected
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_details: Mapped[dict[str, Any]] = mapped_column(default=dict)

    __table_args__ = (
        UniqueConstraint("trace_id", "stage", "rank", name="uq_trace_stage_rank"),
    )


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    query_run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # verifying | published | abstained | failed
    rendered_markdown: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(50))  # high | moderate | low
    generation_model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    verification_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class AnswerClaim(Base):
    __tablename__ = "answer_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50))  # direct_answer | limitations | summary
    is_material: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(50))  # pending | verified | unverified

    __table_args__ = (
        UniqueConstraint("answer_id", "ordinal", name="uq_claim_ordinal"),
    )


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("answer_claims.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    marker_index: Mapped[int] = mapped_column(Integer, nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(50))  # verified | unverified
    supporting_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("claim_id", "chunk_id", name="uq_claim_chunk_citation"),
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), nullable=False)
    citation_id: Mapped[str | None] = mapped_column(ForeignKey("citations.id", ondelete="SET NULL"))
    vote: Mapped[str] = mapped_column(String(10), nullable=False)  # up | down
    reason_code: Mapped[str | None] = mapped_column(String(100))  # inaccurate | unsupported | etc.
    free_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
