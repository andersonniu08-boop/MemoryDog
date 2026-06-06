# AGENTS.md — MemoryDog Development Guidelines

## Architecture

```
core/                MemoryDog Core — shared library, zero UI
  agent_loop.py      Core execution loop + memory extraction + streaming
  tools.py           7 tools (read, write, edit, bash, glob, grep, memory_search)
  provider.py        BaseProvider, MockProvider, LiteLLMProvider
  memory.py          Memory CRUD, extraction, embedding, parsing
  retrieval.py       Hybrid vector + FTS retrieval
  ranking.py         Score formula: 0.35V + 0.20B + 0.15R + 0.15I + 0.10W + 0.05F
  instincts.py       TOML loader, keyword trigger matching, bias + prompt injection
  db.py              asyncpg pool, auto-migration
  context.py         Prompt construction

cli/                 Textual TUI frontend (imports core)
  main.py            Entry point (dog chat, config, status, install)
  app.py             Textual app bootstrap
  ui/chat.py         Chat screen with live status + streaming
  ui/widgets.py      StatusBar, PlanPanel, DiffPreview, ToolOutput

vscode/              VS Code extension (TypeScript)
  src/extension.ts   Extension entry, terminal + webview
  src/webview/       Sidebar panels (HTML/JS)
```

**Both frontends import `core/`.** No duplication of agent logic, memory, retrieval, or instincts.

## Conventions

- Python 3.11+, async where possible
- asyncpg for database (raw SQL, not SQLAlchemy ORM)
- LiteLLM for all LLM calls — never call providers directly
- TOML for config and instincts (tomllib in stdlib)
- Ruff for linting, pytest for testing
- 🐕 Dog persona in status chrome only — never in agent responses to user
- Status messages through `dog_status(message)` / `on_status` callback
- Memory extraction uses `_parse_memory_json()` for provider-agnostic parsing

## MVP Scope — Implemented

The following are complete and working:
- LiteLLM multi-provider integration
- 7 tools with error-safe execution
- Persistent memory with PostgreSQL + pgvector
- Hybrid retrieval (vector + FTS + ranking)
- Local embeddings via Ollama + nomic-embed-text
- Memory extraction with defensive JSON parsing
- Workspace awareness (ranking boost, not hard filter)
- Instinct engine (TOML, keyword triggers, retrieval bias, prompt injection)
- Streaming responses via `chat_stream()` in background thread
- Live status updates through `on_status`/`on_token` callbacks
- Animated thinking indicator in TUI
- `dog install` for PATH installation
- API key diagnostics in `dog status`
- Cross-session memory recall
- 42 parsing tests + 38 original tests = 80 total

## Out of MVP Scope

Do not add (yet):
- FastAPI, Flask, or any server
- Redis, message queues, background workers
- Multi-user, auth, API keys
- Memory relations, confidence scoring
- Automatic instinct generation

## Planned Enhancements

### Cross-Encoder Reranking (post-MVP)

Add a second-stage reranker after the current hybrid retrieval to improve ranking precision. See `docs/specs/2026-05-31-memorydog-mvp.md` for the full design.

**Rationale:** Bi-encoder embeddings (nomic-embed-text) lose nuance by compressing documents into single vectors. A cross-encoder processes query+candidate jointly and produces strictly more accurate relevance scores.

**Implementation sketch:**

```python
# New file: core/reranking.py
# Dependencies: torch, transformers, sentence-transformers

class CrossEncoderReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model = CrossEncoder(model_name)

    async def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
        pairs = [(query, c["content"]) for c in candidates]
        scores = self.model.predict(pairs)
        scored = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]
```

**Integration into retrieval pipeline:**
1. First-stage: hybrid search → formula score → top 20 (unchanged)
2. Second-stage: cross-encoder rerank top 20 → top 5
3. Fallback: if reranker unavailable, return formula-scored top 5

**Models considered:** BAAI/bge-reranker-v2-m3 (high quality, ~2.2GB), ms-marco-MiniLM-L-6-v2 (lightweight, ~80MB)

## Design Spec

Read `docs/specs/2026-05-31-memorydog-mvp.md` for the full design rationale.

## Testing

```bash
pytest tests/                          # 80 tests
python -m tests.benchmarks.harness     # A/B benchmarks
```

## Linting

```bash
ruff check core/ cli/ tests/
ruff format core/ cli/ tests/
```
