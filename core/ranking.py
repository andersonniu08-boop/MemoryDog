"""Hybrid retrieval ranking formula."""
import math


def _safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def sanitize(score: float) -> float:
    if score is None:
        return 0.0
    return max(0.0, min(1.0, float(score)))


def score_memory(
    vector_score: float,
    bm25_score: float,
    days_since_access: float,
    importance: float,
    decay_factor: float,
    same_workspace: bool,
    access_count: int,
    mean_access_count: float = 5.0,
) -> float:
    vec = max(0.0, _safe_float(vector_score))
    bm = max(0.0, _safe_float(bm25_score))
    days = max(0.0, _safe_float(days_since_access))
    imp = max(0.0, min(1.0, _safe_float(importance, 0.5)))
    dec = max(0.0, min(1.0, _safe_float(decay_factor, 1.0)))
    same_ws = bool(same_workspace)
    acc = max(0, _safe_int(access_count))
    mean_acc = max(1.0, _safe_float(mean_access_count, 5.0))

    recency = math.exp(-0.01 * days)
    effective_importance = imp * dec
    workspace_boost = 1.5 if same_ws else 1.0
    frequency = _sigmoid(acc / mean_acc)

    return (
        0.35 * vec
        + 0.20 * bm
        + 0.15 * recency
        + 0.15 * effective_importance
        + 0.10 * workspace_boost
        + 0.05 * frequency
    )


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 1.0 if x > 0 else 0.0
