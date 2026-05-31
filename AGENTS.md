# AGENTS.md — MemoryDog Development Guidelines

## Project Context

MemoryDog is a memory-augmented coding agent. It is a single-user CLI tool (Python) that connects directly to PostgreSQL + pgvector via asyncpg. No server, no Redis, no background workers in MVP.

## Architecture

```
cli/main.py          — entry point, CLI args (dog chat, dog config)
cli/app.py           — Textual app bootstrap
cli/agent_loop.py    — core execution loop
cli/context.py       — prompt construction
cli/tools.py         — all 7 tools
cli/provider.py      — LiteLLM wrapper
cli/db.py            — PostgreSQL connection, queries
cli/memory.py        — memory CRUD, extraction, retrieval
cli/ranking.py       — hybrid ranking formula
cli/instincts.py     — TOML loader, activation, retrieval bias
cli/ui/chat.py       — Textual screen
cli/ui/widgets.py    — custom widgets
```

## Conventions

- Python 3.11+, async where possible
- asyncpg for database, not SQLAlchemy ORM (raw SQL preferred for simplicity)
- LiteLLM for all LLM calls (never call OpenAI/Anthropic directly)
- TOML for config and instincts (tomllib in stdlib)
- Ruff for linting, pytest for testing
- Dog persona emoji (🐕) in status messages only — never in agent responses to user
- Status messages go through a `status(message)` helper, not direct print

## Design Spec

Read `docs/specs/2026-05-31-memorydog-mvp.md` before making architectural changes.

## MVP Scope

4-week target. Do not add:
- Servers (FastAPI, Flask)
- Redis, message queues, background workers
- Multi-user, auth, API keys
- Complex memory relations or confidence scoring
- Automatic instinct generation

## Testing

```bash
pytest tests/
```

## Linting

```bash
ruff check cli/ tests/
```
