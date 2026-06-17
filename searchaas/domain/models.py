"""
Domain models used across the platform.

Documents are assumed to be chunked and embedded BEFORE ingestion. Query
embeddings are generated dynamically (see embeddings/factory.py) and MUST
match the `embedding_model` value stored on each chunk.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceRef(BaseModel):
    document_id: str
    uri: str | None = None
    parent_id: str | None = None   # enables parent-document retrieval
    page: int | None = None


class Chunk(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    content: str
    embedding: list[float]
    embedding_model: str            # must match query-time embedding model
    metadata: dict[str, Any] = Field(default_factory=dict)
    entities: list[str] = Field(default_factory=list)
    source: SourceRef
