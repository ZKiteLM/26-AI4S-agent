"""Validated request payloads for the public API."""

from typing import Literal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class ModelConfigIn(BaseModel):
    provider: Literal["openai", "ohmygpt", "ollama"]
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=255)
    api_key: str = Field(default="", max_length=2000)


class ChatIn(BaseModel):
    question: str = Field(min_length=2)
    paper_ids: list[str] = Field(min_length=1)


class ChatSessionIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)


class ChatMessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=8000)
    paper_ids: list[str] | None = None


class ResolveIn(BaseModel):
    resolution: str


class TranslateIn(BaseModel):
    version_id: str | None = None
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    text: str | None = None
    target_lang: str = "zh"


class SearchIn(BaseModel):
    query: str = Field(min_length=2)
    limit: int = Field(default=10, ge=1, le=25)


class ImportIn(BaseModel):
    openalex_id: str | None = None
    arxiv_id: str | None = None


class GraphIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    external_seeds: list[dict[str, str]] = Field(default_factory=list, max_length=5)
    mode: str = "explore"
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class GraphExpandIn(BaseModel):
    work_id: str
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class GraphRefreshIn(BaseModel):
    work_id: str
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class DraftIn(BaseModel):
    idea: str = Field(default="", max_length=5000)
    fields: dict | None = None
    graph_id: str | None = None
    idea_index: int | None = Field(default=None, ge=0)


class DraftAnalyzeIn(BaseModel):
    idea: str = Field(min_length=10, max_length=5000)


class DraftCompleteIn(DraftAnalyzeIn):
    fields: dict


class DiagramIn(BaseModel):
    description: str = Field(min_length=5, max_length=5000)
    previous: str = Field(default="", max_length=20000)
    error: str = Field(default="", max_length=1000)


class ChartCaptionIn(BaseModel):
    data_artifact_id: str
    spec: dict


class SubscriptionIn(BaseModel):
    topic: str = Field(min_length=2, max_length=255)
    keywords: list[str] | None = None


class SubscriptionUpdateIn(BaseModel):
    active: bool | None = None
    keywords: list[str] | None = None


class FeedbackIn(BaseModel):
    value: int = Field(ge=-1, le=1)
