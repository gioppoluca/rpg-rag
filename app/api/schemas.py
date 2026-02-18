from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ----------------------------
# Campaigns
# ----------------------------

class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Unique campaign name.")
    system: str = Field(..., min_length=1, max_length=50, description="Game system, e.g. 'dnd5e' or 'v5'.")
    description: Optional[str] = Field(None, max_length=2000)
    config: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary JSON config.")
    embedding_model: str = Field(default="nomic-embed-text")
    embedding_dim: int = Field(default=768, ge=64, le=8192)


class CampaignPatchRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    system: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=2000)
    config: Optional[Dict[str, Any]] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = Field(None, ge=64, le=8192)


class CampaignResponse(BaseModel):
    id: UUID
    name: str
    system: str
    description: Optional[str] = None
    config: Dict[str, Any]
    embedding_model: str
    embedding_dim: int


class CampaignListResponse(BaseModel):
    items: List[CampaignResponse]
    total: int
    limit: int
    offset: int


# ----------------------------
# Workspaces
# ----------------------------

class WorkspaceCreateRequest(BaseModel):
    campaign_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    purpose: Optional[str] = Field(None, max_length=2000)
    state: Dict[str, Any] = Field(default_factory=dict)


class WorkspacePatchRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    purpose: Optional[str] = Field(None, max_length=2000)
    state: Optional[Dict[str, Any]] = None


class WorkspaceResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    name: str
    purpose: Optional[str] = None
    state: Dict[str, Any]


class WorkspaceListResponse(BaseModel):
    items: List[WorkspaceResponse]
    total: int
    limit: int
    offset: int


# ----------------------------
# Sources
# ----------------------------

class SourceCreateRequest(BaseModel):
    campaign_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(default="folder", max_length=50, description="Source kind: folder/md/pdf/etc.")
    root_path: str = Field(..., min_length=1, max_length=2000)
    recursive: bool = True
    follow_symlinks: bool = False
    include_globs: List[str] = Field(default_factory=list, description="POSIX globs, evaluated on relative path.")
    exclude_globs: List[str] = Field(default_factory=list)
    change_detection: str = Field(default="auto", description="mtime|sha256|auto")
    enabled: bool = True


class SourcePatchRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[str] = Field(None, max_length=50)
    root_path: Optional[str] = Field(None, min_length=1, max_length=2000)
    recursive: Optional[bool] = None
    follow_symlinks: Optional[bool] = None
    include_globs: Optional[List[str]] = None
    exclude_globs: Optional[List[str]] = None
    change_detection: Optional[str] = None
    enabled: Optional[bool] = None


class SourceResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    name: str
    kind: str
    root_path: str
    recursive: bool
    follow_symlinks: bool
    include_globs: List[str]
    exclude_globs: List[str]
    change_detection: str
    enabled: bool
    last_scan_at: Optional[datetime] = None
    last_ingest_at: Optional[datetime] = None


class SourceListResponse(BaseModel):
    items: List[SourceResponse]
    total: int
    limit: int
    offset: int


class SourceFolderResponse(BaseModel):
    id: UUID
    source_id: UUID
    rel_path: str
    parent_id: Optional[UUID] = None
    depth: int
    last_seen_at: Optional[datetime] = None


class SourceFileResponse(BaseModel):
    id: UUID
    source_id: UUID
    folder_id: Optional[UUID] = None
    rel_path: str
    ext: Optional[str] = None
    size_bytes: Optional[int] = None
    mtime_epoch: Optional[int] = None
    sha256: Optional[str] = None
    status: str
    last_seen_at: Optional[datetime] = None
    last_ingested_at: Optional[datetime] = None
    last_ingest_status: Optional[str] = None
    error: Optional[str] = None


class SourceFileListResponse(BaseModel):
    items: List[SourceFileResponse]
    total: int
    limit: int
    offset: int


# ----------------------------
# KB Update / Ingest runs
# ----------------------------

class KBUpdateRequest(BaseModel):
    campaign_id: UUID
    dry_run: bool = Field(default=False, description="If true, scan and compute changes without ingesting documents.")
    force_rehash: bool = Field(default=False, description="If true, recompute hashes even if mtime/size unchanged.")
    max_files: Optional[int] = Field(default=None, ge=1, description="Optional cap of files to ingest in this run.")


class KBUpdateResponse(BaseModel):
    ingest_run_id: UUID
    status: str
    stats: Dict[str, Any]


class IngestRunResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    trigger: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    stats: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class IngestRunListResponse(BaseModel):
    items: List[IngestRunResponse]
    total: int
    limit: int
    offset: int


class IngestRunFileResponse(BaseModel):
    ingest_run_id: UUID
    source_file_id: UUID
    action: str
    status: str
    reason: Optional[str] = None
    error: Optional[str] = None


# ----------------------------
# KB Documents / Chunks
# ----------------------------

class DocumentResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    source_file_id: Optional[UUID] = None
    doc_type: str
    title: str
    body_md: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    limit: int
    offset: int


class ChunkResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    document_id: UUID
    section_path: str
    chunk_index: int
    content: str
    metadata: Dict[str, Any]


class ChunkListResponse(BaseModel):
    items: List[ChunkResponse]
    total: int
    limit: int
    offset: int
