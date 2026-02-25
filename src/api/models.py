"""Pydantic models for the REST API."""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class ScanRequest(BaseModel):
    path: str


class QueryRequest(BaseModel):
    query: str
    send_to_cloud: bool = True
    unmask_response: bool = False


class PreviewResponse(BaseModel):
    masked_prompt: str
    estimated_tokens: int
    files_included: int
    files_truncated: int
    masking_log: list[dict]


class QueryResponse(BaseModel):
    query: str
    response: str
    estimated_tokens: int
    files_included: int
    masking_count: int
    cost_estimate: Optional[dict] = None
    local_llm_used: bool = False


class ProjectInfo(BaseModel):
    root: str
    total_files: int
    skipped_files: int
    extensions: dict[str, int]
    total_size_kb: int
    file_tree: str


class MaskingLogResponse(BaseModel):
    entries: list[dict]
    total: int


class StatusResponse(BaseModel):
    status: str
    project_loaded: bool
    local_llm_available: bool
    cloud_llm_configured: bool
    provider: str
    model: str
