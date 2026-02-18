from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db import db_session
from app.api.schemas import (
    # campaigns
    CampaignCreateRequest,
    CampaignPatchRequest,
    CampaignResponse,
    CampaignListResponse,
    # workspaces
    WorkspaceCreateRequest,
    WorkspacePatchRequest,
    WorkspaceResponse,
    WorkspaceListResponse,
    # sources
    SourceCreateRequest,
    SourcePatchRequest,
    SourceResponse,
    SourceListResponse,
    SourceFolderResponse,
    SourceFileResponse,
    SourceFileListResponse,
    # kb update / ingest
    KBUpdateRequest,
    KBUpdateResponse,
    IngestRunResponse,
    IngestRunListResponse,
    IngestRunFileResponse,
    # docs
    DocumentResponse,
    DocumentListResponse,
    ChunkResponse,
    ChunkListResponse,
)
from app.kb.update import update_campaign_kb

router = APIRouter()


# ----------------------------
# Campaigns
# ----------------------------

@router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["campaigns"],
    summary="Create a campaign (1 campaign = 1 KB).",
)
def create_campaign(payload: CampaignCreateRequest):
    q = text(
        """
        INSERT INTO campaigns (name, system, description, config, embedding_model, embedding_dim)
        VALUES (:name, :system, :description, :config::jsonb, :embedding_model, :embedding_dim)
        RETURNING id, name, system, description, config, embedding_model, embedding_dim
        """
    )
    with db_session() as db:
        try:
            row = (
                db.execute(
                    q,
                    {
                        "name": payload.name,
                        "system": payload.system,
                        "description": payload.description,
                        "config": payload.config,
                        "embedding_model": payload.embedding_model,
                        "embedding_dim": payload.embedding_dim,
                    },
                )
                .mappings()
                .one()
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Campaign name already exists.")
    return dict(row)


@router.get(
    "/campaigns",
    response_model=CampaignListResponse,
    tags=["campaigns"],
    summary="List campaigns.",
)
def list_campaigns(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with db_session() as db:
        total = db.execute(text("SELECT count(*) FROM campaigns")).scalar_one()
        rows = (
            db.execute(
                text(
                    """
                    SELECT id, name, system, description, config, embedding_model, embedding_dim
                    FROM campaigns
                    ORDER BY name
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    tags=["campaigns"],
    summary="Get campaign by id.",
)
def get_campaign(campaign_id: UUID):
    with db_session() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT id, name, system, description, config, embedding_model, embedding_dim
                    FROM campaigns
                    WHERE id = :id
                    """
                ),
                {"id": str(campaign_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Campaign not found.")
    return dict(row)


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    tags=["campaigns"],
    summary="Update campaign fields.",
)
def patch_campaign(campaign_id: UUID, payload: CampaignPatchRequest):
    # Build a single UPDATE with COALESCE so we can patch safely.
    q = text(
        """
        UPDATE campaigns
        SET
          name = COALESCE(:name, name),
          system = COALESCE(:system, system),
          description = COALESCE(:description, description),
          config = COALESCE(:config::jsonb, config),
          embedding_model = COALESCE(:embedding_model, embedding_model),
          embedding_dim = COALESCE(:embedding_dim, embedding_dim)
        WHERE id = :id
        RETURNING id, name, system, description, config, embedding_model, embedding_dim
        """
    )
    with db_session() as db:
        try:
            row = (
                db.execute(
                    q,
                    {
                        "id": str(campaign_id),
                        "name": payload.name,
                        "system": payload.system,
                        "description": payload.description,
                        "config": payload.config,
                        "embedding_model": payload.embedding_model,
                        "embedding_dim": payload.embedding_dim,
                    },
                )
                .mappings()
                .first()
            )
            if not row:
                db.rollback()
                raise HTTPException(status_code=404, detail="Campaign not found.")
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Update violates a uniqueness constraint.")
    return dict(row)


@router.delete(
    "/campaigns/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["campaigns"],
    summary="Delete a campaign (may fail if FK constraints exist).",
)
def delete_campaign(campaign_id: UUID):
    with db_session() as db:
        row = db.execute(text("DELETE FROM campaigns WHERE id = :id RETURNING id"), {"id": str(campaign_id)}).scalar()
        if not row:
            db.rollback()
            raise HTTPException(status_code=404, detail="Campaign not found.")
        db.commit()
    return None


# ----------------------------
# Workspaces
# ----------------------------

@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
    summary="Create a workspace (chat topic) under a campaign.",
)
def create_workspace(payload: WorkspaceCreateRequest):
    with db_session() as db:
        exists = db.execute(text("SELECT 1 FROM campaigns WHERE id = :id"), {"id": str(payload.campaign_id)}).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail="Campaign not found.")

        try:
            row = (
                db.execute(
                    text(
                        """
                        INSERT INTO workspaces (campaign_id, name, purpose, state)
                        VALUES (:campaign_id, :name, :purpose, :state::jsonb)
                        RETURNING id, campaign_id, name, purpose, state
                        """
                    ),
                    {
                        "campaign_id": str(payload.campaign_id),
                        "name": payload.name,
                        "purpose": payload.purpose,
                        "state": payload.state,
                    },
                )
                .mappings()
                .one()
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Workspace name already exists for this campaign.")
    return dict(row)


@router.get(
    "/workspaces",
    response_model=WorkspaceListResponse,
    tags=["workspaces"],
    summary="List workspaces (optionally filtered by campaign).",
)
def list_workspaces(
    campaign_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with db_session() as db:
        if campaign_id:
            total = db.execute(
                text("SELECT count(*) FROM workspaces WHERE campaign_id = :cid"),
                {"cid": str(campaign_id)},
            ).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, name, purpose, state
                        FROM workspaces
                        WHERE campaign_id = :cid
                        ORDER BY name
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"cid": str(campaign_id), "limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
        else:
            total = db.execute(text("SELECT count(*) FROM workspaces")).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, name, purpose, state
                        FROM workspaces
                        ORDER BY name
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )

    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    tags=["workspaces"],
    summary="Get workspace by id.",
)
def get_workspace(workspace_id: UUID):
    with db_session() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT id, campaign_id, name, purpose, state
                    FROM workspaces
                    WHERE id = :id
                    """
                ),
                {"id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Workspace not found.")
    return dict(row)


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    tags=["workspaces"],
    summary="Update workspace fields.",
)
def patch_workspace(workspace_id: UUID, payload: WorkspacePatchRequest):
    q = text(
        """
        UPDATE workspaces
        SET
          name = COALESCE(:name, name),
          purpose = COALESCE(:purpose, purpose),
          state = COALESCE(:state::jsonb, state)
        WHERE id = :id
        RETURNING id, campaign_id, name, purpose, state
        """
    )
    with db_session() as db:
        try:
            row = (
                db.execute(
                    q,
                    {"id": str(workspace_id), "name": payload.name, "purpose": payload.purpose, "state": payload.state},
                )
                .mappings()
                .first()
            )
            if not row:
                db.rollback()
                raise HTTPException(status_code=404, detail="Workspace not found.")
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Update violates a uniqueness constraint.")
    return dict(row)


@router.delete(
    "/workspaces/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workspaces"],
    summary="Delete a workspace (may fail if FK constraints exist).",
)
def delete_workspace(workspace_id: UUID):
    with db_session() as db:
        row = db.execute(text("DELETE FROM workspaces WHERE id = :id RETURNING id"), {"id": str(workspace_id)}).scalar()
        if not row:
            db.rollback()
            raise HTTPException(status_code=404, detail="Workspace not found.")
        db.commit()
    return None


# ----------------------------
# Sources (campaign_sources)
# ----------------------------

@router.post(
    "/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sources"],
    summary="Create a source folder configuration for a campaign.",
)
def create_source(payload: SourceCreateRequest):
    with db_session() as db:
        exists = db.execute(text("SELECT 1 FROM campaigns WHERE id = :id"), {"id": str(payload.campaign_id)}).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        try:
            row = (
                db.execute(
                    text(
                        """
                        INSERT INTO campaign_sources (
                          campaign_id, name, kind, root_path, recursive, follow_symlinks,
                          include_globs, exclude_globs, change_detection, enabled
                        )
                        VALUES (
                          :campaign_id, :name, :kind, :root_path, :recursive, :follow_symlinks,
                          :include_globs::jsonb, :exclude_globs::jsonb, :change_detection, :enabled
                        )
                        RETURNING id, campaign_id, name, kind, root_path, recursive, follow_symlinks,
                                  include_globs, exclude_globs, change_detection, enabled, last_scan_at, last_ingest_at
                        """
                    ),
                    {
                        "campaign_id": str(payload.campaign_id),
                        "name": payload.name,
                        "kind": payload.kind,
                        "root_path": payload.root_path,
                        "recursive": payload.recursive,
                        "follow_symlinks": payload.follow_symlinks,
                        "include_globs": payload.include_globs,
                        "exclude_globs": payload.exclude_globs,
                        "change_detection": payload.change_detection,
                        "enabled": payload.enabled,
                    },
                )
                .mappings()
                .one()
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Source name already exists for this campaign.")
    return dict(row)


@router.get(
    "/sources",
    response_model=SourceListResponse,
    tags=["sources"],
    summary="List sources (optionally filtered by campaign).",
)
def list_sources(
    campaign_id: Optional[UUID] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with db_session() as db:
        if campaign_id:
            total = db.execute(
                text("SELECT count(*) FROM campaign_sources WHERE campaign_id = :cid"),
                {"cid": str(campaign_id)},
            ).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, name, kind, root_path, recursive, follow_symlinks,
                               include_globs, exclude_globs, change_detection, enabled, last_scan_at, last_ingest_at
                        FROM campaign_sources
                        WHERE campaign_id = :cid
                        ORDER BY name
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"cid": str(campaign_id), "limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
        else:
            total = db.execute(text("SELECT count(*) FROM campaign_sources")).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, name, kind, root_path, recursive, follow_symlinks,
                               include_globs, exclude_globs, change_detection, enabled, last_scan_at, last_ingest_at
                        FROM campaign_sources
                        ORDER BY name
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get(
    "/sources/{source_id}",
    response_model=SourceResponse,
    tags=["sources"],
    summary="Get source by id.",
)
def get_source(source_id: UUID):
    with db_session() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT id, campaign_id, name, kind, root_path, recursive, follow_symlinks,
                           include_globs, exclude_globs, change_detection, enabled, last_scan_at, last_ingest_at
                    FROM campaign_sources
                    WHERE id = :id
                    """
                ),
                {"id": str(source_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Source not found.")
    return dict(row)


@router.patch(
    "/sources/{source_id}",
    response_model=SourceResponse,
    tags=["sources"],
    summary="Update a source configuration.",
)
def patch_source(source_id: UUID, payload: SourcePatchRequest):
    q = text(
        """
        UPDATE campaign_sources
        SET
          name = COALESCE(:name, name),
          kind = COALESCE(:kind, kind),
          root_path = COALESCE(:root_path, root_path),
          recursive = COALESCE(:recursive, recursive),
          follow_symlinks = COALESCE(:follow_symlinks, follow_symlinks),
          include_globs = COALESCE(:include_globs::jsonb, include_globs),
          exclude_globs = COALESCE(:exclude_globs::jsonb, exclude_globs),
          change_detection = COALESCE(:change_detection, change_detection),
          enabled = COALESCE(:enabled, enabled)
        WHERE id = :id
        RETURNING id, campaign_id, name, kind, root_path, recursive, follow_symlinks,
                  include_globs, exclude_globs, change_detection, enabled, last_scan_at, last_ingest_at
        """
    )
    with db_session() as db:
        try:
            row = (
                db.execute(
                    q,
                    {
                        "id": str(source_id),
                        "name": payload.name,
                        "kind": payload.kind,
                        "root_path": payload.root_path,
                        "recursive": payload.recursive,
                        "follow_symlinks": payload.follow_symlinks,
                        "include_globs": payload.include_globs,
                        "exclude_globs": payload.exclude_globs,
                        "change_detection": payload.change_detection,
                        "enabled": payload.enabled,
                    },
                )
                .mappings()
                .first()
            )
            if not row:
                db.rollback()
                raise HTTPException(status_code=404, detail="Source not found.")
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Update violates a uniqueness constraint.")
    return dict(row)


@router.delete(
    "/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["sources"],
    summary="Delete a source configuration (may fail if FK constraints exist).",
)
def delete_source(source_id: UUID):
    with db_session() as db:
        row = db.execute(text("DELETE FROM campaign_sources WHERE id = :id RETURNING id"), {"id": str(source_id)}).scalar()
        if not row:
            db.rollback()
            raise HTTPException(status_code=404, detail="Source not found.")
        db.commit()
    return None


@router.get(
    "/sources/{source_id}/folders",
    response_model=list[SourceFolderResponse],
    tags=["sources"],
    summary="List folders discovered for a source (from last scans).",
)
def list_source_folders(source_id: UUID):
    with db_session() as db:
        rows = (
            db.execute(
                text(
                    """
                    SELECT id, source_id, rel_path, parent_id, depth, last_seen_at
                    FROM source_folders
                    WHERE source_id = :sid
                    ORDER BY depth, rel_path
                    """
                ),
                {"sid": str(source_id)},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.get(
    "/sources/{source_id}/files",
    response_model=SourceFileListResponse,
    tags=["sources"],
    summary="List files discovered for a source (with ingest status).",
)
def list_source_files(
    source_id: UUID,
    status_filter: Optional[str] = Query(None, description="Filter by status: seen|deleted|..."),
    ingest_status: Optional[str] = Query(None, description="Filter by last_ingest_status: ok|error|skipped|never"),
    q: Optional[str] = Query(None, description="Substring match on rel_path."),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    where = ["source_id = :sid"]
    params = {"sid": str(source_id), "limit": limit, "offset": offset}
    if status_filter:
        where.append("status = :status")
        params["status"] = status_filter
    if ingest_status:
        where.append("last_ingest_status = :lst")
        params["lst"] = ingest_status
    if q:
        where.append("rel_path ILIKE :q")
        params["q"] = f"%{q}%"

    where_sql = " AND ".join(where)
    with db_session() as db:
        total = db.execute(text(f"SELECT count(*) FROM source_files WHERE {where_sql}"), params).scalar_one()
        rows = (
            db.execute(
                text(
                    f"""
                    SELECT id, source_id, folder_id, rel_path, ext, size_bytes, mtime_epoch, sha256,
                           status, last_seen_at, last_ingested_at, last_ingest_status, error
                    FROM source_files
                    WHERE {where_sql}
                    ORDER BY rel_path
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


# ----------------------------
# KB update
# ----------------------------

@router.post(
    "/kb/update",
    response_model=KBUpdateResponse,
    status_code=status.HTTP_200_OK,
    tags=["kb"],
    summary="Scan configured sources, detect changes, and ingest updated files into the campaign KB.",
)
def kb_update(payload: KBUpdateRequest):
    try:
        result = update_campaign_kb(
            campaign_id=str(payload.campaign_id),
            dry_run=payload.dry_run,
            force_rehash=payload.force_rehash,
            max_files=payload.max_files,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------
# Ingest history
# ----------------------------

@router.get(
    "/ingest/runs",
    response_model=IngestRunListResponse,
    tags=["ingest"],
    summary="List ingestion runs (optionally by campaign).",
)
def list_ingest_runs(
    campaign_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with db_session() as db:
        if campaign_id:
            total = db.execute(
                text("SELECT count(*) FROM ingest_runs WHERE campaign_id = :cid"),
                {"cid": str(campaign_id)},
            ).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, trigger, status, started_at, finished_at, stats, error
                        FROM ingest_runs
                        WHERE campaign_id = :cid
                        ORDER BY started_at DESC NULLS LAST, id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"cid": str(campaign_id), "limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
        else:
            total = db.execute(text("SELECT count(*) FROM ingest_runs")).scalar_one()
            rows = (
                db.execute(
                    text(
                        """
                        SELECT id, campaign_id, trigger, status, started_at, finished_at, stats, error
                        FROM ingest_runs
                        ORDER BY started_at DESC NULLS LAST, id DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"limit": limit, "offset": offset},
                )
                .mappings()
                .all()
            )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get(
    "/ingest/runs/{run_id}",
    response_model=IngestRunResponse,
    tags=["ingest"],
    summary="Get ingestion run details.",
)
def get_ingest_run(run_id: UUID):
    with db_session() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT id, campaign_id, trigger, status, started_at, finished_at, stats, error
                    FROM ingest_runs
                    WHERE id = :id
                    """
                ),
                {"id": str(run_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Ingest run not found.")
    return dict(row)


@router.get(
    "/ingest/runs/{run_id}/files",
    response_model=list[IngestRunFileResponse],
    tags=["ingest"],
    summary="List per-file actions for an ingestion run.",
)
def list_ingest_run_files(run_id: UUID):
    with db_session() as db:
        rows = (
            db.execute(
                text(
                    """
                    SELECT ingest_run_id, source_file_id, action, status, reason, error
                    FROM ingest_run_files
                    WHERE ingest_run_id = :id
                    ORDER BY source_file_id
                    """
                ),
                {"id": str(run_id)},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ----------------------------
# KB Documents / chunks
# ----------------------------

@router.get(
    "/kb/documents",
    response_model=DocumentListResponse,
    tags=["kb"],
    summary="List documents in the campaign KB (basic filtering).",
)
def list_documents(
    campaign_id: UUID = Query(...),
    doc_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Substring match on title/body."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_body: bool = Query(False, description="If true, include body_md in results (heavier)."),
):
    where = ["campaign_id = :cid"]
    params = {"cid": str(campaign_id), "limit": limit, "offset": offset}
    if doc_type:
        where.append("doc_type = :dt")
        params["dt"] = doc_type
    if q:
        where.append("(title ILIKE :q OR body_md ILIKE :q)")
        params["q"] = f"%{q}%"
    where_sql = " AND ".join(where)

    cols = "id, campaign_id, source_file_id, doc_type, title, content_hash, created_at, updated_at"
    if include_body:
        cols = "id, campaign_id, source_file_id, doc_type, title, body_md, content_hash, created_at, updated_at"

    with db_session() as db:
        total = db.execute(text(f"SELECT count(*) FROM kb_documents WHERE {where_sql}"), params).scalar_one()
        rows = (
            db.execute(
                text(
                    f"""
                    SELECT {cols}
                    FROM kb_documents
                    WHERE {where_sql}
                    ORDER BY updated_at DESC NULLS LAST, id DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get(
    "/kb/documents/{doc_id}",
    response_model=DocumentResponse,
    tags=["kb"],
    summary="Get a document (includes body).",
)
def get_document(doc_id: UUID):
    with db_session() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT id, campaign_id, source_file_id, doc_type, title, body_md, content_hash, created_at, updated_at
                    FROM kb_documents
                    WHERE id = :id
                    """
                ),
                {"id": str(doc_id)},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Document not found.")
    return dict(row)


@router.get(
    "/kb/documents/{doc_id}/chunks",
    response_model=ChunkListResponse,
    tags=["kb"],
    summary="List chunks for a document.",
)
def list_document_chunks(
    doc_id: UUID,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    with db_session() as db:
        # verify doc exists and get campaign_id for response consistency
        doc = db.execute(text("SELECT campaign_id FROM kb_documents WHERE id = :id"), {"id": str(doc_id)}).scalar()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        total = db.execute(text("SELECT count(*) FROM kb_chunks WHERE document_id = :id"), {"id": str(doc_id)}).scalar_one()
        rows = (
            db.execute(
                text(
                    """
                    SELECT id, campaign_id, document_id, section_path, chunk_index, content, metadata
                    FROM kb_chunks
                    WHERE document_id = :id
                    ORDER BY chunk_index
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"id": str(doc_id), "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}
