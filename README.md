# MemoryDog

A memory-augmented coding agent that gets better the longer you work with it.

Unlike stateless coding agents constrained by context windows, MemoryDog remembers previous conversations, design decisions, bugs, implementation details, and project history across sessions.

The mascot is a dog because the agent "fetches" memories.

```
🐕 Fetching memories...
🐕 Found related implementation from 12 days ago.
🐕 I remember this project.
```

## Features

- **Persistent memory** — facts survive across sessions via PostgreSQL + pgvector
- **Hybrid retrieval** — vector similarity + BM25 keyword search + recency + importance scoring
- **Instincts** — user-defined reusable modules that guide agent behavior
- **Multi-provider LLM** — OpenAI, Anthropic, Gemini, DeepSeek, Ollama via LiteLLM
- **Developer TUI** — multi-pane Textual interface (conversation, file preview, tool output)
- **6 tools** — read, write, edit, bash, glob, grep, plus memory_search

## Architecture

```
CLI (Textual TUI + Agent Loop + Tools + Memory Layer)
  │
  └── asyncpg ── PostgreSQL 16 + pgvector
```

Single process. No server, no workers, no Redis. Direct database connection.

## Quick Start

```bash
# Start PostgreSQL
docker compose up -d

# Configure
dog config

# Start coding
dog chat
```

## Configuration

`~/.memorydog/config.toml`:

```toml
[provider]
api_base = "https://api.anthropic.com"
api_key = "sk-..."
model = "claude-sonnet-4-20250514"

[embedding]
provider = "openai"
model = "text-embedding-3-small"

[database]
url = "postgresql+asyncpg://memorydog:memorydog@localhost:5432/memorydog"
```

## Instincts

Define reusable behavioral modules in `~/.memorydog/instincts.toml`:

```toml
[[instincts]]
name = "AI Evaluation Expert"
triggers = ["benchmark", "evaluation", "metric", "ablation"]
prompt = "Consider benchmarks, metrics, and ablation studies."
retrieval_bias = ["benchmark", "evaluation", "metric"]
```

## Design

See [`docs/specs/2026-05-31-memorydog-mvp.md`](docs/specs/2026-05-31-memorydog-mvp.md) for the full MVP design specification.

## License

MIT
