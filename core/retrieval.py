"""Hybrid memory retrieval query."""
from core.db import get_pool
from core.ranking import score_memory


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
        r for r in results
        if float(r.get("importance", 0.5) or 0.5) >= confidence_threshold
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
