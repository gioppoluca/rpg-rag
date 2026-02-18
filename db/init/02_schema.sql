CREATE TABLE IF NOT EXISTS campaigns (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name text NOT NULL UNIQUE,
    system text NOT NULL,
    -- "dnd5e" | "v5" | etc
    description text,
    config jsonb NOT NULL DEFAULT '{}',
    -- arbitrary: rules, house rules, etc
    embedding_model text NOT NULL DEFAULT 'nomic-embed-text',
    embedding_dim int NOT NULL DEFAULT 768,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS workspaces (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name text NOT NULL,
    -- e.g. "Session planning: next arc"
    purpose text,
    -- optional freeform
    state jsonb NOT NULL DEFAULT '{}',
    -- optional: pinned entities, current date, etc
    created_at timestamptz NOT NULL DEFAULT now(),
    last_active timestamptz NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, name)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id bigserial PRIMARY KEY,
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    token_count int,
    metadata jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS chat_messages_ws_time ON chat_messages (workspace_id, created_at DESC);
CREATE TABLE IF NOT EXISTS campaign_sources (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name text NOT NULL,
    -- e.g. "Obsidian Vault", "PDF Handouts"
    kind text NOT NULL,
    -- "obsidian_md" | "pdf" | "txt" | etc
    root_path text NOT NULL,
    -- folder root on disk (inside container mount)
    recursive boolean NOT NULL DEFAULT true,
    follow_symlinks boolean NOT NULL DEFAULT false,
    include_globs text [] NOT NULL DEFAULT ARRAY ['**/*.md'],
    exclude_globs text [] NOT NULL DEFAULT ARRAY ['**/.obsidian/**','**/.git/**','**/node_modules/**'],
    -- how to detect changes:
    -- "mtime_size" (fast) OR "sha256" (safe) OR "auto" (mtime_size then sha256 if needed)
    change_detection text NOT NULL DEFAULT 'auto' CHECK (
        change_detection IN ('mtime_size', 'sha256', 'auto')
    ),
    enabled boolean NOT NULL DEFAULT true,
    last_scan_at timestamptz,
    last_ingest_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, name)
);
CREATE INDEX IF NOT EXISTS campaign_sources_campaign ON campaign_sources (campaign_id);
CREATE TABLE IF NOT EXISTS source_folders (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id uuid NOT NULL REFERENCES campaign_sources(id) ON DELETE CASCADE,
    rel_path text NOT NULL,
    -- relative from root_path ('' for root)
    parent_id uuid REFERENCES source_folders(id) ON DELETE CASCADE,
    depth int NOT NULL DEFAULT 0,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, rel_path)
);
CREATE INDEX IF NOT EXISTS source_folders_parent ON source_folders (parent_id);
CREATE TABLE IF NOT EXISTS source_files (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id uuid NOT NULL REFERENCES campaign_sources(id) ON DELETE CASCADE,
    folder_id uuid REFERENCES source_folders(id) ON DELETE
    SET NULL,
        rel_path text NOT NULL,
        -- relative path from root_path
        ext text,
        size_bytes bigint,
        mtime_epoch bigint,
        -- store stat.mtime as integer seconds (portable)
        sha256 text,
        -- computed if change_detection requires it
        content_hash text,
        -- optional: can store normalized hash for MD body only
        status text NOT NULL DEFAULT 'seen' CHECK (status IN ('seen', 'deleted', 'error')),
        first_seen_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        last_ingested_at timestamptz,
        last_ingest_status text NOT NULL DEFAULT 'never' CHECK (
            last_ingest_status IN ('never', 'ok', 'skipped', 'error')
        ),
        error text,
        UNIQUE (source_id, rel_path)
);
CREATE INDEX IF NOT EXISTS source_files_source ON source_files (source_id);
CREATE INDEX IF NOT EXISTS source_files_status ON source_files (source_id, status);
CREATE INDEX IF NOT EXISTS source_files_changed_hint ON source_files (source_id, mtime_epoch, size_bytes);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    trigger text NOT NULL DEFAULT 'manual' CHECK (
        trigger IN ('manual', 'scheduled', 'watcher', 'api')
    ),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'error')),
    stats jsonb NOT NULL DEFAULT '{}',
    error text
);
CREATE TABLE IF NOT EXISTS ingest_run_files (
    ingest_run_id uuid NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
    source_file_id uuid NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    action text NOT NULL CHECK (action IN ('ingest', 'skip', 'delete')),
    reason text,
    status text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),
    error text,
    PRIMARY KEY (ingest_run_id, source_file_id)
);
CREATE INDEX IF NOT EXISTS ingest_runs_campaign_time ON ingest_runs (campaign_id, started_at DESC);
CREATE TABLE IF NOT EXISTS kb_documents (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    -- If doc comes from a file, link it:
    source_file_id uuid REFERENCES source_files(id) ON DELETE
    SET NULL,
        -- Optional: memory docs can be workspace-scoped
        workspace_id uuid REFERENCES workspaces(id) ON DELETE
    SET NULL,
        doc_type text NOT NULL,
        -- npc/pc/session/location/plot/faction/item/memory/etc
        title text NOT NULL,
        canonical_name text,
        -- for entities (if applicable)
        frontmatter jsonb NOT NULL DEFAULT '{}',
        body_md text,
        -- optional: store raw markdown (or omit if you prefer filesystem as source of truth)
        content_hash text,
        -- hash of normalized content to avoid re-chunking unnecessarily
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_documents_campaign ON kb_documents (campaign_id);
CREATE INDEX IF NOT EXISTS kb_documents_type ON kb_documents (campaign_id, doc_type);
CREATE INDEX IF NOT EXISTS kb_documents_title_trgm ON kb_documents USING gin (title gin_trgm_ops);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    section_path text,
    chunk_index int NOT NULL,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    -- FTS: keep predictable, use 'simple'
    fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    embedding vector(768),
    embedding_model text NOT NULL DEFAULT 'nomic-embed-text',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS kb_chunks_fts_gin ON kb_chunks USING gin (fts);
-- Create vector index later when you have enough data:
-- CREATE INDEX kb_chunks_embedding_hnsw
--   ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_doc ON kb_chunks (document_id);
CREATE TABLE IF NOT EXISTS kb_entities (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind text NOT NULL,
    -- pc/npc/location/faction/item/plot/session/folder/etc
    canonical_name text NOT NULL,
    aliases text [] NOT NULL DEFAULT '{}',
    source_document_id uuid REFERENCES kb_documents(id) ON DELETE
    SET NULL,
        meta jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (campaign_id, kind, canonical_name)
);
CREATE INDEX IF NOT EXISTS kb_entities_name_trgm ON kb_entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS kb_entities_aliases_gin ON kb_entities USING gin (aliases);
CREATE TABLE IF NOT EXISTS kb_edges (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    src_id uuid NOT NULL REFERENCES kb_entities(id) ON DELETE CASCADE,
    dst_id uuid NOT NULL REFERENCES kb_entities(id) ON DELETE CASCADE,
    rel text NOT NULL,
    -- member_of, located_in, appears_in, boon_owed_to...
    weight real NOT NULL DEFAULT 1.0,
    attrs jsonb NOT NULL DEFAULT '{}',
    -- secrecy/intensity/status/etc
    evidence jsonb NOT NULL DEFAULT '{}',
    -- doc_id/chunk_id/quotes/etc
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_edges_src ON kb_edges (src_id);
CREATE INDEX IF NOT EXISTS kb_edges_dst ON kb_edges (dst_id);
CREATE INDEX IF NOT EXISTS kb_edges_rel ON kb_edges (campaign_id, rel);
CREATE TABLE IF NOT EXISTS kb_mentions (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    chunk_id uuid NOT NULL REFERENCES kb_chunks(id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES kb_entities(id) ON DELETE CASCADE,
    surface_form text,
    start_offset int,
    end_offset int,
    confidence real NOT NULL DEFAULT 1.0,
    extractor text NOT NULL DEFAULT 'wikilink' CHECK (
        extractor IN ('wikilink', 'alias_match', 'llm', 'manual')
    ),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_mentions_entity ON kb_mentions (entity_id);
CREATE INDEX IF NOT EXISTS kb_mentions_chunk ON kb_mentions (chunk_id);
CREATE TABLE IF NOT EXISTS kb_suggestions (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    -- optional: suggestion originated while chatting in a workspace
    workspace_id uuid REFERENCES workspaces(id) ON DELETE
    SET NULL,
        -- optional: suggestion originated from a chunk
        source_chunk_id uuid REFERENCES kb_chunks(id) ON DELETE
    SET NULL,
        kind text NOT NULL CHECK (kind IN ('entity', 'edge', 'tag', 'attribute')),
        payload jsonb NOT NULL,
        -- proposed node/edge + fields
        confidence real NOT NULL DEFAULT 0.5,
        status text NOT NULL DEFAULT 'new' CHECK (
            status IN ('new', 'accepted', 'rejected', 'applied')
        ),
        created_at timestamptz NOT NULL DEFAULT now(),
        reviewed_at timestamptz,
        reviewer text,
        notes text
);
CREATE INDEX IF NOT EXISTS kb_suggestions_campaign_status ON kb_suggestions (campaign_id, status, created_at DESC);