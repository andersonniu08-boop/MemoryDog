# MemoryDog

A memory-augmented coding agent that gets better the longer you work with it.

```
🐕 Fetching memories...
🐕 Found related implementation from 12 days ago.
🐕 I remember this project.
```

## Architecture

```
memorydog-core/     Shared Python library (all business logic)
memorydog-cli/      Textual TUI frontend (imports core)
memorydog-vscode/   VS Code extension frontend (imports core)
```

Both frontends share one implementation of memory, retrieval, instincts, and agent behavior. Zero duplication.

## Quick Start

```bash
# Start PostgreSQL
docker compose up -d

# Configure
dog config

# Start coding (CLI)
dog chat
```

## Configuration

`~/.memorydog/config.toml`:

```toml
[provider]
model = "anthropic/claude-sonnet-4-20250514"
api_key = "sk-..."
# api_base = "https://custom-api.example.com"  # optional

[embedding]
model = "openai/text-embedding-3-small"
api_key = "sk-..."

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
