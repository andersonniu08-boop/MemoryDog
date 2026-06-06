# Cross-Encoder Reranking for MemoryDog

> **Status:** Planned
> **Dependencies:** torch, transformers, sentence-transformers
> **Models:** BAAI/bge-reranker-v2-m3 (high quality), ms-marco-MiniLM-L-6-v2 (lightweight)

## Rationale

The current retrieval pipeline uses bi-encoder embeddings (nomic-embed-text, 768-dim) for first-stage retrieval. Bi-encoders compress documents into single vectors, losing word-level interaction information. A cross-encoder processes query and candidate jointly, producing strictly more accurate relevance scores.

The reranker is a **second-stage** addition: the first-stage hybrid search (HNSW + FTS → formula → top 20) remains unchanged. The cross-encoder reranks only the top 20 candidates, adding ~200ms-1s of latency.

## Architecture

```
Query →
  Stage 1 (fast recall):     pgvector HNSW + FTS → formula → top 20
  Stage 2 (precision):       Cross-encoder reranker → rerank → top 5
  Injection:                 Top 5 into system prompt
```

## File Changes

### New file: `core/reranking.py`

Cross-encoder wrapper with model loading, scoring, and fallback:

```python
"""Cross-encoder reranking for second-stage memory retrieval."""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class Reranker:
    """Cross-encoder reranker for improving retrieval precision."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self.model = None

    def _load(self):
        if self.model is None:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(self.model_name, device=self.device)

    async def rerank(
        self, query: str, candidates: list[dict], top_k: int = 5
    ) -> list[dict]:
        """Rerank candidates by cross-encoder relevance score.

        Args:
            query: The search query.
            candidates: List of memory dicts with 'content' key.
            top_k: Number of top results to return.

        Returns:
            Reranked candidates, highest relevance first.
        """
        if not candidates:
            return []

        self._load()
        pairs = [(query, c["content"]) for c in candidates]

        try:
            scores = self.model.predict(pairs)
            scored = list(zip(scores, candidates))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in scored[:top_k]]
        except Exception as e:
            logger.warning(f"Reranker failed, falling back to formula scoring: {e}")
            return candidates[:top_k]

    @property
    def is_loaded(self) -> bool:
        return self.model is not None
```

### Modified: `core/retrieval.py`

Add reranking step after hybrid search:

```python
# In retrieve_memories(), after scoring and before returning top results:
if RERANKER_ENABLED and len(scored) > 0:
    candidates = [dict(row) for _, row in scored[:20]]  # top 20 for reranker
    reranked = await reranker.rerank(query, candidates, top_k=limit)
    return reranked
else:
    return [dict(row) for _, row in scored[:limit]]
```

### Modified: `core/config.py`

Add reranker configuration section:

```toml
[reranking]
enabled = true
model = "BAAI/bge-reranker-v2-m3"
top_k = 20
```

### New dependencies in `pyproject.toml`

```
sentence-transformers>=3.0
torch>=2.0
```

## Test Plan

### Unit tests in `tests/test_reranking.py`

1. **`test_reranker_initialization`** — Model loads with default params
2. **`test_reranker_scores_relevant_higher`** — Related query+candidate gets higher score than unrelated
3. **`test_reranker_reranks_correctly`** — Top result after reranking is the most relevant
4. **`test_reranker_fallback_on_failure`** — Returns formula-scored top-k when model unavailable
5. **`test_reranker_empty_input`** — Empty candidates returns empty list
6. **`test_reranker_top_k_respected`** — Returns exactly top_k results

### Integration test

7. **`test_retrieval_with_reranker`** — Full pipeline: store → retrieve → rerank → verify ordering improved

## Acceptance Criteria

- [ ] Reranker loads specified model on first use (lazy load)
- [ ] Reranker scores (query, candidate) pairs and returns scores
- [ ] Reranked results are ordered by descending relevance score
- [ ] Reranker falls back gracefully when model unavailable (returns formula-scored top-5)
- [ ] Config toggle enables/disables reranking
- [ ] Pipeline latency with reranker < 2s total (stage 1 + stage 2)
- [ ] NDCG@5 improves over formula-only baseline on benchmark suite

## Future Considerations

### Model selection

| Model | Params | Size | Quality (NDCG@10 on MTEB) |
|-------|--------|------|--------------------------|
| BAAI/bge-reranker-v2-m3 | 568M | ~2.2GB | 60.86 (top) |
| BAAI/bge-reranker-v2-minicpm-1b | 1.1B | ~1.1GB (int8) | 59.94 |
| ms-marco-MiniLM-L-6-v2 | 22M | ~80MB | ~47 (adequate) |

Start with MiniLM for fast iteration, swap to bge-reranker-v2-m3 for production quality.

### ONNX export

For the lightweight path, export the MiniLM model to ONNX format to avoid the torch/sentence-transformers dependency:

```python
from optimum.onnxruntime import ORTModelForSequenceClassification
model = ORTModelForSequenceClassification.from_pretrained("cross-encoder/ms-marco-MiniLM-L-6-v2", export=True)
```
