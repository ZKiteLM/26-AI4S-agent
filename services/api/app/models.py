from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    abstract: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    work_id: Mapped[str | None] = mapped_column(
        ForeignKey("scholarly_works.id"), nullable=True, index=True
    )
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="upload")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Version(Base):
    __tablename__ = "paper_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    label: Mapped[str] = mapped_column(String(80), default="unknown")
    filename: Mapped[str] = mapped_column(Text)
    file_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_status: Mapped[str] = mapped_column(String(30), default="pending")
    parse_revision: Mapped[int] = mapped_column(Integer, default=1)
    content_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    sample_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Block(Base):
    __tablename__ = "document_blocks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("paper_versions.id"), index=True)
    page_index: Mapped[int] = mapped_column(Integer, index=True)
    order: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30), default="paragraph")
    text: Mapped[str] = mapped_column(Text, default="")
    markdown: Mapped[str] = mapped_column(Text, default="")
    latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[list] = mapped_column(JSON, default=list)
    section_path: Mapped[list] = mapped_column(JSON, default=list)
    parser: Mapped[str] = mapped_column(String(30), default="pymupdf")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class Chunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("paper_versions.id"), index=True)
    block_ids: Mapped[list] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UserModelConfig(Base):
    __tablename__ = "user_model_configs"
    owner_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    base_url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="chat")
    paper_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    answer: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160), default="新会话")
    paper_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Translation(Base):
    __tablename__ = "translations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    block_id: Mapped[str] = mapped_column(ForeignKey("document_blocks.id"), index=True)
    target_lang: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)


class CitationEdge(Base):
    __tablename__ = "citation_edges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    citing_paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    cited_paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class ScholarlyWork(Base):
    __tablename__ = "scholarly_works"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstract: Mapped[str] = mapped_column(Text, default="")
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    openalex_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    s2_paper_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_source: Mapped[str] = mapped_column(String(40), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScholarlyWorkAlias(Base):
    __tablename__ = "scholarly_work_aliases"
    __table_args__ = (UniqueConstraint("namespace", "normalized_value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(30))
    normalized_value: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScholarlyAliasConflict(Base):
    __tablename__ = "scholarly_alias_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(30))
    normalized_value: Mapped[str] = mapped_column(String(255))
    existing_work_id: Mapped[str] = mapped_column(String(36))
    incoming_work_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScholarlyCitationEdge(Base):
    __tablename__ = "scholarly_citation_edges"
    __table_args__ = (UniqueConstraint("citing_work_id", "cited_work_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    citing_work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    cited_work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CitationFetchState(Base):
    __tablename__ = "citation_fetch_state"
    __table_args__ = (UniqueConstraint("work_id", "provider", "direction"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="not_fetched")
    next_cursor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class GraphView(Base):
    __tablename__ = "graph_views"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GraphInsight(Base):
    __tablename__ = "graph_insights"
    __table_args__ = (UniqueConstraint("graph_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_views.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    graph_version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GraphReviewRun(Base):
    __tablename__ = "graph_review_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_views.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("background_tasks.id"), unique=True)
    graph_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    phase: Mapped[str] = mapped_column(String(20), default="planning")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    outline: Mapped[dict] = mapped_column(JSON, default=dict)
    sections: Mapped[list] = mapped_column(JSON, default=list)
    verification: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GraphReviewEvidence(Base):
    __tablename__ = "graph_review_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("graph_review_runs.id"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    paper_id: Mapped[str | None] = mapped_column(String(36))
    version_id: Mapped[str | None] = mapped_column(String(36))
    block_id: Mapped[str | None] = mapped_column(String(36))
    source_type: Mapped[str] = mapped_column(String(20))
    page_index: Mapped[int | None] = mapped_column(Integer)
    start: Mapped[int | None] = mapped_column(Integer)
    length: Mapped[int] = mapped_column(Integer, default=1)
    bbox: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str] = mapped_column(Text)


class GraphViewNode(Base):
    __tablename__ = "graph_view_nodes"
    __table_args__ = (UniqueConstraint("graph_id", "work_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_views.id"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("scholarly_works.id"), index=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    expanded_references: Mapped[bool] = mapped_column(Boolean, default=False)
    expanded_citations: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    type: Mapped[str] = mapped_column(String(40))
    file_key: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    topic: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    last_auto_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_status: Mapped[str] = mapped_column(String(20), default="idle")
    refresh_token: Mapped[str | None] = mapped_column(String(36))
    refresh_mode: Mapped[str | None] = mapped_column(String(20))
    refresh_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_error: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperCandidate(Base):
    __tablename__ = "paper_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title_key: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    abstract: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    github_verification: Mapped[str | None] = mapped_column(String(20), nullable=True)
    github_stars_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    code_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_check_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    citation_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    citation_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    citation_check_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("owner_id", "candidate_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("paper_candidates.id"), index=True)
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.id"), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    feedback: Mapped[int] = mapped_column(Integer, default=0)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    matched_keywords: Mapped[dict] = mapped_column(JSON, default=dict)
    topic_analyses: Mapped[dict] = mapped_column(JSON, default=dict)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
