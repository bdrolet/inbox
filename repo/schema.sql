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
    message_id    UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    embedding     vector(384)  NOT NULL,
    current_label TEXT,        -- NULL until human confirms or corrects; never set by LLM
    updated_at    TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS message_embeddings_hnsw
    ON message_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS classifications (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id     UUID        NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    category       TEXT        NOT NULL,
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
