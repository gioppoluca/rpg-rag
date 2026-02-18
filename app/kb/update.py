from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from app.core.db import SessionLocal

def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _matches_any(rel_posix: str, patterns: List[str]) -> bool:
    p = PurePosixPath(rel_posix)
    for pat in patterns:
        try:
            if p.match(pat):
                return True
        except Exception:
            continue
    return False

def _should_include(rel_posix: str, include_globs: List[str], exclude_globs: List[str]) -> bool:
    if exclude_globs and _matches_any(rel_posix, exclude_globs):
        return False
    if not include_globs:
        return True
    return _matches_any(rel_posix, include_globs)

def _chunk_text(text_str: str, max_chars: int = 6000, overlap: int = 300) -> List[str]:
    s = text_str.strip()
    if not s:
        return []
    out: List[str] = []
    i = 0
    while i < len(s):
        j = min(len(s), i + max_chars)
        out.append(s[i:j])
        if j >= len(s):
            break
        i = max(0, j - overlap)
    return out

def _upsert_document_and_chunks(conn, campaign_id: str, source_file_id: str, title: str, doc_type: str, body: str) -> Tuple[str, str]:
    content_hash = _sha256_text(body)

    doc_row = conn.execute(
        text(
            """
            SELECT id, content_hash
            FROM kb_documents
            WHERE campaign_id = :campaign_id AND source_file_id = :source_file_id
            """
        ),
        {"campaign_id": campaign_id, "source_file_id": source_file_id},
    ).mappings().first()

    if doc_row and doc_row["content_hash"] == content_hash:
        return (str(doc_row["id"]), "skipped")

    if doc_row:
        doc_id = str(doc_row["id"])
        conn.execute(
            text(
                """
                UPDATE kb_documents
                SET title = :title,
                    doc_type = :doc_type,
                    body_md = :body,
                    content_hash = :content_hash,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": doc_id, "title": title, "doc_type": doc_type, "body": body, "content_hash": content_hash},
        )
        conn.execute(text("DELETE FROM kb_chunks WHERE document_id = :doc_id"), {"doc_id": doc_id})
    else:
        doc_id = conn.execute(
            text(
                """
                INSERT INTO kb_documents (campaign_id, source_file_id, doc_type, title, body_md, content_hash)
                VALUES (:campaign_id, :source_file_id, :doc_type, :title, :body, :content_hash)
                RETURNING id
                """
            ),
            {"campaign_id": campaign_id, "source_file_id": source_file_id, "doc_type": doc_type, "title": title, "body": body, "content_hash": content_hash},
        ).scalar_one()

    chunks = _chunk_text(body)
    for idx, ch in enumerate(chunks):
        conn.execute(
            text(
                """
                INSERT INTO kb_chunks (campaign_id, document_id, section_path, chunk_index, content, metadata, embedding, embedding_model)
                VALUES (:campaign_id, :document_id, :section_path, :chunk_index, :content, :metadata::jsonb, NULL, :embedding_model)
                """
            ),
            {
                "campaign_id": campaign_id,
                "document_id": str(doc_id),
                "section_path": "",
                "chunk_index": idx,
                "content": ch,
                "metadata": json.dumps({"v": 0}),
                "embedding_model": "nomic-embed-text",
            },
        )
    return (str(doc_id), "ingested")

def update_campaign_kb(
    campaign_id: str,
    dry_run: bool = False,
    force_rehash: bool = False,
    max_files: Optional[int] = None,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT 1 FROM campaigns WHERE id = :id"), {"id": campaign_id}).scalar()
        if not exists:
            raise ValueError("Campaign not found.")

        ingest_run_id = db.execute(
            text(
                """
                INSERT INTO ingest_runs (campaign_id, trigger, status)
                VALUES (:campaign_id, 'api', 'running')
                RETURNING id
                """
            ),
            {"campaign_id": campaign_id},
        ).scalar_one()

        stats: Dict[str, Any] = {
            "sources_total": 0,
            "sources_scanned": 0,
            "files_seen": 0,
            "files_new": 0,
            "files_changed": 0,
            "files_unchanged": 0,
            "files_deleted": 0,
            "files_ingested": 0,
            "docs_ingested": 0,
            "docs_skipped": 0,
            "errors": [],
        }

        sources = db.execute(
            text(
                """
                SELECT id, name, kind, root_path, recursive, follow_symlinks, include_globs, exclude_globs, change_detection
                FROM campaign_sources
                WHERE campaign_id = :campaign_id AND enabled = true
                """
            ),
            {"campaign_id": campaign_id},
        ).mappings().all()

        stats["sources_total"] = len(sources)

        for src in sources:
            stats["sources_scanned"] += 1
            source_id = str(src["id"])
            root_path = Path(src["root_path"])
            include_globs = list(src["include_globs"] or [])
            exclude_globs = list(src["exclude_globs"] or [])
            change_detection = src["change_detection"]

            db.execute(text("UPDATE campaign_sources SET last_scan_at = now() WHERE id = :id"), {"id": source_id})

            if not root_path.exists():
                stats["errors"].append(f"Source '{src['name']}' root_path not found: {root_path}")
                continue

            existing = db.execute(
                text(
                    """
                    SELECT id, rel_path, size_bytes, mtime_epoch, sha256, status
                    FROM source_files
                    WHERE source_id = :source_id AND status <> 'deleted'
                    """
                ),
                {"source_id": source_id},
            ).mappings().all()
            existing_map = {r["rel_path"]: r for r in existing}
            seen_paths = set()

            folder_rows = db.execute(
                text(
                    """
                    SELECT id, rel_path
                    FROM source_folders
                    WHERE source_id = :source_id
                    """
                ),
                {"source_id": source_id},
            ).mappings().all()
            folder_map = {r["rel_path"]: str(r["id"]) for r in folder_rows}

            if "" not in folder_map:
                root_folder_id = db.execute(
                    text(
                        """
                        INSERT INTO source_folders (source_id, rel_path, parent_id, depth, last_seen_at)
                        VALUES (:source_id, '', NULL, 0, now())
                        ON CONFLICT (source_id, rel_path) DO UPDATE SET last_seen_at = now()
                        RETURNING id
                        """
                    ),
                    {"source_id": source_id},
                ).scalar_one()
                folder_map[""] = str(root_folder_id)
            else:
                db.execute(
                    text("UPDATE source_folders SET last_seen_at = now() WHERE source_id = :sid AND rel_path = ''"),
                    {"sid": source_id},
                )

            files_to_ingest = 0
            for dirpath, _, filenames in os.walk(root_path, followlinks=bool(src["follow_symlinks"])):
                dir_rel = Path(dirpath).relative_to(root_path).as_posix()
                parent_rel = "" if dir_rel == "" else PurePosixPath(dir_rel).parent.as_posix()
                depth = 0 if dir_rel == "" else len(PurePosixPath(dir_rel).parts)

                if dir_rel not in folder_map:
                    parent_id = folder_map.get(parent_rel, folder_map[""])
                    folder_id = db.execute(
                        text(
                            """
                            INSERT INTO source_folders (source_id, rel_path, parent_id, depth, last_seen_at)
                            VALUES (:source_id, :rel_path, :parent_id, :depth, now())
                            ON CONFLICT (source_id, rel_path) DO UPDATE SET last_seen_at = now()
                            RETURNING id
                            """
                        ),
                        {"source_id": source_id, "rel_path": dir_rel, "parent_id": parent_id, "depth": depth},
                    ).scalar_one()
                    folder_map[dir_rel] = str(folder_id)
                else:
                    db.execute(
                        text("UPDATE source_folders SET last_seen_at = now() WHERE source_id = :sid AND rel_path = :rp"),
                        {"sid": source_id, "rp": dir_rel},
                    )

                for fn in filenames:
                    full_path = Path(dirpath) / fn
                    rel_path = full_path.relative_to(root_path).as_posix()

                    if not _should_include(rel_path, include_globs, exclude_globs):
                        continue

                    try:
                        st = full_path.stat()
                    except OSError as e:
                        stats["errors"].append(f"stat failed for {full_path}: {e}")
                        continue

                    seen_paths.add(rel_path)
                    stats["files_seen"] += 1

                    size_bytes = int(st.st_size)
                    mtime_epoch = int(st.st_mtime)

                    prev = existing_map.get(rel_path)
                    is_new = prev is None
                    changed_hint = is_new or (prev["size_bytes"] != size_bytes or prev["mtime_epoch"] != mtime_epoch)

                    sha256 = prev["sha256"] if prev else None
                    changed = changed_hint

                    if (change_detection in ("sha256", "auto") and (force_rehash or changed_hint or is_new)):
                        sha256_now = _sha256_file(full_path)
                        if prev and prev["sha256"] == sha256_now:
                            changed = False
                        sha256 = sha256_now

                    folder_id = folder_map.get(dir_rel, folder_map[""])
                    ext = full_path.suffix.lower().lstrip(".") or None

                    if is_new:
                        stats["files_new"] += 1
                    elif changed:
                        stats["files_changed"] += 1
                    else:
                        stats["files_unchanged"] += 1

                    if is_new:
                        source_file_id = db.execute(
                            text(
                                """
                                INSERT INTO source_files (
                                  source_id, folder_id, rel_path, ext, size_bytes, mtime_epoch, sha256,
                                  status, last_seen_at, last_ingest_status
                                )
                                VALUES (
                                  :source_id, :folder_id, :rel_path, :ext, :size_bytes, :mtime_epoch, :sha256,
                                  'seen', now(), 'never'
                                )
                                RETURNING id
                                """
                            ),
                            {"source_id": source_id, "folder_id": folder_id, "rel_path": rel_path, "ext": ext, "size_bytes": size_bytes, "mtime_epoch": mtime_epoch, "sha256": sha256},
                        ).scalar_one()
                    else:
                        source_file_id = str(prev["id"])
                        db.execute(
                            text(
                                """
                                UPDATE source_files
                                SET folder_id = :folder_id,
                                    ext = :ext,
                                    size_bytes = :size_bytes,
                                    mtime_epoch = :mtime_epoch,
                                    sha256 = :sha256,
                                    status = 'seen',
                                    last_seen_at = now()
                                WHERE id = :id
                                """
                            ),
                            {"id": source_file_id, "folder_id": folder_id, "ext": ext, "size_bytes": size_bytes, "mtime_epoch": mtime_epoch, "sha256": sha256},
                        )

                    if (is_new or changed) and not dry_run:
                        if max_files is not None and files_to_ingest >= max_files:
                            continue

                        try:
                            files_to_ingest += 1
                            body = full_path.read_text(encoding="utf-8", errors="replace")
                            title = full_path.stem
                            doc_type = "md" if ext == "md" else (ext or "file")

                            _, doc_action = _upsert_document_and_chunks(
                                conn=db,
                                campaign_id=campaign_id,
                                source_file_id=source_file_id,
                                title=title,
                                doc_type=doc_type,
                                body=body,
                            )

                            if doc_action == "ingested":
                                stats["files_ingested"] += 1
                                stats["docs_ingested"] += 1
                                ingest_status = "ok"
                            else:
                                stats["docs_skipped"] += 1
                                ingest_status = "skipped"

                            db.execute(
                                text(
                                    """
                                    UPDATE source_files
                                    SET last_ingested_at = now(),
                                        last_ingest_status = :status,
                                        error = NULL
                                    WHERE id = :id
                                    """
                                ),
                                {"id": source_file_id, "status": ingest_status},
                            )

                            db.execute(
                                text(
                                    """
                                    INSERT INTO ingest_run_files (ingest_run_id, source_file_id, action, status)
                                    VALUES (:run_id, :file_id, 'ingest', 'ok')
                                    ON CONFLICT (ingest_run_id, source_file_id) DO NOTHING
                                    """
                                ),
                                {"run_id": str(ingest_run_id), "file_id": source_file_id},
                            )
                        except Exception as e:
                            stats["errors"].append(f"ingest failed for {rel_path}: {e}")
                            db.execute(
                                text(
                                    """
                                    UPDATE source_files
                                    SET last_ingested_at = now(),
                                        last_ingest_status = 'error',
                                        error = :err
                                    WHERE id = :id
                                    """
                                ),
                                {"id": source_file_id, "err": str(e)[:5000]},
                            )
                            db.execute(
                                text(
                                    """
                                    INSERT INTO ingest_run_files (ingest_run_id, source_file_id, action, status, error)
                                    VALUES (:run_id, :file_id, 'ingest', 'error', :err)
                                    ON CONFLICT (ingest_run_id, source_file_id) DO UPDATE SET status='error', error=excluded.error
                                    """
                                ),
                                {"run_id": str(ingest_run_id), "file_id": source_file_id, "err": str(e)[:5000]},
                            )

            for rel_path, prev in existing_map.items():
                if rel_path in seen_paths:
                    continue
                stats["files_deleted"] += 1
                if not dry_run:
                    db.execute(text("UPDATE source_files SET status='deleted', last_seen_at=now() WHERE id=:id"), {"id": str(prev["id"])})
                    db.execute(
                        text(
                            """
                            INSERT INTO ingest_run_files (ingest_run_id, source_file_id, action, status, reason)
                            VALUES (:run_id, :file_id, 'delete', 'ok', 'missing_on_disk')
                            ON CONFLICT (ingest_run_id, source_file_id) DO NOTHING
                            """
                        ),
                        {"run_id": str(ingest_run_id), "file_id": str(prev["id"])},
                    )

            if not dry_run:
                db.execute(text("UPDATE campaign_sources SET last_ingest_at = now() WHERE id = :id"), {"id": source_id})

        status = "ok" if not stats["errors"] else "partial"
        db.execute(
            text(
                """
                UPDATE ingest_runs
                SET finished_at = now(),
                    status = :status,
                    stats = :stats::jsonb,
                    error = :error
                WHERE id = :id
                """
            ),
            {"id": str(ingest_run_id), "status": status, "stats": json.dumps(stats), "error": "\n".join(stats["errors"])[:5000] if stats["errors"] else None},
        )

        db.commit()
        return {"ingest_run_id": str(ingest_run_id), "status": status, "stats": stats}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
