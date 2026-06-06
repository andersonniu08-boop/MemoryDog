"""Instinct engine — TOML-based procedural modules."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

INSTINCTS_PATH = Path.home() / ".memorydog" / "instincts.toml"

DEFAULT_INSTINCTS = """\
# MemoryDog Instincts
# Define reusable behavioral modules that activate on keyword triggers.
# Active instincts bias memory retrieval and augment the system prompt.

[[instincts]]
name = "Bug Hunter"
description = "When fixing bugs, checks for similar issues and adds regression tests"
triggers = ["bug", "race condition", "deadlock", "fix", "debug", "crash"]
  prompt = "When fixing bugs, add a regression test and check similar code for the same pattern."
  retrieval_bias = ["bug", "fix", "debug", "test", "regression"]

[[instincts]]
name = "AI Evaluation Expert"
description = "Prioritizes benchmarks, metrics, and ablation studies"
triggers = ["benchmark", "evaluation", "metric", "ablation"]
  prompt = "Consider benchmarks, metrics, ablation studies, and statistical significance."
  retrieval_bias = ["benchmark", "evaluation", "metric", "ablation"]

[[instincts]]
name = "Recruiter Lens"
description = "Emphasizes resume value and portfolio impact when discussing projects"
triggers = ["resume", "recruiter", "interview", "portfolio", "hiring"]
  prompt = "Emphasize measurable impact and specific technologies when discussing projects."
  retrieval_bias = ["resume", "recruiting", "interview", "portfolio", "impact"]
"""


@dataclass
class Instinct:
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    prompt: str = ""
    retrieval_bias: list[str] = field(default_factory=list)


def ensure_instincts_file() -> Path:
    """Create default instincts.toml if it doesn't exist."""
    parent = INSTINCTS_PATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not INSTINCTS_PATH.exists():
        INSTINCTS_PATH.write_text(DEFAULT_INSTINCTS)
    return INSTINCTS_PATH


def load_instincts() -> list[Instinct]:
    path = ensure_instincts_file()
    data = tomllib.loads(path.read_text())
    results = []
    for raw in data.get("instincts", []):
        results.append(
            Instinct(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                triggers=raw.get("triggers", []),
                prompt=raw.get("prompt", ""),
                retrieval_bias=raw.get("retrieval_bias", []),
            )
        )
    return results


def match_instincts(
    instincts: list[Instinct], query: str, workspace: str
) -> list[tuple[Instinct, float]]:
    """Match and rank instincts against query + workspace. Returns (instinct, score)."""
    combined = f"{query} {workspace}".lower()
    scored: list[tuple[Instinct, float]] = []
    for inst in instincts:
        hits = sum(1 for t in inst.triggers if t.lower() in combined)
        if hits > 0:
            scored.append((inst, hits / len(inst.triggers)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:3]


def get_retrieval_bias(active: list[Instinct]) -> list[str]:
    """Collect retrieval bias terms from active instincts."""
    terms: list[str] = []
    for inst in active:
        terms.extend(inst.retrieval_bias)
    return list(dict.fromkeys(terms))


def get_instinct_prompts(active: list[Instinct]) -> str:
    """Build prompt injection block for active instincts."""
    if not active:
        return ""
    parts = []
    for inst in active:
        parts.append(f"[ACTIVE INSTINCT: {inst.name}] {inst.prompt}")
    return "\n".join(parts)
