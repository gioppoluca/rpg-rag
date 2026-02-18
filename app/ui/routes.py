from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.templating import Jinja2Templates

from app.core.db import db_session

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(directory="app/ui/templates")


@router.get("/campaigns", response_class=HTMLResponse)
def ui_campaigns(request: Request, msg: str | None = None, error: str | None = None):
    """List campaigns with actions."""
    with db_session() as db:
        rows = (
            db.execute(
                text(
                    """
                    SELECT id, name, system, description, embedding_model, embedding_dim
                    FROM campaigns
                    ORDER BY name
                    """
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(
        "campaigns_list.html",
        {
            "request": request,
            "campaigns": [dict(r) for r in rows],
            "msg": msg,
            "error": error,
        },
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def ui_campaign_new(request: Request):
    """Create campaign form."""
    return templates.TemplateResponse(
        "campaign_form.html",
        {
            "request": request,
            "mode": "create",
            "campaign": {
                "name": "",
                "system": "v5",
                "description": "",
                "embedding_model": "nomic-embed-text",
                "embedding_dim": 768,
                "config": "{}",
            },
            "error": None,
        },
    )


@router.post("/campaigns/new")
def ui_campaign_create(
    request: Request,
    name: str = Form(...),
    system: str = Form(...),
    description: str = Form(""),
    embedding_model: str = Form("nomic-embed-text"),
    embedding_dim: int = Form(768),
    config_json: str = Form("{}"),
):
    try:
        config = json.loads(config_json or "{}")
    except Exception:
        return templates.TemplateResponse(
            "campaign_form.html",
            {
                "request": request,
                "mode": "create",
                "campaign": {
                    "name": name,
                    "system": system,
                    "description": description,
                    "embedding_model": embedding_model,
                    "embedding_dim": embedding_dim,
                    "config": config_json,
                },
                "error": "Config must be valid JSON.",
            },
            status_code=400,
        )

    q = text(
        """
        INSERT INTO campaigns (name, system, description, config, embedding_model, embedding_dim)
        -- NOTE: SQLAlchemy's text() parsing can choke on Postgres '::jsonb' shorthand.
        -- Use CAST(:config AS jsonb) instead.
        VALUES (:name, :system, :description, CAST(:config AS jsonb), :embedding_model, :embedding_dim)        RETURNING id
        """
    )

    with db_session() as db:
        try:
            db.execute(
                q,
                {
                    "name": name,
                    "system": system,
                    "description": description or None,
                    "config": json.dumps(config),
                    "embedding_model": embedding_model,
                    "embedding_dim": embedding_dim,
                },
            ).scalar_one()
            db.commit()
        except IntegrityError:
            db.rollback()
            return templates.TemplateResponse(
                "campaign_form.html",
                {
                    "request": request,
                    "mode": "create",
                    "campaign": {
                        "name": name,
                        "system": system,
                        "description": description,
                        "embedding_model": embedding_model,
                        "embedding_dim": embedding_dim,
                        "config": config_json,
                    },
                    "error": "Campaign name already exists.",
                },
                status_code=409,
            )

    return RedirectResponse(url="/ui/campaigns?msg=Campaign+created", status_code=303)


@router.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
def ui_campaign_edit(request: Request, campaign_id: UUID):
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
            raise HTTPException(status_code=404, detail="Campaign not found")

    c = dict(row)
    return templates.TemplateResponse(
        "campaign_form.html",
        {
            "request": request,
            "mode": "edit",
            "campaign_id": str(campaign_id),
            "campaign": {
                "name": c.get("name") or "",
                "system": c.get("system") or "v5",
                "description": c.get("description") or "",
                "embedding_model": c.get("embedding_model") or "nomic-embed-text",
                "embedding_dim": c.get("embedding_dim") or 768,
                "config": json.dumps(
                    c.get("config") or {}, indent=2, ensure_ascii=False
                ),
            },
            "error": None,
        },
    )


@router.post("/campaigns/{campaign_id}/edit")
def ui_campaign_update(
    request: Request,
    campaign_id: UUID,
    name: str = Form(...),
    system: str = Form(...),
    description: str = Form(""),
    embedding_model: str = Form("nomic-embed-text"),
    embedding_dim: int = Form(768),
    config_json: str = Form("{}"),
):
    try:
        config = json.loads(config_json or "{}")
    except Exception:
        return templates.TemplateResponse(
            "campaign_form.html",
            {
                "request": request,
                "mode": "edit",
                "campaign_id": str(campaign_id),
                "campaign": {
                    "name": name,
                    "system": system,
                    "description": description,
                    "embedding_model": embedding_model,
                    "embedding_dim": embedding_dim,
                    "config": config_json,
                },
                "error": "Config must be valid JSON.",
            },
            status_code=400,
        )

    q = text(
        """
        UPDATE campaigns
        SET name = :name,
            system = :system,
            description = :description,
            config = :config::jsonb,
            embedding_model = :embedding_model,
            embedding_dim = :embedding_dim
        WHERE id = :id
        RETURNING id
        """
    )

    with db_session() as db:
        try:
            updated = db.execute(
                q,
                {
                    "id": str(campaign_id),
                    "name": name,
                    "system": system,
                    "description": description or None,
                    "config": json.dumps(config),
                    "embedding_model": embedding_model,
                    "embedding_dim": embedding_dim,
                },
            ).scalar()
            if not updated:
                db.rollback()
                raise HTTPException(status_code=404, detail="Campaign not found")
            db.commit()
        except IntegrityError:
            db.rollback()
            return templates.TemplateResponse(
                "campaign_form.html",
                {
                    "request": request,
                    "mode": "edit",
                    "campaign_id": str(campaign_id),
                    "campaign": {
                        "name": name,
                        "system": system,
                        "description": description,
                        "embedding_model": embedding_model,
                        "embedding_dim": embedding_dim,
                        "config": config_json,
                    },
                    "error": "Update violates uniqueness constraint (campaign name).",
                },
                status_code=409,
            )

    return RedirectResponse(url="/ui/campaigns?msg=Campaign+updated", status_code=303)


@router.post("/campaigns/{campaign_id}/delete")
def ui_campaign_delete(campaign_id: UUID):
    with db_session() as db:
        try:
            deleted = db.execute(
                text("DELETE FROM campaigns WHERE id = :id RETURNING id"),
                {"id": str(campaign_id)},
            ).scalar()
            if not deleted:
                db.rollback()
                return RedirectResponse(
                    url="/ui/campaigns?error=Campaign+not+found", status_code=303
                )
            db.commit()
        except Exception:
            db.rollback()
            # likely FK constraint
            return RedirectResponse(
                url="/ui/campaigns?error=Cannot+delete+campaign+(check+dependencies)",
                status_code=303,
            )

    return RedirectResponse(url="/ui/campaigns?msg=Campaign+deleted", status_code=303)


@router.get("/ui/campaigns/{campaign_id}/kb")
def ui_campaign_kb(request: Request, campaign_id: UUID, db=Depends(get_db)):
    sources = (
        db.execute(
            text(
                """
            SELECT id, name, kind, root_path, enabled, recursive, follow_symlinks,
                   include_globs, exclude_globs, change_detection,
                   last_scan_at, last_ingest_at
            FROM campaign_sources
            WHERE campaign_id = :cid
            ORDER BY created_at DESC
        """
            ),
            {"cid": str(campaign_id)},
        )
        .mappings()
        .all()
    )

    return templates.TemplateResponse(
        "campaign_kb.html",
        {"request": request, "campaign_id": str(campaign_id), "sources": sources},
    )


@router.get("/ui/campaigns/{campaign_id}/sources/new")
def ui_source_new(request: Request, campaign_id: UUID):
    return templates.TemplateResponse(
        "source_form.html",
        {
            "request": request,
            "campaign_id": str(campaign_id),
            "error": None,
            "form": {},
        },
    )


@router.post("/ui/campaigns/{campaign_id}/sources/new")
def ui_source_create(
    request: Request,
    campaign_id: UUID,
    name: str = Form(...),
    kind: str = Form("obsidian_md"),
    root_path: str = Form(...),
    recursive: bool = Form(True),
    follow_symlinks: bool = Form(False),
    include_globs_raw: str = Form("**/*.md"),
    exclude_globs_raw: str = Form("**/.obsidian/**\n**/.git/**\n**/node_modules/**"),
    change_detection: str = Form("auto"),
    enabled: bool = Form(True),
    db=Depends(get_db),
):
    # parse one glob per line
    include_globs = [ln.strip() for ln in include_globs_raw.splitlines() if ln.strip()]
    exclude_globs = [ln.strip() for ln in exclude_globs_raw.splitlines() if ln.strip()]

    # Basic sanity
    if not root_path.strip():
        return templates.TemplateResponse(
            "source_form.html",
            {
                "request": request,
                "campaign_id": str(campaign_id),
                "error": "root_path is required",
                "form": {"name": name, "root_path": root_path},
            },
            status_code=400,
        )

    db.execute(
        text(
            """
            INSERT INTO campaign_sources
              (campaign_id, name, kind, root_path, recursive, follow_symlinks,
               include_globs, exclude_globs, change_detection, enabled)
            VALUES
              (:cid, :name, :kind, :root, :rec, :sym, :inc, :exc, :cd, :en)
        """
        ),
        {
            "cid": str(campaign_id),
            "name": name,
            "kind": kind,
            "root": root_path.strip(),
            "rec": bool(recursive),
            "sym": bool(follow_symlinks),
            # if your table uses text[] these lists are correct.
            "inc": include_globs,
            "exc": exclude_globs,
            "cd": change_detection,
            "en": bool(enabled),
        },
    )
    db.commit()

    return RedirectResponse(
        f"/ui/campaigns/{campaign_id}/kb", status_code=HTTP_303_SEE_OTHER
    )


@router.post("/ui/sources/{source_id}/delete")
def ui_source_delete(source_id: UUID, request: Request, db=Depends(get_db)):
    # find campaign_id for redirect
    row = (
        db.execute(
            text("SELECT campaign_id FROM campaign_sources WHERE id = :id"),
            {"id": str(source_id)},
        )
        .mappings()
        .first()
    )
    if row:
        cid = row["campaign_id"]
        db.execute(
            text("DELETE FROM campaign_sources WHERE id = :id"), {"id": str(source_id)}
        )
        db.commit()
        return RedirectResponse(
            f"/ui/campaigns/{cid}/kb", status_code=HTTP_303_SEE_OTHER
        )

    return RedirectResponse("/ui/campaigns", status_code=HTTP_303_SEE_OTHER)
