"""Hybrid memory retrieval query, budgeting, and logging."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from core.db import get_pool
from core.ranking import score_memory


@dataclass
class RetrievalBudget:
    """Tracks retrieval calls per turn with budget limits."""

    triggered_count: int = 0
    total_count: int = 0
    max_triggered: int = 3
    max_total: int = 10

    def can_trigger(self) -> bool:
        return self.triggered_count < self.max_triggered and self.total_count < self.max_total

    def record(self, triggered: bool = False):
        self.total_count += 1
        if triggered:
            self.triggered_count += 1

    def remaining_triggered(self) -> int:
        return max(0, self.max_triggered - self.triggered_count)

    def __str__(self) -> str:
        return (
            f"retrievals: {self.total_count}/{self.max_total}"
            f" (triggered: {self.triggered_count}/{self.max_triggered})"
        )


RETRIEVAL_LOG: list[dict] = []


def log_retrieval_event(event: dict):
    """Record a retrieval event for debugging."""
    event["timestamp"] = datetime.now(UTC).isoformat()
    RETRIEVAL_LOG.append(event)
    if len(RETRIEVAL_LOG) > 100:
        RETRIEVAL_LOG[:50] = []


def get_retrieval_log(limit: int = 20) -> list[dict]:
    return RETRIEVAL_LOG[-limit:]


def clear_retrieval_log():
    RETRIEVAL_LOG.clear()


def extract_trigger_terms(tool_results: list[dict]) -> list[str]:
    """Extract potentially unfamiliar terms from tool outputs for triggered retrieval.

    Scans file contents, command output, grep matches, glob results.
    Returns a deduplicated list of candidate terms.
    """
    terms = set()

    for tr in tool_results:
        result = tr.get("result", {})
        if not isinstance(result, dict):
            continue
        content = (
            result.get("content", "") or result.get("stdout", "") or result.get("stderr", "") or ""
        )
        content_str = str(content)

        # Extract identifiers (snake_case, camelCase, PascalCase)
        ids = re.findall(r"[a-z_][a-z0-9_]{3,}(?:_[a-z0-9_]+)*", content_str)
        for i in ids:
            if not _is_common_word(i):
                terms.add(i)

        ids = re.findall(r"[A-Z][a-z]+(?:[A-Z][a-z]+)+", content_str)
        for i in ids:
            if len(i) > 4:
                terms.add(i.lower())

        # Extract file paths (paths with extensions or depth > 1)
        paths = re.findall(r"(?:/[\w.-]+)+|(?:\.[\w.-]+(?:/[\w.-]+)+)", content_str)
        for p in paths:
            parts = p.strip("/").split("/")
            for part in parts:
                if re.match(r"^[a-z][a-z0-9_]{3,}$", part) and not _is_common_word(part):
                    terms.add(part)

    return sorted(terms)[:20]


COMMON_WORDS = {
    "the",
    "this",
    "that",
    "with",
    "from",
    "file",
    "path",
    "name",
    "data",
    "code",
    "line",
    "list",
    "none",
    "true",
    "false",
    "null",
    "error",
    "success",
    "result",
    "value",
    "type",
    "class",
    "self",
    "args",
    "kwargs",
    "return",
    "input",
    "output",
    "index",
    "count",
    "total",
    "found",
    "content",
    "string",
    "number",
    "object",
    "items",
    "item",
    "key",
    "text",
    "size",
    "main",
    "test",
    "done",
    "time",
    "help",
    "info",
    "hello",
    "world",
    "temp",
    "file",
    "path",
    "user",
    "home",
}


def _is_common_word(word: str) -> bool:
    return word.lower() in COMMON_WORDS


async def triggered_retrieval(
    tool_results: list[dict],
    workspace: str,
    budget: RetrievalBudget,
    existing_memory_ids: set[str],
) -> list[dict]:
    """Run triggered retrieval based on tool output terms, respecting budget.

    Extracts unfamiliar terms from tool results, runs hybrid retrieval
    for each trigger term, deduplicates against already-seen memories.
    """
    if not budget.can_trigger():
        return []

    trigger_terms = extract_trigger_terms(tool_results)
    if not trigger_terms:
        return []

    log_retrieval_event(
        {
            "type": "triggered_start",
            "terms": trigger_terms[:5],
            "term_count": len(trigger_terms),
        }
    )

    seen_ids = set(existing_memory_ids)
    new_results = []

    for term in trigger_terms:
        if not budget.can_trigger():
            log_retrieval_event({"type": "triggered_budget_exhausted", "term": term})
            break

        budget.record(triggered=True)

        from core.memory import generate_embedding

        embedding = await generate_embedding(term)
        results = await retrieve_memories(
            query=term,
            workspace=workspace,
            query_embedding=embedding,
            limit=3,
        )

        triggered_ids = set()
        for r in results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                triggered_ids.add(r["id"])
                new_results.append(r)

        log_retrieval_event(
            {
                "type": "triggered_run",
                "term": term,
                "results": len(triggered_ids),
                "budget": str(budget),
            }
        )

    return new_results


async def retrieve_memories(
    query: str,
    workspace: str,
    query_embedding: list[float] | None = None,
    bias_terms: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """Run hybrid retrieval: vector + FTS, union, rank, return top results."""
    pool = await get_pool()

    augmented = query
    if bias_terms:
        augmented = query + " " + " ".join(bias_terms)

    emb_str = None
    if query_embedding:
        emb_str = "[" + ",".join(str(e) for e in query_embedding) + "]"

    mean_access = 5.0

    if emb_str:
        rows = await pool.fetch(
            """
            WITH vector_results AS (
                SELECT id, content, summary, memory_type, workspace_name,
                       importance, decay_factor,
                       COALESCE(access_count, 0) AS access_count,
                       EXTRACT(DAY FROM NOW() - COALESCE(last_accessed, created_at))
                         AS days_since_access,
                       1 - (embedding <=> $1::vector) AS vector_score,
                       NULL::float AS bm25_score
                FROM memories
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT 50
            ),
            fts_results AS (
                SELECT id, content, summary, memory_type, workspace_name,
                       importance, decay_factor,
                       COALESCE(access_count, 0) AS access_count,
                       EXTRACT(DAY FROM NOW() - COALESCE(last_accessed, created_at))
                         AS days_since_access,
                       NULL::float AS vector_score,
                       ts_rank(to_tsvector('english', content),
                               plainto_tsquery('english', $2)) AS bm25_score
                FROM memories
                WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $2)
                LIMIT 50
            ),
            combined AS (
                SELECT * FROM vector_results
                UNION
                SELECT * FROM fts_results
            )
            SELECT * FROM combined
            WHERE id IS NOT NULL
            ORDER BY id
            """,
            emb_str,
            augmented,
        )
    else:
        rows = await pool.fetch(
            """
            WITH fts_results AS (
                SELECT id, content, summary, memory_type, workspace_name,
                       importance, decay_factor,
                       COALESCE(access_count, 0) AS access_count,
                       EXTRACT(DAY FROM NOW() - COALESCE(last_accessed, created_at))
                         AS days_since_access,
                       NULL::float AS vector_score,
                       ts_rank(to_tsvector('english', content),
                               plainto_tsquery('english', $1)) AS bm25_score
                FROM memories
                WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $1)
                LIMIT 50
            )
            SELECT * FROM fts_results
            WHERE id IS NOT NULL
            ORDER BY id
            """,
            augmented,
        )

    scored = []
    seen_ids = set()
    for row in rows:
        rid = row["id"]
        if rid in seen_ids:
            continue
        seen_ids.add(rid)

        vec = row["vector_score"] or 0.0
        bm = row["bm25_score"] or 0.0
        days = float(row["days_since_access"] or 0)
        imp = float(row["importance"] or 0.5)
        dec = float(row["decay_factor"] or 1.0)
        same_ws = row["workspace_name"] == workspace
        acc = int(row["access_count"] or 0)

        s = score_memory(vec, bm, days, imp, dec, same_ws, acc, mean_access)
        scored.append((s, dict(row)))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, d in scored[:limit]:
        results.append(
            {
                "id": d["id"],
                "content": d["content"],
                "summary": d["summary"],
                "memory_type": d["memory_type"],
                "workspace_name": d["workspace_name"],
                "importance": float(d["importance"] or 0.5),
            }
        )

    return results


def rerank_with_confidence(
    results: list[dict],
    confidence_threshold: float = 0.3,
) -> list[dict]:
    filtered = [
        r for r in results if float(r.get("importance", 0.5) or 0.5) >= confidence_threshold
    ]
    return filtered


async def log_retrieval_access(memory_ids: list[str]):
    """Increment access_count for retrieved memories."""
    if not memory_ids:
        return
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE memories
        SET access_count = COALESCE(access_count, 0) + 1,
            last_accessed = NOW(),
            decay_factor = 1.0
        WHERE id = ANY($1)
        """,
        memory_ids,
    )
