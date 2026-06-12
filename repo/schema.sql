CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS messages (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source       TEXT        NOT NULL,
    external_id  TEXT        NOT NULL,
    sender       TEXT        NOT NULL,
    sender_display TEXT,
    subject      TEXT,
    body         TEXT,
    received_at  TIMESTAMPTZ NOT NULL,
    thread_id    TEXT,
    raw          JSONB,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id        UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    embedding         vector(384)  NOT NULL,
    current_label     TEXT,        -- NULL until human confirms or corrects; never set by LLM
    current_importance TEXT,       -- NULL until human confirms or corrects; never set by LLM
    updated_at        TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS message_embeddings_hnsw
    ON message_embeddings USING hnsw (embedding vector_cosine_ops);

-- Add current_importance to existing deployments (no-op if already present)
ALTER TABLE message_embeddings ADD COLUMN IF NOT EXISTS current_importance TEXT;

CREATE TABLE IF NOT EXISTS classifications (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id     UUID        NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    category       TEXT        NOT NULL,
    importance     TEXT,                -- 'P0'|'P1'|'P2'|'P3'; NULL for human-assigned rows
    confidence     FLOAT,
    alternatives   JSONB,
    tags           TEXT[],
    reasoning      TEXT,
    model          TEXT,
    prompt_version TEXT,
    source         TEXT        NOT NULL, -- 'llm' | 'human_correction' | 'human_confirmation'
    created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS classifications_message_id
    ON classifications (message_id, created_at DESC);

-- Add importance to existing deployments (no-op if already present)
ALTER TABLE classifications ADD COLUMN IF NOT EXISTS importance TEXT;

CREATE TABLE IF NOT EXISTS senders (
    identifier        TEXT NOT NULL,
    source            TEXT NOT NULL,
    first_seen        TIMESTAMPTZ,
    message_count     INT  DEFAULT 0,
    my_response_count INT  DEFAULT 0,
    relationship_label TEXT,
    notes             TEXT,
    PRIMARY KEY (source, identifier)
);

CREATE TABLE IF NOT EXISTS tags (
    name        TEXT PRIMARY KEY,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asana_tag_cache (
    tag_name   TEXT PRIMARY KEY,
    tag_gid    TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS calendar_invites (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id       UUID REFERENCES messages(id),
    graph_message_id TEXT,
    ical_uid         TEXT,
    title            TEXT,
    start_time       TIMESTAMPTZ,
    end_time         TIMESTAMPTZ,
    timezone         TEXT,
    organizer        TEXT,
    zoom_link        TEXT,
    location         TEXT,
    user_response    TEXT,        -- NULL | accept | decline | maybe
    responded_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
