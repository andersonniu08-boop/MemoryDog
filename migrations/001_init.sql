-- Migration 001: MemoryDog MVP schema
-- Requires: PostgreSQL 16 + pgvector extension

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Core memory storage
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    summary VARCHAR(512),
    embedding VECTOR(1536),
    memory_type TEXT NOT NULL CHECK (memory_type IN (
        'conversation', 'design_decision', 'learned_fact',
        'user_preference', 'task_history', 'code_snippet', 'bug'
    )),
    workspace_name TEXT NOT NULL,
    importance FLOAT DEFAULT 0.5,
    access_count INT DEFAULT 0,
    last_accessed TIMESTAMP DEFAULT NOW(),
    decay_factor FLOAT DEFAULT 1.0,
    tags TEXT[] DEFAULT '{}',
    source_turn_id UUID,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Conversation sessions
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_name TEXT NOT NULL,
    title VARCHAR(256),
    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP
);

-- Individual messages
CREATE TABLE conversation_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tool_calls JSONB,
    token_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Instinct activation log
CREATE TABLE instinct_activations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instinct_name TEXT NOT NULL,
    conversation_id UUID REFERENCES conversations(id),
    trigger_match_score FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Persistent user preferences
CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Vector index
CREATE INDEX idx_memories_embedding ON memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- Full-text search index
CREATE INDEX idx_memories_fts ON memories
    USING gin (to_tsvector('english', content));

-- Workspace lookup
CREATE INDEX idx_memories_workspace ON memories (workspace_name);

-- Tag lookup
CREATE INDEX idx_memories_tags ON memories USING gin (tags);

-- Conversation indexes
CREATE INDEX idx_turns_conversation ON conversation_turns (conversation_id);
CREATE INDEX idx_conversations_workspace ON conversations (workspace_name);
