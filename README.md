# 🐕 MemoryDog

**A memory-augmented coding agent that gets better the longer you work with it.**

Unlike stateless coding agents, MemoryDog remembers previous conversations, design decisions, bugs, and project history across sessions. It combines a persistent memory system with hybrid retrieval, a Textual-based developer TUI, and an instinct engine for reusable behavioral modules.

---

## Demo

```
Session 1:  "Remember that we chose Textual because it supports multi-pane layouts."
Session 2:  "Why did we choose Textual?"
            → "According to my memory, we chose Textual because it supports multi-pane layouts."
```

MemoryDog stores facts from conversations, embeds them via local Ollama, retrieves them with hybrid search, and injects them into the LLM context — automatically, across sessions.

---

## Architecture

MemoryDog uses a **shared core + thin frontends** model. All business logic lives in `memorydog-core`, a single Python package. Both `memorydog-cli` (Textual TUI) and `memorydog-vscode` (VS Code extension) import it. Zero duplication.

```
┌─────────────────────────────────────────────────────┐
│ memorydog-core (Python package, zero UI)            │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Agent    │  │ Memory   │  │ Retrieval Engine  │  │
│  │ Loop     │  │ CRUD     │  │ Hybrid + Ranking  │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Tools    │  │ Instinct │  │ Provider          │  │
│  │ (x7)     │  │ Engine   │  │ (LiteLLM)         │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Ranking  │  │ DB Layer │  │ Prompt Builder    │  │
│  │ Formula  │  │ (asyncpg)│  │                   │  │
│  └──────────┘  └──────────┘  └───────────────────┘  │
└──────────────────┬──────────────────┬───────────────┘
                   │                  │
                   │ imports          │ imports via subprocess
                   ▼                  ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│ memorydog-cli            │  │ memorydog-vscode              │
│ Textual TUI frontend     │  │ TypeScript extension          │
│                          │  │                              │
│ • Multi-pane layout      │  │ • Sidebar memory browser     │
│ • Conversation view      │  │ • Instinct viewer            │
│ • File preview           │  │ • Animated dog mascot        │
│ • Tool output panel      │  │ • Integrated terminal        │
│ • 🐕 Status bar          │  │ • Webview panels             │
└──────────────────────────┘  └──────────────────────────────┘
          │
          │ asyncpg
          ▼
┌──────────────────────────────────────────────────────┐
│ PostgreSQL 16 + pgvector                             │
│ 5 tables, HNSW vector index, GIN full-text search    │
└──────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **No server** | The agent is the product. A REST API adds 50% more code for zero interview value. PostgreSQL is fast enough for single-user workloads. |
| **No Redis** | HNSW indexes make vector search sub-millisecond. PostgreSQL FTS is fast. No bottleneck to solve at this scale. |
| **No background workers** | Embedding generation takes ~100ms — do it inline. The user is waiting for the agent's response anyway. |
| **LiteLLM for all LLM calls** | Never call providers directly. One interface for 100+ providers. Swap models with a config change. |
| **Local embeddings (Ollama)** | nomic-embed-text produces 768-dim vectors. No API key needed. Auto-pulls model on first use. |

---

## Memory Pipeline

```
User Message
    │
    ▼
┌─────────────────────┐
│ 1. Instinct Match   │  Keyword trigger matching against TOML-defined instincts
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 2. Memory Retrieval │  Hybrid vector + FTS search
│                     │    ↓ query embedding (Ollama)
│                     │    ↓ SQL: HNSW cosine + GIN ts_rank → UNION → rank
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 3. Context Build    │  System prompt + retrieved memories + instinct prompts
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 4. LLM Call         │  via LiteLLM (streaming to TUI)
└─────────┬───────────┘
          │
    ┌─────┴─────┐
    │           │
    ▼           ▼
  Tool        Final
  Call      Response
    │           │
    ▼           ▼
  Execute     ┌─────────────────────┐
  Tool        │ 5. Memory Extract   │  LLM extracts facts from turn
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │ 6. Embed & Store    │  Ollama → pgvector INSERT
              └─────────────────────┘
```

### Hybrid Ranking Formula

```
Score(m, q) = 0.35·V + 0.20·B + 0.15·R + 0.15·I + 0.10·W + 0.05·F
```

| Term | Weight | Definition |
|------|--------|------------|
| **V** Vector similarity | 0.35 | Cosine similarity via pgvector HNSW index |
| **B** BM25 keyword | 0.20 | PostgreSQL full-text search ranking |
| **R** Recency | 0.15 | `e^(-0.01 × days_since_last_access)` |
| **I** Importance | 0.15 | `importance × decay_factor` |
| **W** Workspace boost | 0.10 | Same workspace → 1.5×, different → 1.0× |
| **F** Frequency | 0.05 | Logistic sigmoid of relative access count |

---

## RAG Architecture

MemoryDog implements **retrieval-augmented generation (RAG)** with a hybrid search backend and cross-session persistence.

```
┌──────────────────────────────────────────────────────────┐
│                  RETRIEVAL PIPELINE                       │
│                                                           │
│  Query: "Why did we choose Textual?"                     │
│     │                                                     │
│     ▼                                                     │
│  ┌─────────────────┐    ┌──────────────────────────┐     │
│  │ Ollama Embed    │    │ Augment with instinct     │     │
│  │ nomic-embed-text│    │ bias terms + workspace    │     │
│  │ → 768-dim vec   │    │                           │     │
│  └────────┬────────┘    └──────────┬────────────────┘     │
│           │                        │                      │
│           ▼                        ▼                      │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ PostgreSQL Hybrid Query                              │  │
│  │                                                      │  │
│  │  WITH vector_results AS (                            │  │
│  │    SELECT ..., embedding <=> $1 AS cosine_score      │  │
│  │    FROM memories WHERE embedding IS NOT NULL         │  │
│  │    ORDER BY cosine_score LIMIT 50                    │  │
│  │  ),                                                  │  │
│  │  fts_results AS (                                    │  │
│  │    SELECT ..., ts_rank(...) AS bm25_score            │  │
│  │    FROM memories WHERE content @@ plainto_tsquery    │  │
│  │    LIMIT 50                                          │  │
│  │  )                                                   │  │
│  │  SELECT * FROM vector_results UNION fts_results      │  │
│  │  → rank via Score = 0.35V + 0.20B + 0.15R + ...     │  │
│  │  → return top 5                                      │  │
│  └──────────────────────┬──────────────────────────────┘  │
│                         │                                  │
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Prompt Injection                                     │  │
│  │                                                      │  │
│  │  ## Retrieved Memories                               │  │
│  │  1. [design_decision] We chose Textual because       │  │
│  │     it supports multi-pane layouts.                  │  │
│  └──────────────────────┬──────────────────────────────┘  │
│                         │                                  │
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ LLM: "According to my memory, we chose Textual       │  │
│  │ because it supports multi-pane layouts."             │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**Key properties:**
- **Asymmetric retrieval**: Query embedding ≠ stored embedding (DeepSeek query → Ollama embed → match against stored Ollama embeddings)
- **No hard workspace filter**: Cross-workspace memories get a ranking boost, not an exclusion. Relevant knowledge from other projects surfaces naturally
- **Deterministic embeddings**: nomic-embed-text returns identical vectors for identical text, enabling reliable dedup via cosine > 0.95

---

## Workspace Awareness

MemoryDog derives a `workspace_name` from your current directory. Memories are scoped with a **ranking boost**, not a hard filter:

| Condition | Score multiplier |
|-----------|-----------------|
| Same workspace | 1.5× |
| Different workspace | 1.0× |

This means relevant memories from other projects still surface, but same-project context is preferred. No workspace table, no management UI, no ownership model — just a string field.

```
Session in ~/projects/memorydog:
  → workspace: "memorydog"

  "Why did we choose asyncpg?"
  → retrieves: [design_decision] "Chose asyncpg for database access" (boost: 1.5×)
  → also sees: [learned_fact] "asyncpg is faster than psycopg2" from "neuralgomoku" (boost: 1.0×)
```

---

## Instinct System

Instincts are user-defined procedural modules stored in `~/.memorydog/instincts.toml`. They activate based on keyword triggers and influence both retrieval and agent behavior.

```toml
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
name = "AI Evaluation Expert"
triggers = ["benchmark", "evaluation", "metric", "ablation"]
prompt = """
When working on evaluation-related tasks:
- Consider benchmarks, metrics, and ablation studies
- Prioritize reproducibility and standard evaluation protocols
"""
retrieval_bias = ["benchmark", "evaluation", "metric"]
```

### Activation Flow

```
User query: "Fix the deadlock in task_queue.py"
     │
     ▼
Keyword match against instinct triggers
     │ "deadlock" → Bug Hunter ✓
     │
     ▼
Two effects:
  1. Retrieval bias: query augmented with ["bug", "fix", "concurrency", ...]
  2. Prompt injection: Bug Hunter prompt block added to system prompt
     [ACTIVE INSTINCT: Bug Hunter]
     When fixing bugs:
     - Add a regression test before fixing
     ...
```

### Default Instincts

MemoryDog ships with 3 instincts. Add your own in `~/.memorydog/instincts.toml`.

```
dog instinct list

  🐕 Instincts

  1. Bug Hunter
     Finds and fixes bugs with regression test discipline
     Triggers: bug, race condition, deadlock, fix, debug, crash

  2. AI Evaluation Expert
     Prioritizes benchmarks, metrics, and ablation studies
     Triggers: benchmark, evaluation, metric, ablation

  3. Recruiter Lens
     Interprets README structure for resume value
     Triggers: resume, interview, recruiter, job
```

---

## CLI TUI

MemoryDog's primary interface is a multi-pane Textual terminal UI.

```
┌───────────────────────────────────────────────────────┐
│ ⭘     MemoryDogApp                             15:21:36│
├──────────────────────────────────┬────────────────────┤
│ 🐕 MemoryDog ready.              │ ┌────────────────┐ │
│ 🐕 Model: deepseek/deepseek-chat │ │ File Preview   │ │
│                                  │ │                │ │
│ You: Why did we choose Textual?  │ │ content...     │ │
│ 🐕 Fetching memories...          │ │                │ │
│ 🐕 Found 1 related memories      │ └────────────────┘ │
│ 🐕 Thinking...                   │ ┌────────────────┐ │
│ MemoryDog: We chose Textual      │ │ Tool Output    │ │
│ because it supports multi-pane   │ │                │ │
│ layouts.                         │ │ $ command      │ │
│                                  │ │ stdout         │ │
│                                  │ └────────────────┘ │
├──────────────────────────────────┴────────────────────┤
│ 🐕 Ready | memorydog | 1 memory | 0 instincts | 0m | │
│ > Type your message...                                │
└───────────────────────────────────────────────────────┘
```

### Panels

| Panel | Location | Content |
|-------|----------|---------|
| **Conversation** | Left (⅔ width) | Chat history with memory status messages |
| **File Preview** | Right top | File content shown after `read` tool calls |
| **Tool Output** | Right bottom | Command stdout after `bash` tool calls |
| **Status Bar** | Bottom | Workspace, memory/instinct counts, session time, model |

### Keybindings

| Key | Action |
|-----|--------|
| `Ctrl+P` | Toggle side panels |
| `Ctrl+S` | Focus input |
| `Ctrl+Q` | Quit |

---

## VS Code Extension

The VS Code extension provides sidebar panels for browsing memories and viewing active instincts, plus an animated dog mascot.

```
┌────────────────────────────────────────────────────┐
│ Activity Bar (dog icon)                            │
│                                                    │
│ ┌────────────────────────────────────────────────┐ │
│ │ 🐕 MemoryDog                                   │ │
│ │                                                │ │
│ │ ┌────────────────────────────────────────────┐ │ │
│ │ │ Memory Browser                 [+ Add]    │ │ │
│ │ │                                            │ │ │
│ │ │ 🔍 [Search memories...]                   │ │ │
│ │ │                                            │ │ │
│ │ │ 📝 design_decision                        │ │ │
│ │ │    We chose Textual because it supports   │ │ │
│ │ │    multi-pane layouts.                    │ │ │
│ │ │    importance: 0.85  workspace: memorydog │ │ │
│ │ │                                            │ │ │
│ │ │ 🐛 bug                                     │ │ │
│ │ │    Fixed race condition in task queue     │ │ │
│ │ │    importance: 0.90  workspace: memorydog │ │ │
│ │ └────────────────────────────────────────────┘ │ │
│ │                                                │ │
│ │ ┌────────────────────────────────────────────┐ │ │
│ │ │ Instincts                                  │ │ │
│ │ │ 🔵 Bug Hunter           ● active           │ │ │
│ │ │ 🟢 AI Evaluation Expert ○ inactive         │ │ │
│ │ │ 🟡 Recruiter Lens       ○ inactive         │ │ │
│ │ └────────────────────────────────────────────┘ │ │
│ └────────────────────────────────────────────────┘ │
│                                                    │
│ 🐕 Ready                                           │
└────────────────────────────────────────────────────┘
```

The animated dog mascot uses pure CSS transitions with four states:

| State | When | Animation |
|-------|------|-----------|
| **Idle** | Waiting | Gentle breathing |
| **Sniffing** | Memory retrieval | Nose twitch, head bob |
| **Excited** | Memories found | Tail wag, ear perk |
| **Sleeping** | Inactive | Slow breathing, closed eyes |

---

## Setup Guide

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ with pgvector
- Ollama (for local embeddings)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/memorydog.git
cd memorydog

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install MemoryDog
pip install -e ".[dev]"

# Install to PATH
dog install
```

### Database Setup

**Option A: Docker (recommended)**

```bash
docker compose up -d
```

This starts PostgreSQL 16 with pgvector, creates the `memorydog` database and user, and automatically runs migrations on first start.

**Option B: Native PostgreSQL**

```bash
# Create the database and user
createdb memorydog
psql memorydog -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql memorydog -c "CREATE USER memorydog WITH PASSWORD 'memorydog';"
psql memorydog -c "GRANT ALL ON DATABASE memorydog TO memorydog;"

# Run migrations
psql memorydog -f migrations/001_init.sql
```

### Embeddings

```bash
# Install Ollama (if not already installed)
# curl -fsSL https://ollama.com/install.sh | sh

# Pull the embedding model
ollama pull nomic-embed-text

# Verify
ollama list  # should show nomic-embed-text:latest
```

### Configuration

```bash
# Run the interactive config wizard
dog config
```

This prompts for:
1. **Model** — LiteLLM model string (e.g., `deepseek/deepseek-chat`, `openai/gpt-4o`, `anthropic/claude-sonnet-4-20250514`)
2. **API key** — Provider API key (also accepts `MEMORYDOG_API_KEY` env var)
3. **API base** — Optional custom endpoint URL

Config is stored at `~/.memorydog/config.toml`:

```toml
[provider]
model = "deepseek/deepseek-chat"
api_key = "sk-..."
# api_base = "https://custom-api.example.com"  # optional

[embedding]
model = "nomic-embed-text"

[database]
url = "postgresql+asyncpg://memorydog:memorydog@localhost:5432/memorydog"
```

### Verify Installation

```bash
dog status
```

Expected output:

```
🐕 MemoryDog Status

  Provider: deepseek/deepseek-chat
  Embedding: nomic-embed-text
  Instincts: 3 loaded
  Config: ~/.memorydog/config.toml
  Instincts file: ~/.memorydog/instincts.toml
  API Key: sk-b...ce23
  ✅ Database: connected and migrated
  ✅ API key: valid
  ✅ Embeddings: Ollama connected
```

### Test the Pipeline

```bash
# Run the test suite
pytest tests/

# Output: 80 passed
```

---

## Demo Walkthrough

### Session 1: Store a Memory

```
> Remember that we chose Textual because it supports multi-pane layouts.
  🐕 Fetching memories...
  🐕 No matching memories found (total: 0)
  🐕 Thinking...
  🐕 Extracting memories...
  🐕 Saving memories...
  MemoryDog: I have stored that fact. Textual was chosen for its multi-pane layout support.
```

What happens internally:
1. The agent loop checks for matching instincts (none for this query)
2. Retrieval runs but finds nothing (first session)
3. The LLM responds to the user
4. Memory extraction identifies a `design_decision` with importance 0.85
5. The memory is embedded via Ollama (768-dim vector)
6. Stored in PostgreSQL with the vector
7. Dedup check prevents duplicates on re-storage

### Session 2: Recall Across Sessions

```
> Why did we choose Textual?
  🐕 Fetching memories...
  🐕 Found 1 related memories (total: 1)
  🐕 Thinking...
  MemoryDog: According to my memory, we chose Textual because it supports multi-pane layouts, which was a key design decision for this project.
```

What happens internally:
1. Query is embedded via Ollama: "Why did we choose Textual?" → 768-dim vector
2. Hybrid retrieval: cosine search against HNSW index UNION FTS keyword search
3. Ranking formula scores results: vector similarity (0.35) + BM25 (0.20) + recency (0.15) + importance (0.15) + workspace boost (0.10) + frequency (0.05)
4. Top memory: "Remember that we chose Textual because it supports multi-pane layouts." (score: 0.78)
5. Memory is injected into the system prompt under `## Retrieved Memories`
6. LLM reads the memory and answers the question

### Session 3: Cross-Project Knowledge

```
> How do we connect to PostgreSQL?
  🐕 Fetching memories...
  🐕 Found 1 related memory (across workspaces)
  🐕 Thinking...
  MemoryDog: Based on what I know from your other project, you used asyncpg with a connection pool. Would you like to set that up here too?
```

Cross-workspace memories are ranked at 1.0× instead of 1.5×, but they still surface when relevant. This creates a growing body of knowledge that follows you across projects.

---

## Project Structure

```
memorydog/
├── core/                  # Shared library (zero UI)
│   ├── agent_loop.py      # Core execution loop
│   ├── tools.py           # 7 tools: read, write, edit, bash, glob, grep, memory_search
│   ├── provider.py        # LiteLLM wrapper, MockProvider
│   ├── memory.py          # Memory CRUD, extraction, embedding
│   ├── retrieval.py       # Hybrid vector + FTS retrieval
│   ├── ranking.py         # 6-term ranking formula
│   ├── instincts.py       # TOML loader, activation, bias
│   ├── db.py              # asyncpg connection pool, migrations
│   └── context.py         # Prompt construction
├── cli/                   # Textual TUI frontend
│   ├── main.py            # CLI entry point (dog chat, config, status)
│   ├── app.py             # Textual app bootstrap
│   └── ui/
│       ├── chat.py        # Chat screen with live status and streaming
│       └── widgets.py     # Custom StatusBar, PlanPanel, DiffPreview
├── vscode/                # VS Code extension (TypeScript)
│   ├── src/extension.ts   # Extension entry point
│   └── src/webview/       # HTML/JS sidebar panels
├── migrations/
│   └── 001_init.sql       # 5-table schema with indexes
├── tests/
│   ├── test_tui.py        # 32 unit tests
│   ├── test_integration.py # 6 integration tests
│   ├── test_extraction.py # 42 extraction parsing tests
│   └── benchmarks/        # 4-task A/B benchmark suite
├── docker-compose.yml     # PostgreSQL 16 + pgvector
├── pyproject.toml         # Python package config, ruff, pytest
└── AGENTS.md              # Developer guidelines
```

---

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Language | Python 3.11+ | Dominant in AI/ML, rich ecosystem |
| CLI Framework | Textual + Rich | Multi-pane TUI, professional look |
| LLM Provider | LiteLLM | 100+ providers, one interface |
| Database | PostgreSQL 16 + pgvector | Vector + relational in one DB, FTS built in |
| Embeddings | Ollama + nomic-embed-text | Local, no API key, 768-dim |
| DB Driver | asyncpg | Async PostgreSQL driver |
| Config | TOML (tomllib) | Python stdlib, human-readable |
| Testing | pytest + pytest-asyncio | Industry standard |
| Linting | ruff | Fast, all-in-one |

### Dependencies

```
textual, rich, litellm, sqlalchemy[asyncio], asyncpg, pgvector,
httpx, pydantic, pytest, pytest-asyncio, ruff
```

---

## Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=core

# Run benchmarks
python -m tests.benchmarks.harness

# Lint
ruff check core/ cli/ tests/
```

Current status: **80 tests, all passing**, ruff clean.

---

## Comparison with Stateless Agents

| Capability | Stateless Agent | MemoryDog |
|------------|----------------|-----------|
| Remember past conversations | ❌ Each session starts blank | ✅ Facts persist across sessions |
| Learn user preferences | ❌ Must be re-explained | ✅ Stored and retrieved automatically |
| Cross-project knowledge | ❌ No transfer between projects | ✅ Workspace-aware ranking |
| Design decision recall | ❌ Lost after context window | ✅ Injected into every relevant prompt |
| Bug fix history | ❌ Must be re-discovered | ✅ Retrieved when similar issues arise |
| Custom behavior modules | ❌ Prompt engineering required | ✅ TOML-defined instincts |

---

## License

MIT

---

## Design Specification

For the full design rationale, database schema, and roadmap, see [`docs/specs/2026-05-31-memorydog-mvp.md`](docs/specs/2026-05-31-memorydog-mvp.md).
