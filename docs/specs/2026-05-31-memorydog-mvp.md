# MemoryDog MVP — Design Specification

**Date:** 2026-05-31
**Status:** Design Complete
**Target:** 4 weeks to demo-ready, single-student buildable

## Overview

MemoryDog is a memory-augmented coding agent that gets better the longer you work with it. Unlike stateless coding agents, it remembers previous conversations, design decisions, bugs, and project history across sessions.

The mascot is a dog because the agent "fetches" memories.

**MVP scope:** A single-user CLI tool that connects directly to a local PostgreSQL database. No server, no Redis, no workers, no multi-tenancy. Maximum resume value per week of work.

### Core Differentiators

1. **Persistent memory** — facts survive across sessions, not just within a context window
2. **Hybrid retrieval** — vector similarity + keyword search + recency + importance
3. **Instincts** — user-defined reusable procedural modules that guide agent behavior
4. **Developer TUI** — multi-pane Textual interface, not a chat terminal
5. **Dog persona** — professional UX with personality in the chrome

---

## Architecture

### Architecture: Shared Core + Thin Frontends

```
┌─────────────────────────────────────────────────┐
│ memorydog-core (Python package, zero UI)        │
│ ┌─────────────────────────────────────────────┐ │
│ │ Agent Loop  │ Memory CRUD │ Retrieval       │ │
│ │ Tools (x7)  │ Ranking     │ Instinct Engine │ │
│ │ Provider    │ Context     │ DB Layer        │ │
│ └─────────────────────────────────────────────┘ │
└──────────┬──────────────────────┬───────────────┘
           │ imports              │ imports via subprocess
           ▼                      ▼
┌──────────────────────┐  ┌──────────────────────────┐
│ memorydog-cli        │  │ memorydog-vscode          │
│ Textual TUI frontend │  │ TypeScript extension      │
│ • Multi-pane layout  │  │ • Sidebar memory browser  │
│ • Conversation view  │  │ • Instinct viewer         │
│ • File preview       │  │ • Animated dog mascot     │
│ • Tool output        │  │ • Terminal integration    │
│ • 🐕 Status bar      │  │ • Webview panels          │
└──────────────────────┘  └──────────────────────────┘
           │
           │ asyncpg
           ▼
┌─────────────────────────────────────────────────┐
│ PostgreSQL 16 + pgvector (local, Docker)        │
│ 5 tables, HNSW index, full-text search          │
└─────────────────────────────────────────────────┘
```

**Zero duplication.** One implementation of memory, retrieval, instincts, and agent behavior — shared by both frontends.

**Why core+frontends split?** The agent logic is a reusable Python package. The CLI and VS Code extension are thin importers. This keeps all business logic in one place, prevents duplication, and lets the VS Code extension deliver a richer UX (animated dog, memory browser) without touching the core agent code.

**Why no server?** The agent IS the product. A REST API adds 50% more code for zero interview value. PostgreSQL is fast enough for single-user workloads. If you ever need multi-user, the memory layer is already cleanly separated in `db.py` and `memory.py` — wrapping it in FastAPI later is straightforward.

**Why no Redis?** HNSW indexes make vector search sub-millisecond. PostgreSQL full-text search is fast. A single user doesn't saturate a local database. Add Redis only if you measure a real bottleneck (you won't at this scale).

**Why no background workers?** Embedding generation takes ~100ms via API — do it inline at memory creation time. The user is waiting for the agent's response anyway. No queue infrastructure needed.

---

## Database Schema — 5 Tables

```sql
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

CREATE INDEX idx_memories_embedding ON memories
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_memories_fts ON memories
    USING gin (to_tsvector('english', content));
CREATE INDEX idx_memories_workspace ON memories (workspace_name);
CREATE INDEX idx_memories_tags ON memories USING gin (tags);

-- Conversation sessions
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_name TEXT NOT NULL,
    title VARCHAR(256),
    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP
);

-- Individual messages in a conversation
CREATE TABLE conversation_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tool_calls JSONB,
    token_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Instinct activation log (instincts defined in TOML file)
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
```

**5 tables, not 17.** No users, no workspaces (just a `workspace_name` column), no memory chunks (memories are atomic), no memory relations, no memory tags (tags are a `TEXT[]` column), no instinct tables (instincts are TOML files), no retrieval events (access_count on memories suffices).

### Workspace Awareness

`workspace_name` is derived from the current directory name or git repo name. It scopes memory retrieval with a ranking boost — not a hard filter:

- Same workspace: **1.5x** score multiplier
- Different workspace: **1.0x** score multiplier
- Cross-workspace retrieval works but prefers local context

No workspace table, no management UI, no ownership model. Just a string field.

---

## Memory System

### Memory Types

| Type | Description |
|------|-------------|
| `conversation` | Things discussed |
| `design_decision` | Architectural choices made |
| `learned_fact` | Deduced information |
| `user_preference` | User's stated preferences |
| `task_history` | Completed tasks |
| `code_snippet` | Important patterns |
| `bug` | Bugs found and fixes applied |

### Importance Scoring

After extraction, each memory gets an initial importance (0.0–1.0) assessed by the extraction LLM. Importance updates over time:

- **On access:** +0.01 per retrieval (capped at 0.95)
- **On explicit save:** set to 0.90
- **Decay:** importance decays exponentially if memory is never accessed. Decay tracked via `decay_factor`: `decay_factor *= e^(-0.01 * days_since_last_access)`. Effective importance = `importance * decay_factor`.

### Memory Extraction

After each conversation turn where code was modified or 3+ tools were called:

1. Send conversation summary to LLM with extraction prompt
2. LLM returns JSON array of `{type, content, summary, importance, tags}`
3. For each extracted fact:
   - Check dedup: cosine similarity against existing memories > 0.95 → skip
   - Generate embedding via OpenAI API
   - Insert into `memories`
4. Rate limit: max 20 new memories per turn

### Memory Lifecycle (Simplified)

- **Store gate:** Importance must be > 0.2 to persist
- **Dedup:** Cosine similarity > 0.95 → skip on insert
- **Pruning:** Soft-delete memories with effective importance < 0.1 and no access in 180 days (simple SQL query, maybe run manually or via a basic cron comment in README)

Full consolidation, archival, and contradiction detection are deferred to post-MVP.

---

## Retrieval Pipeline

### Hybrid Ranking Formula

```
Score(m, q) = 0.35·V + 0.20·B + 0.15·R + 0.15·I + 0.10·W + 0.05·F
```

| Term | Definition |
|------|-----------|
| **V** Vector similarity | `(cos(emb_m, emb_q) + 1) / 2`  (pgvector cosine distance) |
| **B** BM25 relevance | `ts_rank(to_tsvector(content), plainto_tsquery(q))`  (PostgreSQL FTS) |
| **R** Recency | `e^(-0.01 * days_since_last_access)` |
| **I** Importance | `importance * decay_factor` |
| **W** Workspace boost | same workspace → 1.5, different → 1.0 |
| **F** Access frequency | logistic sigmoid of `access_count / mean_access_count` |

### Multi-Stage Retrieval

1. **Initial retrieval:** On every user turn, retrieve top-5 memories for prompt injection
2. **Triggered retrieval:** When agent reads a new file, encounters unfamiliar terms, or discovers new subsystems → additional retrieval (max 3 per turn)
3. **Explicit retrieval:** Agent can call `memory_search` tool directly

### Retrieval Query

```sql
WITH vector_results AS (
    SELECT id, content, summary, memory_type, workspace_name,
           importance, decay_factor, access_count, last_accessed,
           1 - (embedding <=> $query_embedding) AS vector_score
    FROM memories
    ORDER BY embedding <=> $query_embedding
    LIMIT 50
),
fts_results AS (
    SELECT id, content, summary, memory_type, workspace_name,
           importance, decay_factor, access_count, last_accessed,
           ts_rank(to_tsvector('english', content),
                   plainto_tsquery('english', $query_text)) AS bm25_score
    FROM memories
    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $query_text)
    LIMIT 50
),
combined AS (
    SELECT * FROM vector_results
    UNION
    SELECT * FROM fts_results
)
SELECT id, content, summary, memory_type, workspace_name,
       vector_score, bm25_score,
       (0.35 * COALESCE(vector_score, 0) +
        0.20 * COALESCE(bm25_score, 0) +
        0.15 * EXP(-0.01 * EXTRACT(DAY FROM NOW() - last_accessed)) +
        0.15 * (importance * decay_factor) +
        0.10 * CASE WHEN workspace_name = $current_workspace THEN 1.5 ELSE 1.0 END +
        0.05 * (1.0 / (1.0 + EXP(-(access_count - $mean_access) / $mean_access)))
       ) AS final_score
FROM combined
ORDER BY final_score DESC
LIMIT 5;
```

### Cross-Encoder Reranking (Planned)

The current retrieval pipeline uses a **first-stage only** approach: hybrid vector + FTS search produces candidates, which are scored by the hand-tuned formula, and the top-5 are returned. This is fast and cheap but limited by the quality of the static ranking weights.

**Planned addition:** A second-stage cross-encoder reranker that improves precision at the cost of a small latency increase.

```
Query →
  Stage 1 (fast recall)
    ├── pgvector HNSW cosine search → top 50
    └── PostgreSQL FTS (GIN) → top 50
        → UNION → dedup → formula score → top 20
  Stage 2 (precision)
    └── Cross-encoder reranker
        → score each (query, candidate) pair
        → rerank by relevance → top 5
        → inject into prompt
```

**Why a cross-encoder?** Bi-encoder embeddings (like nomic-embed-text) compress a document into a single vector, losing nuance. A cross-encoder processes the query and candidate together, computing a joint relevance score. This is strictly more accurate for ranking than cosine similarity.

**Architecture:**

```python
# Pseudocode for the reranking stage
async def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    pairs = [(query, c["content"]) for c in candidates]
    scores = await cross_encoder.score(pairs)
    scored = list(zip(scores, candidates))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]
```

**Candidate implementations:**

| Model | Size | Latency | Quality |
|-------|------|---------|---------|
| BAAI/bge-reranker-v2-m3 | ~2.2GB | ~50ms/pair | High (top on MTEB) |
| BAAI/bge-reranker-v2-minicpm-1b | ~1.1GB | ~30ms/pair | Good |
| ms-marco-MiniLM-L-6-v2 | ~80MB | ~10ms/pair | Adequate (lightweight) |

All can run locally via HuggingFace transformers or ONNX. No GPU required for batch sizes of 1-5 pairs.

**Integration:** The reranker is an additive stage. The first-stage hybrid search (HNSW + FTS → formula → top 20) remains unchanged. The cross-encoder reranks only the top 20, adding ~200ms-1s of latency depending on model size. If the reranker is unavailable or too slow, the system falls back to the formula-scored top-5.

**Evaluation:** Offline A/B comparison using the benchmark suite. Metric: NDCG@5 comparing formula-only vs formula + reranker rankings against human-judged relevance.

### Deferred (Post-MVP)

- Cross-encoder reranking
- Learning-to-rank (train weights from user interaction data)
- Memory selection models (classify "should this be stored?")
- Automatic instinct generation from behavioral patterns

### Design

Instincts are user-defined procedural modules stored in `~/.memorydog/instincts.toml`. They activate based on keyword triggers and influence both retrieval and agent behavior.

**No automatic discovery in MVP.** Pattern detection from behavioral data is deferred. The concept is demonstrated through manual instincts. "Future work: automatic instinct discovery" is an excellent interview answer.

### Format

```toml
[[instincts]]
name = "AI Evaluation Expert"
description = "Prioritizes benchmarks, metrics, and ablation studies"
triggers = ["benchmark", "evaluation", "metric", "ablation"]
prompt = """
When working on evaluation-related tasks:
- Consider benchmarks, metrics, and ablation studies
- Prioritize reproducibility and standard evaluation protocols
- Suggest statistical significance testing where applicable
"""
retrieval_bias = ["benchmark", "evaluation", "metric", "ablation", "accuracy", "precision", "recall"]

[[instincts]]
name = "Bug Hunter"
triggers = ["bug", "race condition", "deadlock", "fix", "debug", "crash"]
prompt = """
When fixing bugs:
- Add a regression test before fixing
- Check for similar issues in related code paths
- Consider edge cases and concurrency
"""
retrieval_bias = ["bug", "fix", "debug", "test", "regression", "concurrency"]

[[instincts]]
name = "NeuralGomoku Expert"
triggers = ["neuralgomoku", "mcts", "self-play", "gomoku"]
prompt = """
When working on NeuralGomoku:
- Inspect MCTS implementation, self-play pipeline, training loop, model architecture
- Check evaluation metrics before proposing changes
"""
retrieval_bias = ["mcts", "self-play", "training", "model", "evaluation", "gomoku"]
```

### Activation

At the start of each agent turn:

1. Match user query + workspace name against all instinct triggers
2. Activate instincts with ≥ 1 matching trigger, max 3 active
3. Active instincts produce two effects:
   - **Retrieval bias:** Augment retrieval query with `retrieval_bias` terms
   - **Prompt injection:** Insert `prompt` text into system prompt (wrapped in `[ACTIVE INSTINCT: name]` block)
4. Log activation to `instinct_activations` table

---

## Agent Behavior

### Execution Loop

```
1. 🐕 Load instincts (TOML), match triggers, activate
2. 🐕 Fetch memories (hybrid retrieval, biased by active instincts)
3. Construct system prompt (base + memories + instinct prompts + tools + workspace)
4. LLM call via LiteLLM (streaming to TUI)
5. If plan block emitted → render Rich panel, continue
6. If tool call → execute locally → back to step 4 with tool results
7. If final response → extract memories from turn → store to DB
8. Display response, update status bar
```

### Plan Visibility

When the agent begins a multi-step task, it emits a JSON plan block. The CLI parses it and renders a Rich panel. Detailed reasoning is internal — never emitted.

```
🐕 Plan:
  1. Inspect relevant files
  2. Understand current implementation
  3. Apply changes
  4. Run tests
  5. Verify results
```

### Dog Persona Integration

The persona appears in **chrome only** — never in agent responses to the user:

| Trigger | Message |
|---------|---------|
| Memory retrieval start | 🐕 Fetching memories... |
| Memories found | 🐕 Found N related memories |
| Workspace recognized | 🐕 I remember this project. N past conversations. |
| High-value memory created | 🐕 Learned a new trick |
| Instinct activated | 🐕 Instinct activated: [name] |
| Memory stored | 🐕 Remembered that. |
| Status bar (always) | 🐕 Ready. N memories. M instincts. workspace: [name] |

### Memory Store / Ignore Policy

**Store when:** Design decisions made, bugs identified/fixed, user preferences stated, non-trivial implementation details, explicit "remember" command.

**Ignore when:** Small talk, greetings, transient debug state (current variable values), duplicate facts, tool output that's not semantically meaningful.

---

## Roadmap — 6 Weeks to Full Demo

### Week 1: Working Coding Agent

**Goal:** A CLI coding agent that edits code, runs commands, and fixes bugs via LiteLLM.

- Textual TUI with conversation pane
- Agent loop (user → LLM → tool → LLM → response)
- LiteLLM provider integration
- 6 tools: read, write, edit, bash, glob, grep
- Config: `~/.memorydog/config.toml`
- `dog config`, `dog chat` commands

**Resume bullet:** "Built a coding agent CLI with Textual TUI and LiteLLM multi-provider integration supporting 6 tools (read, write, edit, bash, glob, grep)."

### Week 2: Persistent Memory

**Goal:** The agent remembers things across sessions.

- PostgreSQL + pgvector setup (Docker Compose)
- Database schema (migration)
- Memory CRUD: store, retrieve by vector similarity
- LLM-based memory extraction from conversations
- Conversation persistence
- Dog persona chrome (🐕 status messages)
- Workspace awareness (current directory → workspace_name)
- Memory deduplication on insert

**Resume bullet:** "Designed a vector-backed persistent memory system with automatic fact extraction from conversations stored in PostgreSQL with pgvector."

### Week 3: Hybrid Retrieval + Instincts

**Goal:** Smart retrieval and reusable behavioral modules.

- Hybrid ranking formula (vector + BM25 + recency + importance + workspace boost)
- PostgreSQL full-text search integration
- Multi-stage retrieval triggers
- Instinct TOML engine (load, match, activate, bias, inject)
- Instinct-guided retrieval bias
- Retrieval budget controls (max 3 extra, max 10 total)

**Resume bullet:** "Implemented hybrid retrieval combining vector similarity, BM25 keyword search, recency weighting, and importance scoring with configurable ranking. Built an instinct system that activates user-defined procedural modules to bias retrieval and guide agent behavior."

### Week 4: TUI Polish + CLI Demo

**Goal:** A polished, demo-ready CLI product.

- Multi-pane layout (conversation, file preview, tool output)
- Plan visibility (Rich panel from JSON plan blocks)
- Status bar with memory/instinct counts
- README with architecture diagram, setup instructions, demo GIF
- Demo video: show memory persisting across sessions

**Resume bullet:** "Developed a multi-pane developer TUI with real-time file preview, diff viewing, and memory/instinct status indicators."

### Week 5-6: VS Code Extension

**Goal:** A VS Code extension with animated dog mascot and memory browser.

- Extension boilerplate (package.json, activation, commands)
- Sidebar webview panels (memory browser, instinct viewer, dog status)
- Animated dog mascot (idle, sniffing, excited, sleeping states via CSS/JS)
- Python subprocess communication (stdin/stdout JSON-RPC)
- Refactor into `memorydog-core` shared package

**Resume bullet:** "Built a VS Code extension with animated mascot interface, sidebar memory browser, and instinct viewer — both frontends share a single memorydog-core Python library with zero logic duplication."

### Post-MVP (Optional)

- Memory consolidation (summarization)
- Confidence scoring
- Behavioral pattern detection → automatic instinct generation
- Cross-encoder reranking
- Benchmarks (4-task suite)

---

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Language | Python 3.11+ | Dominant in AI/ML, rich ecosystem |
| CLI Framework | Textual + Rich | Multi-pane TUI, professional look |
| LLM Provider | LiteLLM | 100+ providers, one interface |
| Database | PostgreSQL 16 + pgvector | Vector + relational in one DB, FTS built in |
| Embeddings | OpenAI text-embedding-3-small | 1536d, cheap, no GPU |
| DB Driver | asyncpg + SQLAlchemy 2.0 | Async, industry standard |
| Config | TOML (tomllib) | Python stdlib, human-readable |
| Packaging | uv or poetry | Fast, lockfiles |
| Testing | pytest + pytest-asyncio | Industry standard |
| Linting | ruff | Fast, all-in-one |

### Dependencies

```
textual, rich, litellm, sqlalchemy[asyncio], asyncpg, pgvector,
openai, pydantic, pytest, pytest-asyncio, ruff
```

---

## Folder Structure

```
memorydog/              # monorepo
├── core/               # memorydog-core — shared Python package, zero UI
│   ├── agent_loop.py
│   ├── tools.py
│   ├── provider.py
│   ├── memory.py
│   ├── retrieval.py
│   ├── ranking.py
│   ├── instincts.py
│   ├── db.py
│   └── context.py
├── cli/                # memorydog-cli — Textual TUI frontend
│   ├── main.py
│   ├── app.py
│   └── ui/
│       ├── chat.py
│       └── widgets.py
├── vscode/             # memorydog-vscode — TypeScript extension
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/extension.ts
│   ├── src/webview/
│   └── assets/dog/
├── migrations/
│   └── 001_init.sql
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## Benchmarking (Deferred)

If time permits, a 4-task A/B comparison (memory ON vs OFF) measuring task success, context retention, preference adherence, and completion time. Not required for the MVP demo — the agent remembering across sessions is itself the proof.

---

## Resume Description

**MemoryDog** — *Python, PostgreSQL, pgvector, LiteLLM, Textual*

Designed and built a memory-augmented coding agent with persistent long-term memory across sessions and projects. Implemented a hybrid retrieval pipeline combining vector similarity (pgvector HNSW), BM25 keyword search, recency weighting, and importance scoring with configurable ranking formulas. Built an automatic memory extraction system that identifies and stores design decisions, bugs, and user preferences from conversations. Designed an instinct system that activates user-defined procedural modules to bias retrieval and guide agent behavior based on task context. Developed a multi-pane Textual-based TUI with real-time conversation, file preview, and tool execution monitoring. Achieved cross-session context retention beyond stateless agent capabilities using the same underlying LLM.
