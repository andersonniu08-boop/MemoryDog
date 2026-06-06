"""Memory storage, extraction, and retrieval."""

import json
import re
import uuid
from dataclasses import dataclass, field

from core.db import get_pool
from core.provider import BaseProvider, Message

MEMORY_EXTRACTION_PROMPT = """\
Extract factual memories from this conversation turn.

Return a JSON array of objects. Use ONLY valid JSON inside a ```json code block.

Format:
```json
[
  {{
    "type": "...",
    "content": "What was learned or decided (one sentence)",
    "summary": "Very short label",
    "importance": 0.0-1.0,
    "tags": ["tag1", "tag2"]
  }}
]
```

Valid types: design_decision, learned_fact, bug, user_preference,
code_snippet, task_history, conversation

Rules:
- Design decisions and bugs: importance > 0.6
- User preferences: importance > 0.5
- Skip small talk, greetings, transient state
- Return empty array [] if nothing worth remembering

Conversation:
{conversation}"""


@dataclass
class MemoryRecord:
    id: str = ""
    content: str = ""
    summary: str = ""
    memory_type: str = "conversation"
    workspace_name: str = ""
    importance: float = 0.5
    access_count: int = 0
    tags: list[str] = field(default_factory=list)


async def store_memory(
    content: str,
    summary: str,
    memory_type: str,
    workspace_name: str,
    importance: float = 0.5,
    tags: list[str] | None = None,
    embedding: list[float] | None = None,
) -> str:
    """Store a memory with its embedding vector. Returns memory id."""
    pool = await get_pool()
    memory_id = str(uuid.uuid4())

    tags_list = tags or []

    if embedding:
        emb_str = "[" + ",".join(str(e) for e in embedding) + "]"
        await pool.execute(
            """
            INSERT INTO memories (id, content, summary, memory_type, workspace_name,
                                  importance, tags, embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
            """,
            memory_id,
            content,
            summary,
            memory_type,
            workspace_name,
            importance,
            tags_list,
            emb_str,
        )
    else:
        await pool.execute(
            """
            INSERT INTO memories (id, content, summary, memory_type, workspace_name,
                                  importance, tags)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            memory_id,
            content,
            summary,
            memory_type,
            workspace_name,
            importance,
            tags_list,
        )

    return memory_id


async def check_duplicate(content: str, threshold: float = 0.95) -> bool:
    """Check if a similar memory already exists using embedding similarity."""
    embedding = await generate_embedding(content)
    if not embedding:
        return False

    pool = await get_pool()
    emb_str = "[" + ",".join(str(e) for e in embedding) + "]"
    row = await pool.fetchrow(
        """
        SELECT 1 FROM memories
        WHERE 1 - (embedding <=> $1::vector) > $2
        LIMIT 1
        """,
        emb_str,
        threshold,
    )
    return row is not None


async def generate_embedding(text: str) -> list[float] | None:
    """Generate embedding using the configured provider."""
    from core.config import load_config

    config = load_config()
    model = config.embedding.model

    try:
        return await _ollama_embedding(model, text)
    except Exception:
        return None


async def _ollama_embedding(model: str, text: str) -> list[float] | None:
    """Generate embedding via local Ollama API. Auto-pulls model if missing."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:11434/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=30,
        )

        if resp.status_code == 404:
            await _ollama_pull_model(model)
            resp = await client.post(
                "http://localhost:11434/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=60,
            )

        resp.raise_for_status()
        data = resp.json()
        return data.get("embedding")


async def _ollama_pull_model(model: str) -> None:
    """Pull an Ollama model if not already available."""
    import httpx

    async with httpx.AsyncClient() as client:
        await client.post(
            "http://localhost:11434/api/pull",
            json={"model": model},
            timeout=300,
        )


async def check_ollama() -> str | None:
    """Check if Ollama is reachable. Returns status string or None if OK."""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:11434/api/tags", timeout=5)
            if resp.status_code == 200:
                return None
            return "Ollama API returned unexpected status"
    except httpx.ConnectError:
        return "Ollama not running (start with: ollama serve)"
    except Exception as e:
        return f"Ollama error: {e}"


async def extract_memories(
    provider: BaseProvider,
    user_message: str,
    assistant_response: str,
    workspace_name: str,
) -> list[MemoryRecord]:
    """Extract memories from a conversation turn using LLM.

    Uses provider-agnostic parsing that handles JSON in markdown fences,
    extra prose, partial arrays, and malformed output from any provider.
    """
    conversation = f"User: {user_message}\nAssistant: {assistant_response}"
    prompt = MEMORY_EXTRACTION_PROMPT.format(conversation=conversation)

    messages = [Message(role="system", content=prompt)]
    response = await provider.chat_async(messages, tools=None)

    raw = response.content.strip() if response.content else ""
    if not raw:
        return []

    items = _parse_memory_json(raw)
    return _validate_memory_records(items, workspace_name)


def _parse_memory_json(raw: str) -> list[dict]:
    """Parse LLM output into a list of memory dicts, handling common failure modes."""

    # Failure mode 1: Markdown fences (```json ... ```)
    raw = _strip_markdown_fence(raw)

    # Failure mode 2: Extra prose before/after JSON
    raw = _extract_json_block(raw)

    if not raw:
        return []

    # Failure mode 3: Single object instead of array: {...} → [{...}]
    raw = _wrap_single_object(raw)

    # Failure mode 4: Trailing comma before closing bracket
    raw = re.sub(r",\s*([\]}])", r"\1", raw)

    # Failure mode 5: Unquoted keys (common with smaller models)
    raw = _fix_unquoted_keys(raw)

    # Failure mode 6: Single quotes instead of double quotes
    raw = raw.replace("'", '"')

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Failure mode 7: Try to find any JSON array in the text
        data = _find_json_array(raw)
        if data is None:
            return []

    if not isinstance(data, list):
        data = [data]

    return data


def _strip_markdown_fence(text: str) -> str:
    """Remove markdown code block fences, handling optional language tag."""
    # ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _extract_json_block(text: str) -> str:
    """Find the first JSON array or object in text with surrounding prose."""
    # Try to find [...] array first
    m = re.search(r"(\[[\s\S]*\])", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fall back to {...} object
    m = re.search(r"(\{[\s\S]*\})", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _wrap_single_object(text: str) -> str:
    """If text is a single JSON object {...}, wrap it in an array."""
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return f"[{stripped}]"
    return text


def _fix_unquoted_keys(text: str) -> str:
    """Quote unquoted JSON keys (common with smaller models)."""
    # Pattern: {key: value} → {"key": value}
    return re.sub(r"(\{|\,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', text)


def _find_json_array(text: str) -> list | None:
    """Try to find any valid JSON array within text."""
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Try to find any valid JSON entirely
    m = re.search(r"\{[\s\S]*?\}", text)
    if m:
        candidate = m.group(0)
        try:
            result = json.loads(candidate)
            return [result]
        except json.JSONDecodeError:
            pass

    return None


def _validate_memory_records(
    items: list[dict],
    workspace_name: str,
) -> list[MemoryRecord]:
    """Convert parsed dicts to MemoryRecords, filtering invalid entries."""
    valid_types = {
        "design_decision",
        "learned_fact",
        "bug",
        "user_preference",
        "code_snippet",
        "task_history",
        "conversation",
    }

    records = []
    for item in items:
        if not isinstance(item, dict):
            continue

        content = _safe_str(item.get("content"))
        if not content or len(content) < 5:
            continue

        memory_type = _safe_str(item.get("type"), "conversation")
        if memory_type not in valid_types:
            memory_type = "conversation"

        importance = _safe_float(item.get("importance"), 0.5)
        importance = max(0.0, min(1.0, importance))

        summary = _safe_str(item.get("summary"), "")[:200]
        if not summary:
            summary = content[:80]

        tags_raw = item.get("tags", [])
        if isinstance(tags_raw, str):
            tags_raw = [tags_raw]
        tags = [str(t)[:50] for t in (tags_raw if isinstance(tags_raw, list) else [])]

        record = MemoryRecord(
            id=str(uuid.uuid4()),
            content=content,
            summary=summary,
            memory_type=memory_type,
            workspace_name=workspace_name,
            importance=importance,
            tags=tags[:10],
        )
        records.append(record)

    return records


def _safe_str(value, default: str = "") -> str:
    if value is None:
        return default
    try:
        return str(value).strip()
    except Exception:
        return default


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


async def store_extracted_memories(
    records: list[MemoryRecord],
    skip_duplicates: bool = True,
) -> int:
    """Store extracted memories with embeddings. Returns count stored."""
    count = 0
    for record in records:
        if record.importance < 0.2:
            continue
        if skip_duplicates and await check_duplicate(record.content):
            continue
        embedding = await generate_embedding(record.content)
        await store_memory(
            content=record.content,
            summary=record.summary,
            memory_type=record.memory_type,
            workspace_name=record.workspace_name,
            importance=record.importance,
            tags=record.tags,
            embedding=embedding,
        )
        count += 1
    return count


async def count_memories(workspace: str) -> int:
    """Count memories for a workspace."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT COUNT(*) FROM memories WHERE workspace_name = $1",
        workspace,
    )
    return row[0] if row else 0


async def count_instinct_activations() -> int:
    """Count total instinct activations."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT COUNT(*) FROM instinct_activations")
    return row[0] if row else 0
